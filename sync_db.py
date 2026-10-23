"""测试库与主库一键同步。

默认方向：主库 -> 测试库（把测试库重置成主库的副本，实验前跑一次）。
反向：加 --to-main，把测试库的改动合并进主库（冲突默认保留主库，--apply-local 才覆盖）。

用法：
    python sync_db.py                 # 主库覆盖测试库
    python sync_db.py --to-main       # 测试库 → 主库（只合并差异）
    python sync_db.py --to-main --apply-local   # 冲突处用测试库的值
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import updater
from app_paths import DATA_DIR, DB_PATH, DEFAULT_DB_NAME, enable_utf8_stdout, load_config

logger = logging.getLogger(__name__)

MAIN_DB = DATA_DIR / DEFAULT_DB_NAME


def main() -> None:
    enable_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="测试库与主库同步")
    parser.add_argument("--main", default=str(MAIN_DB), help="主库路径（默认 data/seed.db）")
    parser.add_argument("--test", help="测试库路径（默认取 config.json 的 database）")
    parser.add_argument("--to-main", action="store_true", help="反向：把测试库合并进主库")
    parser.add_argument("--apply-local", action="store_true", help="反向合并时冲突采用测试库的值")
    args = parser.parse_args()

    main_path = Path(args.main)
    test_path = Path(args.test) if args.test else DATA_DIR / str(
        load_config().get("database") or DEFAULT_DB_NAME
    )

    if not main_path.is_file():
        raise SystemExit(f"主库不存在: {main_path}")
    if main_path.resolve() == test_path.resolve():
        raise SystemExit("主库和测试库是同一个文件，无需同步（检查 config.json 的 database）")
    if not test_path.is_file():
        raise SystemExit(f"测试库不存在: {test_path}")

    if not args.to_main:
        # 方向一：主库覆盖测试库，最常用，直接复制
        shutil.copy2(main_path, test_path)
        print(f"已用主库覆盖测试库\n  主库:   {main_path}\n  测试库: {test_path}")
        return

    # 方向二：测试库 → 主库，只合并差异，不整文件覆盖
    report = updater.merge(test_path, main_path)
    print(f"测试库: {test_path}\n主库:   {main_path}")
    print(f"新增 {report.inserted} 条，冲突 {len(report.conflicts)} 处，关卡配置更新 {report.meta_updated} 项")

    for conflict in report.conflicts[:20]:
        print(
            f"  冲突 {conflict.trial_id} seed={conflict.seed} {conflict.slot}: "
            f"主库={conflict.local or '—'} 测试库={conflict.remote or '—'}"
        )
    if len(report.conflicts) > 20:
        print(f"  ... 还有 {len(report.conflicts) - 20} 处")

    if report.conflicts and args.apply_local:
        changed = updater.apply_conflicts(
            main_path, report.conflicts, {c.key: True for c in report.conflicts}
        )
        print(f"已用测试库的值覆盖 {changed} 处")
    elif report.conflicts:
        print("冲突未处理，主库保留原值（要覆盖请加 --apply-local）")

    if report.inserted or report.meta_updated:
        print("主库已更新。记得 git add data/seed.db 并提交。")


if __name__ == "__main__":
    main()
