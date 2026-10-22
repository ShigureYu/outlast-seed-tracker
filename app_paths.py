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
