"""把 data/seed.xlsx 里的答案表导入 sqlite。

当前只处理「被告判决」这一张表（Trial ID CHJ_MT02），先把链路跑通；
其余三张结构不一样，等这版验证完再各自加导入分支。

约定：
- 表名一律大写下划线；Trial 的答案表直接叫 Trial ID（CHJ_MT02）。
- 码值一律 TEXT，保住 '014' 这种前导零。
- 源表有缺格/脏格的，那一格存 NULL 而不是整行丢掉：
  机器一次只显示一个图案，缺一个图案不影响另外四个可用。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import openpyxl

logger = logging.getLogger(__name__)

from app_paths import DATA_DIR, XLSX_PATH, enable_utf8_stdout

DB_PATH = DATA_DIR / "seed.db"  # 正式库，固定名字，故意不跟随 config.json

# --- 当前硬编码的一关：被告判决 / 法院 ---
SHEET_NAME = "被告判决"
TRIAL_ID = "CHJ_MT02"
NAME_ZH = "被告判决"
MAP_NAME = "CourthouseJudicial"

META_TABLE = "TRIAL_META"

# xlsx 第 2 列起依次是这些图案（列名 -> 表格里的表头，仅用于日志提示）
SYMBOLS: dict[str, str] = {
    "skull": "骷髅☠️",
    "spiral": "螺旋🌀",
    "triangle": "三角形△",
    "star": "五角星☆",
    "sun": "太阳🌞",
}

CODE_RE = re.compile(r"^\d{3}$")
# 表头里的图案名带 emoji（骷髅☠️），入库的显示名只要中文
NON_CJK_RE = re.compile(r"[^\u4e00-\u9fff]")


def _clean_label(text: str) -> str:
    """'骷髅☠️' -> '骷髅'：去掉 emoji 和符号，只留中文。"""
    return NON_CJK_RE.sub("", text) or text
TRIAL_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


def _normalize_code(raw: object) -> str | None:
    """单元格值 -> 3 位数字串；缺失或不是合法 3 位码就返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    return text if CODE_RE.match(text) else None


def _normalize_seed(raw: object) -> int | None:
    """单元格值 -> seed 整数；非数字或超范围返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():
        return None
    seed = int(text)
    return seed if 1 <= seed <= 999 else None


def create_schema(conn: sqlite3.Connection) -> None:
    """建元信息表和当前 Trial 的答案表（幂等）。"""
    if not TRIAL_ID_RE.match(TRIAL_ID):
        raise ValueError(f"Trial ID 不合法: {TRIAL_ID}")

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {META_TABLE} (
            trial_id  TEXT PRIMARY KEY,
            name_zh   TEXT NOT NULL,
            map_name  TEXT,
            labels    TEXT
        )
        """
    )
    # 老库可能没有 labels 列，补上
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({META_TABLE})")]
    if "labels" not in columns:
        conn.execute(f"ALTER TABLE {META_TABLE} ADD COLUMN labels TEXT")

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TRIAL_ID} (
            seed     INTEGER PRIMARY KEY,
            skull    TEXT,
            spiral   TEXT,
            triangle TEXT,
            star     TEXT,
            sun      TEXT,
            updated_at TEXT
        )
        """
    )


def ensure_columns(conn: sqlite3.Connection) -> None:
    """老库没有 updated_at 列时补上（SQLite 支持 ADD COLUMN，无需重建）。"""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({TRIAL_ID})")]
    if "updated_at" not in columns:
        conn.execute(f"ALTER TABLE {TRIAL_ID} ADD COLUMN updated_at TEXT")
        logger.info("已为 %s 添加 updated_at 列", TRIAL_ID)


def load_rows() -> tuple[list[tuple], list[tuple[int, int, str]]]:
    """读 sheet，返回 (待入库行, 含空格子的行及原因)。"""
    workbook = openpyxl.load_workbook(XLSX_PATH, data_only=True, read_only=True)
    sheet = workbook[SHEET_NAME]

    rows: list[tuple] = []
    gaps: list[tuple[int, int, str]] = []

    for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        if index == 1:  # 表头
            logger.info("表头: %s", [c for c in row if c is not None])
            continue
        if row is None or row[0] is None:
            continue

        seed = _normalize_seed(row[0])
        if seed is None:
            logger.warning("第 %d 行 seed 非法，跳过: %r", index, row[0])
            continue

        codes = [_normalize_code(v) for v in row[1 : 1 + len(SYMBOLS)]]
        missing = [
            f"{SYMBOLS[name]}={value!r}"
            for name, value, code in zip(SYMBOLS, row[1 : 1 + len(SYMBOLS)], codes)
            if code is None
        ]
        if missing:
            gaps.append((index, seed, ", ".join(missing)))

        rows.append((seed, *codes))

    workbook.close()
    return rows, gaps


def import_all() -> None:
    if not XLSX_PATH.is_file():
        raise SystemExit(f"找不到表格: {XLSX_PATH}")

    rows, gaps = load_rows()

    seen: dict[int, int] = {}
    for seed, *_ in rows:
        seen[seed] = seen.get(seed, 0) + 1
    duplicated = [s for s, n in seen.items() if n > 1]
    if duplicated:
        logger.warning("有 %d 个 seed 重复: %s", len(duplicated), duplicated[:10])

    with closing(sqlite3.connect(DB_PATH)) as conn:
        create_schema(conn)
        ensure_columns(conn)
        conn.execute(
            f"INSERT OR REPLACE INTO {META_TABLE} (trial_id, name_zh, map_name, labels) "
            f"VALUES (?, ?, ?, ?)",
            (TRIAL_ID, NAME_ZH, MAP_NAME, json.dumps({k: _clean_label(v) for k, v in SYMBOLS.items()}, ensure_ascii=False)),
        )
        conn.executemany(
            f"INSERT OR REPLACE INTO {TRIAL_ID} (seed, {', '.join(SYMBOLS)}) "
            f"VALUES ({', '.join('?' * (len(SYMBOLS) + 1))})",
            rows,
        )
        conn.commit()

        total = conn.execute(f"SELECT COUNT(*) FROM {TRIAL_ID}").fetchone()[0]
        incomplete = conn.execute(
            f"SELECT COUNT(*) FROM {TRIAL_ID} WHERE "
            + " OR ".join(f"{c} IS NULL" for c in SYMBOLS)
        ).fetchone()[0]

    print(f"数据库: {DB_PATH}")
    print(f"{TRIAL_ID} 入库 {len(rows)} 行，其中 {incomplete} 行有空格子，表内共 {total} 行")
    for index, seed, reason in gaps:
        print(f"  第 {index} 行 seed={seed} 存为 NULL: {reason}")


def main() -> None:
    enable_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    import_all()


if __name__ == "__main__":
    main()
