"""登记所有已支持关卡的 input_config（slots + choices）。

可重复执行；默认作用于 app_paths.DB_PATH（当前 config.json 指向 seed_local.db）。
只改传入的库的 input_config 列，不动 labels / code_length / choices / input_spec。

用法：
    python migrations/20261116_input_config.py
    python migrations/20261116_input_config.py --db data/seed.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_paths import DB_PATH, enable_utf8_stdout  # noqa: E402
from slot_config import InputConfig, SlotSpec  # noqa: E402

CHOICES = {
    "1": "兔子",
    "2": "梅花",
    "3": "王冠",
    "4": "方块",
    "5": "眼睛",
    "6": "火焰",
    "7": "红桃",
    "8": "月亮",
    "9": "黑桃",
}


def _number(column: str, name: str, length: int, low: int, high: int) -> SlotSpec:
    return SlotSpec(column=column, label=name, kind="number", length=length, min=low, max=high)


def _letter(column: str, name: str, low: str = "A", high: str = "O") -> SlotSpec:
    return SlotSpec(column=column, label=name, kind="letter", length=1, min=low, max=high)


def _pattern(column: str, name: str) -> SlotSpec:
    return SlotSpec(column=column, label=name, kind="pattern")


CONFIGS = {
    "CHJ_MT02": InputConfig(
        (
            _number("skull", "骷髅", 3, 0, 999),
            _number("spiral", "螺旋", 3, 0, 999),
            _number("triangle", "三角形", 3, 0, 999),
            _number("star", "五角星", 3, 0, 999),
            _number("sun", "太阳", 3, 0, 999),
        )
    ),
    "RES_MT01": InputConfig(
        (
            _number("killer", "凶手", 1, 0, 9),
            _number("weapon", "凶器", 1, 0, 9),
            _number("room", "房间", 1, 0, 9),
        )
    ),
    "MHS_MT01": InputConfig(
        (
            _number("triangle", "三角形", 3, 0, 999),
            _number("skull", "骷髅", 3, 0, 999),
            _number("sun", "太阳/五角星", 3, 0, 999),
        )
    ),
    "FPI_MT01": InputConfig(
        (
            _pattern("one", "图案1"),
            _pattern("two", "图案2"),
            _pattern("three", "图案3"),
            _pattern("four", "图案4"),
        ),
        CHOICES,
    ),
    "TSR_MT01": InputConfig(
        (
            _letter("Letter1", "字母1"),
            _letter("Letter2", "字母2"),
            _letter("Letter3", "字母3"),
            _number("Number1", "数字1", 2, 1, 25),
            _number("Number2", "数字2", 2, 1, 25),
            _number("Number3", "数字3", 2, 1, 25),
        )
    ),
}


def apply(db_path: Path) -> None:
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(db_path) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(TRIAL_META)")]
        if not cols:
            raise SystemExit(f"{db_path} 里没有 TRIAL_META")
        if "input_config" not in cols:
            conn.execute("ALTER TABLE TRIAL_META ADD COLUMN input_config TEXT")
            print("TRIAL_META: 已补 input_config 列")

        for trial_id, config in CONFIGS.items():
            cursor = conn.execute(
                "UPDATE TRIAL_META SET input_config=? WHERE trial_id=?",
                (config.to_json(), trial_id),
            )
            print(f"{trial_id}: slot {len(config.slots)} 个，更新 {cursor.rowcount} 行")

        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    print(f"数据库: {db_path}")
    print(f"integrity_check: {integrity}")


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="目标数据库（默认取 config.json）")
    args = parser.parse_args()
    apply(Path(args.db))


if __name__ == "__main__":
    main()
