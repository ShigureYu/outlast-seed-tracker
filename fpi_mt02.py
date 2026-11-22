"""FPI_MT02 多轮数据访问与校验。"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app_paths import DB_PATH
from history import is_dev_db, new_change_id
from trial_info import Room, Target, TrialInfo, load_trial_info

logger = logging.getLogger(__name__)

TRIAL_ID = "FPI_MT02"
TURNS = (2, 3)
MAX_ENTRIES = 4
TABLE = "FPI_MT02"
HISTORY_TABLE = "FPI_MT02_HISTORY"


@dataclass(frozen=True)
class Entry:
    room: Room
    target: Target
    number: str

    def __post_init__(self) -> None:
        if len(self.number) != 1 or self.number not in "123456789":
            raise ValueError(f"数字必须是 1-9 的单字符: {self.number!r}")

    def to_dict(self) -> dict:
        return {"room": self.room.en, "pattern": self.target.name, "number": self.number}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise SystemExit(f"数据库不存在: {path}")
    return sqlite3.connect(path)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            seed       INTEGER NOT NULL,
            turn       INTEGER NOT NULL CHECK (turn IN (2, 3)),
            entries    TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (seed, turn)
        )
        """
    )


def ensure_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            change_id      TEXT PRIMARY KEY,
            seed           INTEGER NOT NULL,
            turn           INTEGER NOT NULL,
            entries        TEXT NOT NULL,
            old_updated_at TEXT,
            changed_at     TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS "idx_{HISTORY_TABLE}_seed" '
        f'ON "{HISTORY_TABLE}" (seed, turn, changed_at)'
    )


def _serialize(entries: tuple[Entry, ...]) -> str:
    return json.dumps(
        [entry.to_dict() for entry in entries],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_entries(info: TrialInfo, raw: str, seed: int, turn: int) -> tuple[Entry, ...]:
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"seed={seed} turn={turn} 的 entries 不是合法 JSON") from exc
    if not isinstance(items, list) or len(items) > MAX_ENTRIES:
        raise ValueError(f"seed={seed} turn={turn} 的 entries 不是最多 4 项的数组")

    entries: list[Entry] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"seed={seed} turn={turn} 的 entries 项不是对象")
        room = info.find_room(str(item.get("room") or ""))
        target = info.find_target(str(item.get("pattern") or ""))
        if room is None:
            raise ValueError(f"未知房间: {item.get('room')!r}")
        if target is None:
            raise ValueError(f"未知图案: {item.get('pattern')!r}")
        entries.append(Entry(room=room, target=target, number=str(item.get("number") or "")))
    return tuple(entries)


def read_turns(
    seed: int | str, db_path: Path | str = DB_PATH
) -> dict[int, tuple[Entry, ...]]:
    info = load_trial_info(TRIAL_ID)
    seed_value = int(str(seed).strip())
    result: dict[int, tuple[Entry, ...]] = {}

    with closing(_connect(db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        if not exists:
            return result
        rows = conn.execute(
            f"SELECT turn, entries FROM {TABLE} WHERE seed=?", (seed_value,)
        ).fetchall()

    for turn, raw in rows:
        if turn not in TURNS:
            logger.warning("FPI_MT02 忽略非法 turn=%s seed=%s", turn, seed_value)
            continue
        result[int(turn)] = _parse_entries(info, raw, seed_value, int(turn))
    return result


def _validate(
    info: TrialInfo, entries_by_turn: dict[int, tuple[Entry, ...]]
) -> dict[int, tuple[Entry, ...]]:
    normalized: dict[int, tuple[Entry, ...]] = {}
    for turn in TURNS:
        entries = tuple(entries_by_turn.get(turn, ()))
        if len(entries) > MAX_ENTRIES:
            raise ValueError(f"第 {turn} 轮最多 {MAX_ENTRIES} 条")
        rooms: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Entry):
                raise ValueError("FPI_MT02 只接受 Entry 对象")
            if info.find_room(entry.room.en) is None:
                raise ValueError(f"未知房间: {entry.room.en}")
            if info.find_target(entry.target.name) is None:
                raise ValueError(f"未知图案: {entry.target.name}")
            if entry.room.en in rooms:
                raise ValueError(f"第 {turn} 轮房间重复: {entry.room.en}")
            rooms.add(entry.room.en)
        normalized[turn] = entries
    return normalized


def save_turns(
    seed: int | str,
    entries_by_turn: dict[int, tuple[Entry, ...]],
    db_path: Path | str = DB_PATH,
) -> None:
    info = load_trial_info(TRIAL_ID)
    seed_value = int(str(seed).strip())
    normalized = _validate(info, entries_by_turn)
    now = _now()
    record_history = is_dev_db(db_path)

    with closing(_connect(db_path)) as conn:
        ensure_table(conn)
        if record_history:
            ensure_history_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for turn in TURNS:
                entries = normalized[turn]
                encoded = _serialize(entries)
                old = conn.execute(
                    f"SELECT entries, updated_at FROM {TABLE} WHERE seed=? AND turn=?",
                    (seed_value, turn),
                ).fetchone()

                if old is not None:
                    try:
                        if _parse_entries(info, old[0], seed_value, turn) == entries:
                            continue
                    except ValueError:
                        # 旧数据损坏时按“有变化”处理，历史仍保留原始文本
                        pass

                if old is not None:
                    if record_history:
                        conn.execute(
                            f"INSERT INTO {HISTORY_TABLE} "
                            f"(change_id, seed, turn, entries, old_updated_at, changed_at) "
                            f"VALUES (?, ?, ?, ?, ?, ?)",
                            (new_change_id(), seed_value, turn, old[0], old[1], now),
                        )
                    if not entries:
                        conn.execute(
                            f"DELETE FROM {TABLE} WHERE seed=? AND turn=?",
                            (seed_value, turn),
                        )
                        continue

                if not entries:
                    continue

                conn.execute(
                    f"INSERT INTO {TABLE} (seed, turn, entries, updated_at) "
                    f"VALUES (?, ?, ?, ?) "
                    f"ON CONFLICT(seed, turn) DO UPDATE SET "
                    f"entries=excluded.entries, updated_at=excluded.updated_at",
                    (seed_value, turn, encoded, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


__all__ = [
    "Entry",
    "HISTORY_TABLE",
    "MAX_ENTRIES",
    "TABLE",
    "TRIAL_ID",
    "TURNS",
    "ensure_history_table",
    "ensure_table",
    "read_turns",
    "save_turns",
]
