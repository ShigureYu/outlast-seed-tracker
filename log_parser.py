"""日志读取模块：定位游戏日志，取出最新的 Trial ID 和 Seed。

UE 每次启动都会重写 OPP.log，单文件通常只有几 MB，所以直接整读最省事：
一次 read() 加几毫秒，没必要为了这点时间写块倒查。

日志里的两行关键信息长这样：
    RB:  GameStageInfo changed. Program ID: programHalloween, Trial ID: CHJ_MT02, ...
         ..., Stage: CourthouseJudicial, Mission: CHJ_MT02, Seed: 815815, ...
    RB:  StageReady. Trial ID: FPI_MT02, Stage: FunParkIllusion, ..., Seed: 543543, ...
"""

import logging
import os
import re
from pathlib import Path

from app_paths import enable_utf8_stdout

logger = logging.getLogger(__name__)

LOG_DIR = Path("OPP") / "Saved" / "Logs"
LOG_GLOB = "OPP*.log"
SEED_PATTERN = re.compile(r"Seed:\s*(\d+)")
TRIAL_PATTERN = re.compile(r"Trial ID:\s*([A-Za-z0-9_]+)")
# 同一行里同时有 Trial ID 和 Seed，优先用它，避免两者来自不同阶段
STATE_PATTERN = re.compile(r"Trial ID:\s*([A-Za-z0-9_]+).{0,300}?Seed:\s*(\d+)")
TRIAL_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


def get_log_dir() -> Path:
    """返回日志目录 C:\\Users\\<用户名>\\AppData\\Local\\OPP\\Saved\\Logs。

    用 %LOCALAPPDATA% 而不是 os.getlogin()：后者在没有控制台的环境
    （打包成无窗口 exe）里会直接抛 OSError。
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base / LOG_DIR


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_latest_log() -> Path | None:
    """返回日志目录里最新的日志文件，找不到返回 None。

    UE 会滚动日志（OPP.log + OPP-backup-*.log），崩溃重启后 OPP.log
    可能反而比备份旧，所以按修改时间挑，而不是写死文件名。
    """
    log_dir = get_log_dir()
    if not log_dir.is_dir():
        logger.warning("日志目录不存在: %s", log_dir)
        return None

    candidates = [p for p in log_dir.glob(LOG_GLOB) if p.is_file()]
    if not candidates:
        logger.warning("日志目录中没有 %s: %s", LOG_GLOB, log_dir)
        return None

    latest = max(candidates, key=_mtime)
    logger.debug("使用日志文件: %s", latest)
    return latest


def _read_log_text(file_path: Path | str) -> str | None:
    """整读日志文本；失败时记一条日志并返回 None。"""
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        logger.warning("日志文件不存在: %s", file_path)
    except OSError as exc:  # 权限不足 / 文件正被游戏占用等
        logger.warning("读取日志失败 %s: %s", file_path, exc)
    return None


def extract_seed_from_log(file_path: Path | str) -> str | None:
    """读出日志里最后一个 Seed，返回它的原始数字串；没有则返回 None。"""
    text = _read_log_text(file_path)
    if text is None:
        return None

    matches = SEED_PATTERN.findall(text)
    if not matches:
        logger.warning("日志中没有 Seed: %s", file_path)
        return None
    return matches[-1]


def extract_trial_id_from_log(file_path: Path | str) -> str | None:
    """读出日志里最后一个 Trial ID（保持日志原样，大小写不转换）。

    注意日志里 Trial ID 和 Mission 常常是同一个值（CHJ_MT02 / CHJ_MT02），
    这里只认 `Trial ID:` 这个字段，不要用 Mission 兜底。
    """
    text = _read_log_text(file_path)
    if text is None:
        return None

    matches = TRIAL_PATTERN.findall(text)
    if not matches:
        logger.warning("日志中没有 Trial ID: %s", file_path)
        return None
    return matches[-1]


def normalize_trial_id(raw: str | None) -> str | None:
    """把 Trial ID 收敛成可以当表名的形态。

    日志里是 `CHJ_MT02` 这种全大写，表名也按全大写建，所以这里只做 strip
    和大写化；万一日志改成小写，也能落到同一张表上。
    """
    if not raw:
        return None

    value = raw.strip().upper()
    if not TRIAL_ID_RE.match(value):
        logger.warning("Trial ID 形态可疑，按原样使用: %r", value)
    return value


def read_current_trial_id() -> str | None:
    """一步到位：找最新日志 -> 取最后出现的 Trial ID -> 归一成大写下划线。"""
    log_file = find_latest_log()
    if log_file is None:
        return None
    return normalize_trial_id(extract_trial_id_from_log(log_file))


def normalize_seed(raw: str | None) -> str | None:
    """把日志里的原始 Seed 串还原成本项目种子表里的整数串。

    这是**有意为之**的还原，不是截断事故：2026-10 抓到的全部日志中，
    官方输出的都是同一个种子重复两遍（543 -> "543543"、815 -> "815815"）。

    注意不能用"取前 3 位"来实现：种子表里存在 1、80 这类不足三位的种子，
    官方是否补零未经验证——补零会输出 "001001"，不补则输出 "11"。
    所以这里改成"从中间对半拆开、验证两半相同"，两种写法都能还原。

    兼容性：哪天官方换了输出规则，只改这个函数即可；
    extract_seed_from_log 仍然返回原始串，read_current_seed 的调用方不用动。
    """
    if not raw:
        return None

    digits = raw.strip()
    if not digits.isdigit():
        logger.warning("Seed 不是纯数字，无法还原: %r", raw)
        return None

    if len(digits) % 2:
        # 不像重复串，但也不至于丢数据：按原值处理并留一条警告
        logger.warning("Seed 长度是奇数，不符合“重复两遍”的已知规则: %r", digits)
        return str(int(digits))

    half = len(digits) // 2
    head, tail = digits[:half], digits[half:]
    if head != tail:
        logger.warning("Seed 两半不一致，按前半处理: %r", digits)
    return str(int(head))


def read_current_seed() -> str | None:
    """一步到位：找最新日志 -> 取最新 Seed -> 还原成整数串。供 UI 的"刷新"按钮调用。"""
    log_file = find_latest_log()
    if log_file is None:
        return None
    return normalize_seed(extract_seed_from_log(log_file))


def read_current_state() -> tuple[str | None, str | None]:
    """一次读取文件，同时返回 (trial_id, seed)。UI 刷新按钮用这个最省事。

    优先取"最后一条同时含 Trial ID 和 Seed 的行"，保证两者属于同一局；
    日志格式若变了导致找不到这种行，再退回各取最后一次出现。
    """
    log_file = find_latest_log()
    if log_file is None:
        return None, None

    text = _read_log_text(log_file)
    if text is None:
        return None, None

    pairs = STATE_PATTERN.findall(text)
    if pairs:
        trial_raw, seed_raw = pairs[-1]
    else:
        logger.info("没有同时含 Trial ID 与 Seed 的行，两者分别取最后一次出现")
        trial_matches = TRIAL_PATTERN.findall(text)
        seed_matches = SEED_PATTERN.findall(text)
        trial_raw = trial_matches[-1] if trial_matches else None
        seed_raw = seed_matches[-1] if seed_matches else None

    return normalize_trial_id(trial_raw), normalize_seed(seed_raw)


def main() -> None:
    enable_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    log_file = find_latest_log()
    if log_file is None:
        return

    raw_seed = extract_seed_from_log(log_file)
    raw_trial = extract_trial_id_from_log(log_file)
    print(f"日志文件: {log_file}")
    print(f"原始 Trial ID: {raw_trial}" if raw_trial else "日志中未找到 Trial ID")
    print(f"原始 Seed: {raw_seed}" if raw_seed else "日志中未找到 Seed")

    trial_id, seed = read_current_state()
    print(f"归一 Trial ID: {trial_id}" if trial_id else "Trial ID 无法归一")
    print(f"归一 Seed: {seed}" if seed else "Seed 无法归一")


if __name__ == "__main__":
    main()
