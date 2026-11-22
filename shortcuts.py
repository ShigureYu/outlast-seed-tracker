"""可配置快捷键：解析、校验与 Windows 全局热键映射。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

DEFAULT_SHORTCUTS = {
    "toggle_interactive": "Alt+B",
    "refresh_global": "Alt+N",
    "refresh_window": "F5",
    "force_save": "Ctrl+Return",
    "sync": "Ctrl+F5",
}

GLOBAL_ACTIONS = ("toggle_interactive", "refresh_global")
WINDOW_ACTIONS = ("refresh_window", "force_save", "sync")
ALL_ACTIONS = GLOBAL_ACTIONS + WINDOW_ACTIONS

ACTION_LABELS = {
    "toggle_interactive": "进入交互/编辑",
    "refresh_global": "全局刷新",
    "refresh_window": "窗口刷新",
    "force_save": "强制保存",
    "sync": "立即同步",
}


@dataclass(frozen=True)
class ShortcutSpec:
    action: str
    text: str
    sequence: QKeySequence
    global_: bool


def _parse(text: str) -> QKeySequence:
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if sequence.isEmpty() or len(sequence) != 1 or not normalized:
        raise ValueError(f"快捷键不合法或包含多段组合: {text!r}")
    return sequence


def load_shortcuts(config: dict) -> tuple[dict[str, ShortcutSpec], list[str]]:
    """合并默认配置并解析；非法项回退默认，返回警告列表。"""
    raw = config.get("shortcuts")
    if not isinstance(raw, dict):
        raw = {}

    specs: dict[str, ShortcutSpec] = {}
    warnings: list[str] = []
    seen: dict[str, str] = {}

    for action in ALL_ACTIONS:
        default_text = DEFAULT_SHORTCUTS[action]
        text = raw.get(action, default_text)
        if not isinstance(text, str) or not text.strip():
            warnings.append(f"{ACTION_LABELS[action]} 的快捷键为空，使用默认 {default_text}")
            text = default_text
        text = text.strip()
        try:
            sequence = _parse(text)
        except ValueError as exc:
            warnings.append(f"{ACTION_LABELS[action]}：{exc}，使用默认 {default_text}")
            text = default_text
            sequence = _parse(default_text)

        normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        duplicate_key = normalized.lower()
        if duplicate_key in seen:
            warnings.append(
                f"{ACTION_LABELS[action]} 与 {ACTION_LABELS[seen[duplicate_key]]} "
                f"重复使用 {normalized}，已忽略"
            )
            continue
        seen[duplicate_key] = action
        specs[action] = ShortcutSpec(
            action=action,
            text=normalized,
            sequence=sequence,
            global_=action in GLOBAL_ACTIONS,
        )

    return specs, warnings


def default_shortcut(action: str) -> ShortcutSpec:
    text = DEFAULT_SHORTCUTS[action]
    sequence = _parse(text)
    return ShortcutSpec(
        action=action,
        text=sequence.toString(QKeySequence.SequenceFormat.PortableText),
        sequence=sequence,
        global_=action in GLOBAL_ACTIONS,
    )


def _vk_for_key(key: int) -> int:
    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        return key
    if int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
        return key
    if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
        return 0x70 + (key - int(Qt.Key.Key_F1))

    special = {
        Qt.Key.Key_Space: 0x20,
        Qt.Key.Key_Tab: 0x09,
        Qt.Key.Key_Backspace: 0x08,
        Qt.Key.Key_Return: 0x0D,
        Qt.Key.Key_Enter: 0x0D,
        Qt.Key.Key_Escape: 0x1B,
        Qt.Key.Key_Insert: 0x2D,
        Qt.Key.Key_Delete: 0x2E,
        Qt.Key.Key_Home: 0x24,
        Qt.Key.Key_End: 0x23,
        Qt.Key.Key_PageUp: 0x21,
        Qt.Key.Key_PageDown: 0x22,
        Qt.Key.Key_Left: 0x25,
        Qt.Key.Key_Up: 0x26,
        Qt.Key.Key_Right: 0x27,
        Qt.Key.Key_Down: 0x28,
        Qt.Key.Key_Minus: 0xBD,
        Qt.Key.Key_Equal: 0xBB,
        Qt.Key.Key_Comma: 0xBC,
        Qt.Key.Key_Period: 0xBE,
        Qt.Key.Key_Slash: 0xBF,
        Qt.Key.Key_Semicolon: 0xBA,
        Qt.Key.Key_Apostrophe: 0xDE,
        Qt.Key.Key_BracketLeft: 0xDB,
        Qt.Key.Key_BracketRight: 0xDD,
        Qt.Key.Key_Backslash: 0xDC,
        Qt.Key.Key_QuoteLeft: 0xC0,
    }
    try:
        vk = special[Qt.Key(key)]
    except (KeyError, ValueError):
        raise ValueError(f"该按键无法注册为 Windows 全局热键: {QKeySequence(key).toString()}") from None
    return vk


def to_win32_hotkey(sequence: QKeySequence) -> tuple[int, int]:
    """把 Qt 快捷键转换为 RegisterHotKey 的 (modifiers, virtual-key)。"""
    if sequence.isEmpty() or len(sequence) != 1:
        raise ValueError("全局热键必须是单段组合键")
    combination = sequence[0]
    vk = _vk_for_key(int(combination.key()))
    modifiers = 0
    flags = combination.keyboardModifiers()
    if flags & Qt.KeyboardModifier.AltModifier:
        modifiers |= MOD_ALT
    if flags & Qt.KeyboardModifier.ControlModifier:
        modifiers |= MOD_CONTROL
    if flags & Qt.KeyboardModifier.ShiftModifier:
        modifiers |= MOD_SHIFT
    if flags & Qt.KeyboardModifier.MetaModifier:
        modifiers |= MOD_WIN
    return modifiers | MOD_NOREPEAT, vk


__all__ = [
    "ACTION_LABELS",
    "ALL_ACTIONS",
    "DEFAULT_SHORTCUTS",
    "GLOBAL_ACTIONS",
    "MOD_NOREPEAT",
    "ShortcutSpec",
    "WINDOW_ACTIONS",
    "default_shortcut",
    "load_shortcuts",
    "to_win32_hotkey",
]
