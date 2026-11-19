"""日志读取模块：定位游戏日志，取出最新的 Trial ID 和 Seed。

UE 每次启动都会重写 OPP.log，日志基本只追加。为了不随日志总大小变慢，
查询从文件末尾按块倒读，找到最近一局的 Trial ID / Seed / Stage 就停；
只有末尾找不到时才逐步扩大窗口，最后才退回整读。

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
STAGE_PATTERN = re.compile(r"Stage:\s*([A-Za-z0-9_]+)")
TRIAL_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
TAIL_CHUNK = 1024 * 1024  # 先读最后 1 MB；不够再翻倍


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


def _read_log_tail(file_path: Path | str, window: int) -> tuple[str, bool] | None:
    """读文件末尾最多 window 字节，返回 (文本, 是否已到文件开头)。"""
    path = Path(file_path)
    try:
        size = path.stat().st_size
        start = max(0, size - window)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
    except FileNotFoundError:
        logger.warning("日志文件不存在: %s", file_path)
        return None
    except OSError as exc:
        logger.warning("读取日志失败 %s: %s", file_path, exc)
        return None

    # 从块中间开始时可能截断 UTF-8 字符，errors="replace" 足够，不影响后面的 ASCII 字段
    return data.decode("utf-8", errors="replace"), start == 0 or not data


def _parse_game_text(text: str) -> tuple[str | None, str | None, str | None]:
    """从一段日志文本里取最后一条 Trial ID / Seed 对，以及对应的 Stage。"""
    pairs = STATE_PATTERN.findall(text)
    if pairs:
        trial_raw, seed_raw = pairs[-1]
    else:
        trial_matches = TRIAL_PATTERN.findall(text)
        seed_matches = SEED_PATTERN.findall(text)
        trial_raw = trial_matches[-1] if trial_matches else None
        seed_raw = seed_matches[-1] if seed_matches else None

    # Stage 和 Trial ID 通常在同一行；只从最后一条含 Trial ID 的行里取，
    # 免得日志跨了多局时拿到别的关卡的地图名。
    stage = None
    for line in reversed(text.splitlines()):
        if TRIAL_PATTERN.search(line) is None:
            continue
        match = STAGE_PATTERN.search(line)
        stage = match.group(1) if match else None
        break

    return trial_raw, seed_raw, stage


def _find_last_in_tail(
    file_path: Path | str, pattern: re.Pattern[str], window: int = TAIL_CHUNK
) -> str | None:
    """从文件末尾向前扩大窗口，返回 pattern 最后一次匹配；找不到返回 None。"""
    while True:
        result = _read_log_tail(file_path, window)
        if result is None:
            return None
        text, at_start = result
        matches = pattern.findall(text)
        if matches:
            return matches[-1]
        if at_start:
            return None
        window *= 2


def extract_seed_from_log(file_path: Path | str) -> str | None:
    """读出日志里最后一个 Seed，返回它的原始数字串；没有则返回 None。"""
    value = _find_last_in_tail(file_path, SEED_PATTERN)
    if value is None:
        logger.warning("日志中没有 Seed: %s", file_path)
    return value


def extract_trial_id_from_log(file_path: Path | str) -> str | None:
    """读出日志里最后一个 Trial ID（保持日志原样，大小写不转换）。

    注意日志里 Trial ID 和 Mission 常常是同一个值（CHJ_MT02 / CHJ_MT02），
    这里只认 `Trial ID:` 这个字段，不要用 Mission 兜底。
    """
    value = _find_last_in_tail(file_path, TRIAL_PATTERN)
    if value is None:
        logger.warning("日志中没有 Trial ID: %s", file_path)
    return value


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
    """把日志里的原始 Seed 还原成种子表里的整数串。

    官方输出的是 `seed × 1001`（把种子拼在自己后面的十进制结果），所以：

        860 -> "860860"    373 -> "373373"    58 -> "58058"    5 -> "5005"

    不能再按"固定三位重复两遍"或"对半拆"来猜——两位数种子只有 5 位，
    长度是奇数，拆半一定失败。用 ×1001 的逆运算对所有位数都成立。

    兼容性：哪天官方换规则，只改这个函数即可。
    """
    if not raw:
        return None

    digits = raw.strip()
    if not digits.isdigit():
        logger.warning("Seed 不是纯数字，无法还原: %r", raw)
        return None

    value = int(digits)

    # 主规则：seed × 1001
    if value > 0 and value % 1001 == 0:
        seed = value // 1001
        if 1 <= seed <= 999:
            return str(seed)

    # 兜底：偶数长度且两半相同（老日志里的写法）
    if len(digits) % 2 == 0:
        half = len(digits) // 2
        if digits[:half] == digits[half:]:
            return str(int(digits[:half]))

    logger.warning("Seed 不符合已知规则（×1001），按原值处理: %r", digits)
    return str(value)


def read_current_seed() -> str | None:
    """一步到位：找最新日志 -> 取最新 Seed -> 还原成整数串。供 UI 的"刷新"按钮调用。"""
    log_file = find_latest_log()
    if log_file is None:
        return None
    return normalize_seed(extract_seed_from_log(log_file))


def read_game_state() -> tuple[str | None, str | None, str | None]:
    """从日志末尾倒查，返回 (原始 Trial ID, 原始 Seed, Stage)。

    默认只读最后 1 MB；找不到完整状态再翻倍扩大窗口，最后才退回整读。
    三个值都保持日志原样；Stage 取自最后一条含 Trial ID 的行，
    供登记 TRIAL_META.map_name 用。日志里没有 Stage 时返回 None。
    """
    log_file = find_latest_log()
    if log_file is None:
        return None, None, None

    window = TAIL_CHUNK
    while True:
        result = _read_log_tail(log_file, window)
        if result is None:
            return None, None, None
        text, at_start = result
        trial_raw, seed_raw, stage = _parse_game_text(text)
        if at_start or (trial_raw and seed_raw and stage):
            return trial_raw, seed_raw, stage
        window *= 2


def read_current_state() -> tuple[str | None, str | None]:
    """一次读取文件，同时返回 (trial_id, seed)。UI 刷新按钮用这个最省事。

    优先取"最后一条同时含 Trial ID 和 Seed 的行"，保证两者属于同一局；
    日志格式若变了导致找不到这种行，再退回各取最后一次出现。
    """
    trial_raw, seed_raw, _ = read_game_state()
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
