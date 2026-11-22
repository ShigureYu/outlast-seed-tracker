"""开发者测试库使用的密码修改历史表。"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

HISTORY_SUFFIX = "_HISTORY"
DEV_DB_NAME = "seed_full.db"


def is_dev_db(db_path: Path | str) -> bool:
    return Path(db_path).name.lower() == DEV_DB_NAME


def history_table_name(trial_id: str) -> str:
    return f"{trial_id}{HISTORY_SUFFIX}"


def is_history_table(table_name: str) -> bool:
    return table_name.endswith(HISTORY_SUFFIX)


def new_change_id() -> str:
    return uuid.uuid4().hex


def ensure_history_table(
    conn: sqlite3.Connection, trial_id: str, columns: list[str]
) -> str:
    """只建表，不迁移旧历史表；测试库重建时随新库一起重建。"""
    table = history_table_name(trial_id)
    definitions = ",\n            ".join(f'"{column}" TEXT' for column in columns)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            change_id      TEXT PRIMARY KEY,
            seed           INTEGER NOT NULL,
            {definitions},
            old_updated_at TEXT,
            changed_at     TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS "idx_{table}_seed" '
        f'ON "{table}" (seed, changed_at)'
    )
    return table


__all__ = [
    "DEV_DB_NAME",
    "HISTORY_SUFFIX",
    "ensure_history_table",
    "history_table_name",
    "is_dev_db",
    "is_history_table",
    "new_change_id",
]
