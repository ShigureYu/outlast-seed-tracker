"""从 GitHub 拉取最新答案库并合并到本地，不依赖 Qt。

设计要点：
- 只做"用远端补本地"：本地没有的插入，值相同跳过，值不同记为冲突交给用户决定，
  绝不静默覆盖用户自己验证过的记录。
- 先下载到临时文件、校验能打开后再合并，避免半截文件把本地库写坏。
- 合并只针对具体的表/行，不做整库替换。
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REMOTE_DB_URL = (
    "https://raw.githubusercontent.com/ShigureYu/outlast-seed-tracker/master/data/seed.db"
)
TIMEOUT = 10
META_TABLE = "TRIAL_META"


@dataclass
class Conflict:
    trial_id: str
    seed: int
    slot: str
    local: str | None
    remote: str | None

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.trial_id, self.seed, self.slot)


@dataclass
class MergeReport:
    inserted: int = 0
    skipped: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    new_trials: list[str] = field(default_factory=list)
    refused: bool = False
    reason: str = ""
    meta_updated: int = 0


def _state_path(db_path: Path) -> Path:
    return db_path.with_name("update_state.json")


def _read_state(db_path: Path) -> dict:
    try:
        return json.loads(_state_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(db_path: Path, state: dict) -> None:
    try:
        _state_path(db_path).write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        logger.warning("写入更新状态失败: %s", exc)


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows if r[0] != META_TABLE]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def fetch_remote(db_path: Path, url: str = REMOTE_DB_URL) -> Path | None:
    """下载远端库到临时文件；远端没变化（304）时返回 None。"""
    state = _read_state(db_path)
    headers = {"User-Agent": "OPPOverlay"}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read()
            etag = response.headers.get("ETag") or hashlib.sha256(payload).hexdigest()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            logger.info("远端数据库未变化")
            return None
        logger.warning("下载失败 HTTP %s", exc.code)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.info("检查更新失败（忽略）: %s", exc)
        return None

    tmp = db_path.with_suffix(".remote")
    try:
        tmp.write_bytes(payload)
        with closing(sqlite3.connect(tmp)) as probe:  # 能打开才认可
            probe.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except (OSError, sqlite3.DatabaseError) as exc:
        logger.warning("远端库不可用: %s", exc)
        tmp.unlink(missing_ok=True)
        return None

    state["etag"] = etag
    _write_state(db_path, state)
    return tmp


def merge(remote_path: Path, local_path: Path) -> MergeReport:
    """把远端库合并进本地库，返回统计与冲突清单；冲突行保持本地原值。"""
    report = MergeReport()

    with closing(sqlite3.connect(remote_path)) as remote, closing(
        sqlite3.connect(local_path)
    ) as local:
        remote.row_factory = sqlite3.Row
        local.row_factory = sqlite3.Row

        # 安全阀：远端表明显比本地小，通常是远端被截断/误删，不能合并
        for table in _tables(remote):
            if not local.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            remote_count = remote.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            local_count = local.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if local_count > 20 and remote_count < local_count * 0.5:
                report.refused = True
                report.reason = (
                    f"{table} 远端只有 {remote_count} 行，本地有 {local_count} 行"
                )
                logger.warning("拒绝合并：%s", report.reason)
                return report

        # 1) 关卡元信息：缺的补上
        remote_meta = remote.execute(f"SELECT * FROM {META_TABLE}").fetchall()
        for row in remote_meta:
            exists = local.execute(
                f"SELECT 1 FROM {META_TABLE} WHERE trial_id=?", (row["trial_id"],)
            ).fetchone()
            keys = list(row.keys())
            if not exists:
                local.execute(
                    f"INSERT INTO {META_TABLE} ({', '.join(keys)}) "
                    f"VALUES ({', '.join('?' * len(keys))})",
                    tuple(row[k] for k in keys),
                )
                report.new_trials.append(row["trial_id"])
                continue

            # 已存在的关卡：meta 以远端为准（关卡名、labels 顺序都算配置）
            fields = [k for k in keys if k != "trial_id"]
            current = local.execute(
                f"SELECT {', '.join(fields)} FROM {META_TABLE} WHERE trial_id=?",
                (row["trial_id"],),
            ).fetchone()
            changed = [
                (name, current_value, row[name])
                for name, current_value in zip(fields, current)
                if current_value != row[name]
            ]
            if changed:
                assignments = ", ".join(f"{name}=?" for name, _, _ in changed)
                local.execute(
                    f"UPDATE {META_TABLE} SET {assignments} WHERE trial_id=?",
                    [new for _, _, new in changed] + [row["trial_id"]],
                )
                report.meta_updated += len(changed)
                for name, old_value, new_value in changed:
                    logger.info(
                        "关卡配置更新 %s.%s: %r -> %r",
                        row["trial_id"], name, old_value, new_value,
                    )

        # 2) 每张答案表逐行比对
        for table in _tables(remote):
            columns = _columns(remote, table)
            if not columns or "seed" not in columns:
                continue

            if not local.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                create = remote.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()[0]
                local.execute(create)
                report.new_trials.append(table)

            local_columns = _columns(local, table)
            missing = set(columns) - set(local_columns)
            if missing == {"updated_at"}:
                # 老库没有审计列：补上就行，没必要因此拒绝整张表
                local.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
                local_columns = _columns(local, table)
                missing = set()
            if missing:
                logger.warning("本地表 %s 缺少列 %s，跳过", table, sorted(missing))
                continue

            # updated_at 只做审计，不参与值比对，否则每行都会报冲突
            palette = [c for c in columns if c not in ("seed", "updated_at")]
            for row in remote.execute(f"SELECT {', '.join(columns)} FROM {table}"):
                seed = row["seed"]
                current = local.execute(
                    f"SELECT {', '.join(palette)} FROM {table} WHERE seed=?", (seed,)
                ).fetchone()

                if current is None:
                    local.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' * len(columns))})",
                        tuple(row[c] for c in columns),
                    )
                    report.inserted += 1
                    continue

                for slot in palette:
                    remote_value = row[slot]
                    local_value = current[slot]
                    if remote_value == local_value:
                        report.skipped += 1
                    else:
                        report.conflicts.append(
                            Conflict(table, seed, slot, local_value, remote_value)
                        )

        local.commit()

    return report


def apply_conflicts(
    local_path: Path,
    conflicts: list[Conflict],
    use_remote: dict[tuple[str, int, str], bool],
) -> int:
    """按用户选择写入冲突项；返回实际改动的行数。"""
    changed = 0
    with closing(sqlite3.connect(local_path)) as local:
        for conflict in conflicts:
            if not use_remote.get(conflict.key):
                continue
            has_time = "updated_at" in [
                row[1] for row in local.execute(f"PRAGMA table_info({conflict.trial_id})")
            ]
            if has_time:
                local.execute(
                    f"UPDATE {conflict.trial_id} SET {conflict.slot}=?, updated_at=? WHERE seed=?",
                    (conflict.remote, datetime.now().isoformat(timespec="seconds"), conflict.seed),
                )
            else:
                local.execute(
                    f"UPDATE {conflict.trial_id} SET {conflict.slot}=? WHERE seed=?",
                    (conflict.remote, conflict.seed),
                )
            changed += 1
        local.commit()
    return changed
