"""从开发库生成可发布的空模板数据库。

用法：
    python tools/build_empty_db.py
    python tools/build_empty_db.py --source data/seed_full.db --target data/seed.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_paths import DATA_DIR, enable_utf8_stdout  # noqa: E402
from history import is_history_table  # noqa: E402

META_TABLE = "TRIAL_META"


def build_empty(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise SystemExit("source 和 target 不能是同一个文件")
    if not source.is_file():
        raise SystemExit(f"开发库不存在: {source}")

    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        tables = [
            row[0]
            for row in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != ? ORDER BY name",
                (META_TABLE,),
            )
        ]

        kept: list[str] = []
        for table in tables:
            if is_history_table(table):
                dst.execute(f'DROP TABLE "{table}"')
                continue
            dst.execute(f'DELETE FROM "{table}"')
            kept.append(table)

        dst.commit()
        dst.execute("VACUUM")
        meta_count = dst.execute(f"SELECT COUNT(*) FROM {META_TABLE}").fetchone()[0]

    print(f"空模板: {target}")
    print(f"TRIAL_META: {meta_count} 行")
    print(f"空表: {', '.join(kept)}")


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DATA_DIR / "seed_full.db"))
    parser.add_argument("--target", default=str(DATA_DIR / "seed.db"))
    args = parser.parse_args()
    build_empty(Path(args.source), Path(args.target))


if __name__ == "__main__":
    main()
