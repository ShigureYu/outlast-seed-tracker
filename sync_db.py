"""测试库与主库之间的两个动作，没有第三个。

    python sync_db.py reset     # 主库 -> 测试库：整文件覆盖，实验前重置用
    python sync_db.py merge     # 测试库 -> 主库：只并入新增的 seed

日常用两个批处理双击即可：重置测试库.cmd / 合并到主库.cmd
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import updater
from app_paths import DATA_DIR, DEFAULT_DB_NAME, enable_utf8_stdout, load_config

logger = logging.getLogger(__name__)

MAIN_DB = DATA_DIR / DEFAULT_DB_NAME


def _paths(args) -> tuple[Path, Path]:
    main_path = Path(args.main) if args.main else MAIN_DB
    test_path = Path(args.test) if args.test else DATA_DIR / str(
        load_config().get("database") or DEFAULT_DB_NAME
    )

    if not main_path.is_file():
        raise SystemExit(f"主库不存在: {main_path}")
    if main_path.resolve() == test_path.resolve():
        raise SystemExit(
            f"主库和测试库是同一个文件（{main_path.name}），请检查 data/config.json 的 database"
        )
    return main_path, test_path


def reset(main_path: Path, test_path: Path) -> None:
    """主库覆盖测试库：测试库里的东西全部丢弃。"""
    shutil.copy2(main_path, test_path)
    print("已用主库覆盖测试库（测试库原有内容已丢弃）")
    print(f"  主库:   {main_path}")
    print(f"  测试库: {test_path}")


def merge(main_path: Path, test_path: Path) -> None:
    """把测试库新增的 seed 并入主库；主库已有的 seed 一律不动。"""
    if not test_path.is_file():
        raise SystemExit(f"测试库不存在: {test_path}")

    report = updater.merge(test_path, main_path)

    print(f"主库:   {main_path}")
    print(f"测试库: {test_path}")
    print(f"新增 {report.inserted} 条；主库已有而值不同的 {len(report.conflicts)} 处（未改动）")

    for conflict in report.conflicts[:10]:
        print(
            f"  值不同 seed={conflict.seed} {conflict.slot}: "
            f"主库={conflict.local or '—'} 测试库={conflict.remote or '—'}"
        )
    if len(report.conflicts) > 10:
        print(f"  ... 还有 {len(report.conflicts) - 10} 处")

    if report.inserted:
        print("主库已更新，记得 git add data/seed.db 并提交。")
    else:
        print("主库没有需要并入的新数据。")


def main() -> None:
    enable_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="测试库 / 主库同步：reset 覆盖，merge 增量并入"
    )
    parser.add_argument("action", choices=("reset", "merge"), help="reset=主库覆盖测试库，merge=测试库增量并入主库")
    parser.add_argument("--main", help="主库路径（默认 data/seed.db）")
    parser.add_argument("--test", help="测试库路径（默认取 config.json 的 database）")
    args = parser.parse_args()

    main_path, test_path = _paths(args)
    if args.action == "reset":
        reset(main_path, test_path)
    else:
        merge(main_path, test_path)


if __name__ == "__main__":
    main()
