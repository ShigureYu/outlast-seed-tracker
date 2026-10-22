"""统一的路径解析：源码运行和 PyInstaller 打包后的行为一致。

打包成 exe 后，`__file__` 指向临时解包目录（onefile 模式），
数据库必须落在 exe 旁边而不是临时目录里，否则用户录入的数据会随进程退出消失。
所以这里用 sys.frozen 判断：冻结时以 exe 所在目录为根，否则以源码目录为根。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "seed.db"
XLSX_PATH = DATA_DIR / "seed.xlsx"
