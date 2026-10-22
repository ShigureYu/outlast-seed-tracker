"""统一的路径解析：源码运行和 PyInstaller 打包后的行为一致。

打包成 exe 后，`__file__` 指向临时解包目录（onefile 模式），
数据库必须落在 exe 旁边而不是临时目录里，否则用户录入的数据会随进程退出消失。
所以这里用 sys.frozen 判断：冻结时以 exe 所在目录为根，否则以源码目录为根。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    """打包后是 onefile 的解包目录；源码运行时就是本目录。"""
    return Path(getattr(sys, "_MEIPASS", _app_dir()))


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "seed.db"
XLSX_PATH = DATA_DIR / "seed.xlsx"
BUNDLED_DB = _bundle_dir() / "data" / "seed.db"


def ensure_database() -> bool:
    """保证 exe 旁边有可用的 seed.db，返回数据库是否可用。

    单文件 exe 里带了一份模板库；首次运行时复制到 exe 同级目录，
    之后的录入都写这份副本（不会因为重新下载 exe 而丢失）。
    源码运行时 DB_PATH 和 BUNDLED_DB 是同一个文件，直接判断存在即可。
    """
    if DB_PATH.is_file():
        return True
    if not BUNDLED_DB.is_file() or BUNDLED_DB == DB_PATH:
        return False
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLED_DB, DB_PATH)
    return True


def enable_utf8_stdout() -> None:
    """让 print 中文在 Windows 控制台不再崩。

    cmd.exe 和 CI runner 的默认代码页是 cp936 / cp1252，直接 print 中文会
    UnicodeEncodeError；这里统一改成 UTF-8，编不出来的字符用替代符而不是抛异常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # 被重定向到不支持 reconfigure 的对象
            pass
