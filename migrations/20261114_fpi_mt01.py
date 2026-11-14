"""登记 FPI_MT01「蛊惑孩童」：建表、写入 seed 136 的样例数据、补 TRIAL_META。

可重复执行；默认作用于 app_paths.DB_PATH（当前 config.json 指向 seed_local.db）。
只改传入的库，不会主动碰 data/seed.db。

用法：
    python migrations/20261114_fpi_mt01.py
    python migrations/20261114_fpi_mt01.py --db data/seed.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_paths import DB_PATH, enable_utf8_stdout  # noqa: E402

TRIAL_ID = "FPI_MT01"
NAME_ZH = "蛊惑孩童"
MAP_NAME = "FunParkIllusion"
CODE_LENGTH = 1
SEED = 136
ROW = ("1", "6", "8", "3")
COLUMNS = (
    ("one", "第1位"),
    ("two", "第2位"),
    ("three", "第3位"),
    ("four", "第4位"),
)
CHOICES = {
    "1": "兔子",
    "2": "梅花",
    "3": "皇冠",
    "4": "方块",
    "5": "眼睛",
    "6": "火焰",
    "7": "爱心",
    "8": "月亮",
    "9": "黑桃",
}


def apply(db_path: Path) -> None:
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(db_path) as conn:
        meta_cols = [row[1] for row in conn.execute("PRAGMA table_info(TRIAL_META)")]
        if not meta_cols:
            raise SystemExit(f"{db_path} 里没有 TRIAL_META")
        if "choices" not in meta_cols:
            conn.execute("ALTER TABLE TRIAL_META ADD COLUMN choices TEXT")
            print("TRIAL_META: 已补 choices 列")

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

        conn.execute(
            f"INSERT OR REPLACE INTO {TRIAL_ID} "
            f"(seed, {', '.join(name for name, _ in COLUMNS)}) "
            f"VALUES (?, ?, ?, ?, ?)",
            (SEED, *ROW),
        )
        conn.execute(
            "INSERT OR REPLACE INTO TRIAL_META "
            "(trial_id, name_zh, map_name, labels, code_length, choices) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                TRIAL_ID,
                NAME_ZH,
                MAP_NAME,
                json.dumps(dict(COLUMNS), ensure_ascii=False),
                CODE_LENGTH,
                json.dumps(CHOICES, ensure_ascii=False),
            ),
        )
        conn.commit()

        total = conn.execute(f"SELECT COUNT(*) FROM {TRIAL_ID}").fetchone()[0]
        row = conn.execute(f"SELECT * FROM {TRIAL_ID} WHERE seed=?", (SEED,)).fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    print(f"数据库: {db_path}")
    print(f"{TRIAL_ID}: 表内 {total} 行；seed {SEED} = {row}")
    print(f"integrity_check: {integrity}")


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="目标数据库（默认取 config.json）")
    args = parser.parse_args()
    apply(Path(args.db))


if __name__ == "__main__":
    main()
