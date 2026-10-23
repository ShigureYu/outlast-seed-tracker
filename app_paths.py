"""统一的路径与配置解析：源码运行和 PyInstaller 打包后的行为一致。

打包成 exe 后，`__file__` 指向临时解包目录（onefile 模式），
数据库必须落在 exe 旁边而不是临时目录里，否则用户录入的数据会随进程退出消失。
所以这里用 sys.frozen 判断：冻结时以 exe 所在目录为根，否则以源码目录为根。

数据库位置按以下优先级解析：
    1. 环境变量 OPPOVERLAY_DB
    2. data/config.json 里的 "database"
    3. 默认 data/seed.db
相对路径一律相对 data/ 目录，所以配置里写个文件名即可。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    """打包后是 onefile 的解包目录；源码运行时就是本目录。"""
    return Path(getattr(sys, "_MEIPASS", _app_dir()))


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
XLSX_PATH = DATA_DIR / "seed.xlsx"
DEFAULT_DB_NAME = "seed.db"

DEFAULT_CONFIG = {
    "database": DEFAULT_DB_NAME,
    "poll_seconds": 60,
    "_help": {
        "database": "答案库文件名（相对 data/ 目录）。测试可用 seed-local.db",
        "poll_seconds": "扫描游戏日志的间隔秒数",
    },
}


def load_config() -> dict:
    """读 data/config.json；没有文件、读不动、JSON 坏掉都退回默认值。"""
    try:
        # 记事本另存为 UTF-8 时可能带 BOM，utf-8-sig 能正常读
        raw = CONFIG_PATH.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except OSError as exc:
        logger.warning("读取配置失败，使用默认值: %s", exc)
        return dict(DEFAULT_CONFIG)

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        logger.warning("config.json 不是合法 JSON，使用默认值: %s", exc)
        return dict(DEFAULT_CONFIG)

    if not isinstance(parsed, dict):
        logger.warning("config.json 顶层不是对象，使用默认值")
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(parsed)
    return merged


def _resolve_db_path() -> Path:
    override = os.environ.get("OPPOVERLAY_DB")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_absolute() else DATA_DIR / candidate

    name = str(load_config().get("database") or DEFAULT_DB_NAME)
    candidate = Path(name).expanduser()
    return candidate if candidate.is_absolute() else DATA_DIR / candidate


DB_PATH = _resolve_db_path()
BUNDLED_DB = _bundle_dir() / "data" / DEFAULT_DB_NAME


def ensure_database() -> bool:
    """保证 DB_PATH 指向的库存在，返回是否可用。

    单文件 exe 里带了一份模板库；首次运行时复制到目标位置，
    之后的录入都写这份副本（不会因为重新下载 exe 而丢失）。
    """
    if DB_PATH.is_file():
        return True
    if not BUNDLED_DB.is_file() or BUNDLED_DB == DB_PATH:
        return False
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLED_DB, DB_PATH)
    logger.info("已从内置模板创建数据库: %s", DB_PATH)
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
