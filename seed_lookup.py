"""打通「日志 Trial ID + seed → sqlite 查询 → 返回图案密码」这条链。

Trial ID 默认从日志里取（`read_current_state()`），所以换关不用改代码；
`--trial` 只是手动覆盖，方便排查。

用法：
    python seed_lookup.py               # Trial 和 seed 都从最新日志取
    python seed_lookup.py --seed 815    # 只手动指定 seed
    python seed_lookup.py --trial CHJ_MT02 --seed 815
    python seed_lookup.py --json        # 输出机器可读的 JSON

也可以当库用：
    from seed_lookup import lookup_from_log
    trial, seed, codes = lookup_from_log()   # ('CHJ_MT02', '815', {'skull': '478', ...})
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from log_parser import normalize_trial_id, read_current_seed, read_current_state, read_current_trial_id

logger = logging.getLogger(__name__)

from app_paths import DB_PATH, enable_utf8_stdout, ensure_database

# 表名会拼进 SQL，所以必须白名单：只允许 CHJ_MT02 这种形态
TABLE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    if not Path(db_path).is_file():
        raise SystemExit(f"数据库不存在: {db_path}（先跑 seed_importer.py）")
    return sqlite3.connect(db_path)


def _check_trial(conn: sqlite3.Connection, trial_id: str) -> None:
    """校验表名合法且确实存在，避免把任意字符串拼进 SQL。"""
    if not TABLE_RE.match(trial_id):
        raise ValueError(f"Trial ID 不合法: {trial_id!r}")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (trial_id,)
    ).fetchone()
    if not exists:
        raise ValueError(f"库里没有 Trial 表: {trial_id}")


def get_symbols(conn: sqlite3.Connection, trial_id: str) -> list[str]:
    """从表结构里读出图案列（除 seed 之外的所有列）。

    以后加新关、图案数量变了，这里不用改。
    """
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({trial_id})")]
    # seed 是主键，updated_at 是审计字段，都不是图案
    return [c for c in columns if c not in ("seed", "updated_at")]


def get_trial_meta(conn: sqlite3.Connection, trial_id: str) -> dict:
    """读关卡元信息。labels 是 JSON 串，调用方自己解析。"""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info(TRIAL_META)")]
    wanted = [c for c in ("trial_id", "name_zh", "map_name", "labels") if c in columns]
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM TRIAL_META WHERE trial_id=?",
        (trial_id,),
    ).fetchone()

    meta: dict = dict.fromkeys(wanted)
    meta.update(trial_id=trial_id)
    if row is not None:
        meta.update(dict(zip(wanted, row)))
    meta.setdefault("name_zh", None)
    meta.setdefault("map_name", None)
    meta.setdefault("labels", None)
    return meta


def resolve_trial_id(trial_id: str | None = None) -> str | None:
    """确定要查哪一关：显式传入优先，否则去日志里读当前关。"""
    if trial_id:
        return normalize_trial_id(trial_id)
    return read_current_trial_id()


def _query_codes(
    conn: sqlite3.Connection, trial_id: str, seed_value: int
) -> dict[str, str | None] | None:
    """真正的查表动作：给一条已校验过的表名和 seed，返回一行。"""
    symbols = get_symbols(conn, trial_id)
    row = conn.execute(
        f"SELECT {', '.join(symbols)} FROM {trial_id} WHERE seed=?",
        (seed_value,),
    ).fetchone()
    if row is None:
        logger.info("表 %s 里没有 seed=%s", trial_id, seed_value)
        return None
    return dict(zip(symbols, row))


def lookup(
    seed: int | str,
    trial_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, str | None] | None:
    """按 seed 查一关的密码；表里没这个 seed 就返回 None。

    trial_id 不给就去日志里读当前关卡。
    返回的字典按键是图案列名，值为 3 位密码串；源表缺格的那种是 None。
    """
    trial = resolve_trial_id(trial_id)
    if trial is None:
        raise ValueError("没能确定 Trial ID：日志里没读到，也没显式传入")

    seed_value = int(str(seed).strip())
    with closing(_connect(db_path)) as conn:
        _check_trial(conn, trial)
        return _query_codes(conn, trial, seed_value)


def lookup_from_log(
    trial_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> tuple[str | None, str | None, dict[str, str | None] | None]:
    """从游戏日志拿当前的 Trial ID 和 seed 再查表。

    返回 (trial_id, seed, 密码字典)。显式给 trial_id 时只从日志取 seed。
    """
    if trial_id:
        trial, seed = normalize_trial_id(trial_id), read_current_seed()
    else:
        trial, seed = read_current_state()

    if seed is None:
        return trial, None, None
    return trial, seed, lookup(seed, trial_id=trial, db_path=db_path)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _now() -> str:
    """本地时间的 ISO 串，精度到秒，足够做人读的时间戳。"""
    return datetime.now().isoformat(timespec="seconds")


def _parse_labels(raw: str | None) -> dict[str, str]:
    """解析 TRIAL_META.labels 里的 JSON；坏数据按"没有标签"处理。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("TRIAL_META.labels 不是合法 JSON: %r", raw)
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def describe_trial(
    trial_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict | None:
    """返回一关的展示信息；库里没有这张表就返回 None（对应"无表状态"）。

    形如：
        {'trial_id': 'CHJ_MT02', 'name_zh': '被告判决',
         'map_name': 'CourthouseJudicial',
         'slots': [('skull', '骷髅'), ('spiral', '螺旋'), ...]}
    """
    trial = resolve_trial_id(trial_id)
    if trial is None:
        return None

    with closing(_connect(db_path)) as conn:
        try:
            _check_trial(conn, trial)
        except ValueError:
            return None
        symbols = get_symbols(conn, trial)
        meta = get_trial_meta(conn, trial)

    labels = _parse_labels(meta.get("labels"))
    return {
        "trial_id": trial,
        "name_zh": meta["name_zh"],
        "map_name": meta["map_name"],
        # 没有映射就直接显示列名（英文），不为某一关写死兜底
        "slots": [(name, labels.get(name) or name) for name in symbols],
    }


def read_codes(
    seed: int | str,
    trial_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, str | None] | None:
    """读一关的密码；没有这张表、或没有这条记录，都返回 None。

    和 lookup() 的区别：这个不抛异常，专门给 UI 判断"该渲染哪种状态"用。
    """
    trial = resolve_trial_id(trial_id)
    if trial is None:
        return None

    with closing(_connect(db_path)) as conn:
        try:
            _check_trial(conn, trial)
        except ValueError:
            return None
        return _query_codes(conn, trial, int(str(seed).strip()))


def save_codes(
    seed: int | str,
    codes: dict[str, str | None],
    trial_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """把一组密码写进对应关的表（INSERT OR REPLACE）。

    空字符串、None 都存成 NULL——"这个图案没看到"和"密码是空"不是一回事，
    和 seed_importer.py 的约定保持一致。
    """
    trial = resolve_trial_id(trial_id)
    if trial is None:
        raise ValueError("没能确定 Trial ID，无法保存")

    seed_value = int(str(seed).strip())

    with closing(_connect(db_path)) as conn:
        _check_trial(conn, trial)
        columns = get_symbols(conn, trial)

        unknown = set(codes) - set(columns)
        if unknown:
            raise ValueError(f"表 {trial} 里没有这些列: {sorted(unknown)}")

        values: list[str | None] = []
        for name in columns:
            raw = codes.get(name)
            text = str(raw).strip() if raw is not None else ""
            values.append(text or None)

        has_time = "updated_at" in _table_columns(conn, trial)
        names = ["seed", *columns] + (["updated_at"] if has_time else [])
        marks = ", ".join("?" * len(names))
        params = [seed_value, *values] + ([_now()] if has_time else [])
        conn.execute(
            f"INSERT OR REPLACE INTO {trial} ({', '.join(names)}) VALUES ({marks})",
            params,
        )
        conn.commit()


def _print_result(
    trial_id: str,
    seed: str | int,
    codes: dict[str, str | None] | None,
    meta: dict[str, str | None],
) -> None:
    if codes is None:
        print(f"{trial_id} / Seed {seed}: 表里没有记录")
        return

    print(f"试炼: {meta['trial_id']}  {meta['name_zh'] or ''}  ({meta['map_name'] or ''})")
    print(f"Seed: {seed}")
    labels = _parse_labels(meta.get("labels"))
    width = max(len(name) for name in codes)
    for key, value in codes.items():
        label = labels.get(key) or key
        shown = value if value is not None else "（源表缺记录）"
        print(f"  {key:<{width}}  {label:<5}  {shown}")


def main() -> None:
    parser = argparse.ArgumentParser(description="日志 Trial ID + seed -> sqlite -> 图案密码")
    parser.add_argument("--seed", help="手动指定 seed（默认从最新日志取）")
    parser.add_argument("--trial", help="手动指定 Trial ID（默认从最新日志取）")
    parser.add_argument("--db", default=str(DB_PATH), help="sqlite 路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    enable_utf8_stdout()
    ensure_database()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)

    # 只要有一项是手动给的，就仍需读一次日志补齐另一项
    if args.seed and args.trial:
        trial_id, seed = normalize_trial_id(args.trial), args.seed
    else:
        log_trial, log_seed = read_current_state()
        trial_id = normalize_trial_id(args.trial) if args.trial else log_trial
        seed = args.seed or log_seed

    if not trial_id:
        raise SystemExit("没有 Trial ID：日志里没读到，也没用 --trial 指定")
    if not seed:
        raise SystemExit("没有可用的 seed：日志里没找到，也没用 --seed 指定")

    with closing(_connect(args.db)) as conn:
        try:
            _check_trial(conn, trial_id)
        except ValueError as exc:
            raise SystemExit(f"{exc}（重新跑 seed_importer.py 导入该关）") from None
        codes = _query_codes(conn, trial_id, int(str(seed).strip()))
        meta = get_trial_meta(conn, trial_id)

    if args.json:
        print(json.dumps(
            {"seed": seed, "trial": meta, "codes": codes},
            ensure_ascii=False,
            indent=2,
        ))
        return

    _print_result(trial_id, seed, codes, meta)


if __name__ == "__main__":
    main()
