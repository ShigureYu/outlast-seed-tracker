"""发布前校验：数据库只能是空模板。"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

META_TABLE = "TRIAL_META"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/seed.db")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.is_file():
        raise SystemExit(f"数据库不存在: {path}")

    with sqlite3.connect(path) as conn:
        meta_count = conn.execute(f"SELECT COUNT(*) FROM {META_TABLE}").fetchone()[0]
        if meta_count <= 0:
            raise SystemExit(f"{META_TABLE} 为空")

        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != ? ORDER BY name",
                (META_TABLE,),
            )
        ]
        counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }

    print(f"TRIAL_META: {meta_count} 行")
    print(f"空表: {counts}")
    non_empty = {table: count for table, count in counts.items() if count != 0}
    if non_empty:
        raise SystemExit(f"发布模板包含答案行: {non_empty}")


if __name__ == "__main__":
    main()
