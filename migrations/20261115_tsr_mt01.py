"""登记 TSR_MT01「取消广播」：建表、补 input_spec，配置 3 字母 + 3 数字的输入规则。

可重复执行；默认作用于 app_paths.DB_PATH（当前 config.json 指向 seed_local.db）。
只改传入的库，不会主动碰 data/seed.db。

规则：
- 第 1～3 位：单个字母 A-O，保存时转大写；
- 第 4～6 位：数字 01-25，必须存两位（输入 1 位会补零）。

用法：
    python migrations/20261115_tsr_mt01.py
    python migrations/20261115_tsr_mt01.py --db data/seed.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_paths import DB_PATH, enable_utf8_stdout  # noqa: E402

TRIAL_ID = "TSR_MT01"
NAME_ZH = "取消广播"
MAP_NAME = "TelevisionStudioRooftop"
COLUMNS = (
    ("Letter1", "字母1"),
    ("Letter2", "字母2"),
    ("Letter3", "字母3"),
    ("Number1", "数字1"),
    ("Number2", "数字2"),
    ("Number3", "数字3"),
)
INPUT_SPEC = {
    "Letter1": {"kind": "letter", "length": 1, "charset": "A-O"},
    "Letter2": {"kind": "letter", "length": 1, "charset": "A-O"},
    "Letter3": {"kind": "letter", "length": 1, "charset": "A-O"},
    "Number1": {"kind": "number", "length": 2, "min": 1, "max": 25},
    "Number2": {"kind": "number", "length": 2, "min": 1, "max": 25},
    "Number3": {"kind": "number", "length": 2, "min": 1, "max": 25},
}
# 早期版本用 one..six 建过表；迁移时改成语义列名
LEGACY_COLUMNS = {
    "one": "Letter1",
    "two": "Letter2",
    "three": "Letter3",
    "four": "Number1",
    "five": "Number2",
    "six": "Number3",
}


def apply(db_path: Path) -> None:
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(db_path) as conn:
        meta_cols = [row[1] for row in conn.execute("PRAGMA table_info(TRIAL_META)")]
        if not meta_cols:
            raise SystemExit(f"{db_path} 里没有 TRIAL_META")
        if "input_spec" not in meta_cols:
            conn.execute("ALTER TABLE TRIAL_META ADD COLUMN input_spec TEXT")
            print("TRIAL_META: 已补 input_spec 列")

        column_defs = ",\n            ".join(f"{name} TEXT" for name, _ in COLUMNS)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TRIAL_ID} (
                seed INTEGER PRIMARY KEY NOT NULL,
                {column_defs},
                updated_at TEXT
            )
            """
        )
        if "updated_at" not in [
            row[1] for row in conn.execute(f"PRAGMA table_info({TRIAL_ID})")
        ]:
            conn.execute(f"ALTER TABLE {TRIAL_ID} ADD COLUMN updated_at TEXT")

        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TRIAL_ID})")}
        for old, new in LEGACY_COLUMNS.items():
            if old in existing and new not in existing:
                conn.execute(
                    f'ALTER TABLE {TRIAL_ID} RENAME COLUMN "{old}" TO "{new}"'
                )
                existing.discard(old)
                existing.add(new)
                print(f"{TRIAL_ID}: 列 {old} -> {new}")

        conn.execute(
            "INSERT OR REPLACE INTO TRIAL_META "
            "(trial_id, name_zh, map_name, labels, code_length, choices, input_spec) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                TRIAL_ID,
                NAME_ZH,
                MAP_NAME,
                json.dumps(dict(COLUMNS), ensure_ascii=False),
                0,
                None,
                json.dumps(INPUT_SPEC, ensure_ascii=False),
            ),
        )
        conn.commit()

        total = conn.execute(f"SELECT COUNT(*) FROM {TRIAL_ID}").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    print(f"数据库: {db_path}")
    print(f"{TRIAL_ID}: 表内 {total} 行")
    print(f"integrity_check: {integrity}")


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="目标数据库（默认取 config.json）")
    args = parser.parse_args()
    apply(Path(args.db))


if __name__ == "__main__":
    main()
