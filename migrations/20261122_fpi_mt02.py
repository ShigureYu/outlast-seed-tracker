"""创建 FPI_MT02 空表。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_paths import DB_PATH, enable_utf8_stdout  # noqa: E402
from fpi_mt02 import ensure_table  # noqa: E402


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    path = Path(args.db)
    if not path.is_file():
        raise SystemExit(f"数据库不存在: {path}")
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM FPI_MT02").fetchone()[0]
    print(f"FPI_MT02: {path}，当前 {total} 行")


if __name__ == "__main__":
    main()
