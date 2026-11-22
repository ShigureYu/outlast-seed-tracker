"""无边框、半透明、始终置顶的悬浮窗：显示当前 Seed，能查就查，查不到就录入。

刷新策略：
- 每 60 秒扫一次日志，只有 (Trial ID, Seed) 变了才重建界面，避免打字被冲掉；
- 录入过程中暂停自动刷新；F5 / 全局 Alt+N 或点 ⟳ 可以手动刷新。

状态（从上到下三个区）：
- 顶部：始终显示 Seed；库里有这张表时，旁边补上「中文名 CHJ_MT02」。
- 中部：表存在 -> 按表结构渲染每个图案一行；有记录则只读，没记录则可编辑。
- 底部：仅"表存在但没记录"时出现保存按钮。
"""

from __future__ import annotations

import logging
import sqlite3
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
else:  # pragma: no cover - 项目主目标平台是 Windows
    ctypes = None
    wintypes = None

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QPoint,
    QRegularExpression,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QKeySequence,
    QPainter,
    QRegularExpressionValidator,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

import fpi_mt02
import updater
from app_paths import (
    DB_PATH,
    enable_utf8_stdout,
    ensure_database,
    load_config,
)
from fpi_mt02_view import FPI_MT02View
from log_parser import (
    find_latest_log,
    normalize_seed,
    normalize_trial_id,
    read_game_state,
)
from seed_lookup import describe_trial, read_codes, register_trial_from_log, save_codes
from slot_config import SlotSpec
from trial_info import load_trial_info

logger = logging.getLogger(__name__)

POLL_MS = 60_000
SERVER_NAME = "OPPOverlay-single-instance"
MIN_WINDOW_WIDTH = 300
MAX_WINDOW_WIDTH = 560

# Windows 全局热键 / 鼠标穿透
HOTKEY_ID_TOGGLE = 0x4F50
HOTKEY_ID_REFRESH = 0x4F51
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

_user32 = ctypes.windll.user32 if ctypes is not None else None
if _user32 is not None:
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL


STYLE = """
QWidget {
    color: #e9e9ec;
    font-family: "Microsoft YaHei", "Segoe UI";
    font-size: 15px;
}
QLabel#seed {
    font-size: 17px;
    font-weight: 600;
}
QLabel#trial {
    font-size: 15px;
    color: #e9e9ec;
}
QLabel#hint {
    font-size: 14px;
    color: #ff8a80;
}
QLabel#update {
    font-size: 13px;
    color: #8fe3a0;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}
QLineEdit {
    background: rgba(255, 255, 255, 26);
    border: 1px solid rgba(255, 255, 255, 46);
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 16px;
    color: #ffffff;
}
QLineEdit[readOnly="true"] {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 16px;
    font-weight: 600;
    color: #7ebf96;
}
QPushButton {
    background: rgba(255, 255, 255, 36);
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 14px;
}
QLabel#slot {
    font-size: 15px;
    font-weight: 600;
    color: #d8dbe2;
}
QLabel#badge {
    font-size: 15px;
    padding: 1px 10px;
    color: #ffcc66;
    border: 1px solid rgba(255, 204, 102, 130);
    border-radius: 6px;
}
QLabel#refresh {
    font-size: 15px;
    padding: 1px 10px;
    color: #e9e9ec;
    border: 1px solid rgba(255, 255, 255, 48);
    border-radius: 6px;
}
QLabel#refresh:hover {
    color: #ffffff;
    border-color: rgba(255, 255, 255, 110);
    background: rgba(255, 255, 255, 24);
}

QLabel#close {
    font-size: 17px;
    color: rgba(255, 255, 255, 150);
}
QLabel#close:hover {
    background: rgba(220, 70, 70, 190);
    color: #ffffff;
    border-radius: 6px;
}
QPushButton#refresh {
    font-size: 11px;
    padding: 0;
    background: rgba(255, 255, 255, 28);
}

QTabWidget#fpiTabs {
    background: transparent;
    outline: none;
}
QTabWidget#fpiTabs:focus {
    outline: none;
}
QTabBar {
    outline: none;
}
QTabWidget#fpiTabs::pane {
    background: transparent;
    border: none;
}
QWidget#fpiTabPage {
    background: transparent;
}
QTabBar::tab {
    background: rgba(255, 255, 255, 22);
    color: #cdd1da;
    border: 1px solid rgba(255, 255, 255, 30);
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 4px 16px;
    margin-right: 2px;
    font-size: 14px;
}
QTabBar::tab:selected {
    background: rgba(255, 255, 255, 52);
    color: #ffffff;
}
QTabBar::tab:hover {
    background: rgba(255, 255, 255, 38);
}
QPushButton#fpiPicker, QPushButton#fpiPattern, QComboBox#fpiPattern {
    background: rgba(255, 255, 255, 26);
    border: 1px solid rgba(255, 255, 255, 46);
    border-radius: 5px;
    color: #ffffff;
    min-height: 24px;
    outline: none;
}
QPushButton#fpiPicker, QPushButton#fpiPattern {
    text-align: left;
    padding: 4px 10px;
}
QComboBox#fpiPattern {
    padding: 4px 24px 4px 10px;
}
QComboBox#fpiPattern::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border: none;
    background: transparent;
}
QPushButton#fpiPicker:focus, QPushButton#fpiPattern:focus, QComboBox#fpiPattern:focus {
    border: 1px solid rgba(255, 255, 255, 80);
    outline: none;
}
QPushButton#fpiPicker:disabled, QPushButton#fpiPattern:disabled, QComboBox#fpiPattern:disabled {
    background: rgba(255, 255, 255, 14);
    border: 1px solid rgba(255, 255, 255, 24);
    color: #d8dbe2;
}
QComboBox#fpiPattern QAbstractItemView {
    background: #20232a;
    color: #e9e9ec;
    selection-background-color: #4a6a8a;
    selection-color: #ffffff;
}
QLineEdit#fpiNumber {
    padding: 4px 8px;
    text-align: center;
    font-size: 16px;
    font-weight: 600;
    color: #ffffff;
    outline: none;
}
QLineEdit#fpiNumber:focus {
    border: 1px solid rgba(255, 255, 255, 80);
    outline: none;
}
QLineEdit#fpiNumber:disabled {
    background: transparent;
    border: none;
    color: #7ebf96;
}
QFrame#fpiPopup, QFrame#fpiPatternPopup {
    background-color: #1d2026;
    border: 1px solid rgba(255, 255, 255, 55);
    border-radius: 6px;
}
QListWidget#fpiFloorList, QListWidget#fpiRoomList, QListWidget#fpiPatternList {
    background-color: #1d2026;
    border: none;
    color: #e9e9ec;
    outline: none;
}
QListWidget#fpiFloorList QWidget, QListWidget#fpiRoomList QWidget,
QListWidget#fpiPatternList QWidget {
    background-color: #1d2026;
}
QListWidget#fpiFloorList QScrollBar:vertical,
QListWidget#fpiRoomList QScrollBar:vertical,
QListWidget#fpiPatternList QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QListWidget#fpiFloorList QScrollBar::handle:vertical,
QListWidget#fpiRoomList QScrollBar::handle:vertical,
QListWidget#fpiPatternList QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 80);
    border-radius: 4px;
    min-height: 20px;
}
QListWidget#fpiFloorList QScrollBar::add-line:vertical,
QListWidget#fpiRoomList QScrollBar::add-line:vertical,
QListWidget#fpiPatternList QScrollBar::add-line:vertical,
QListWidget#fpiFloorList QScrollBar::sub-line:vertical,
QListWidget#fpiRoomList QScrollBar::sub-line:vertical,
QListWidget#fpiPatternList QScrollBar::sub-line:vertical {
    height: 0;
}
QListWidget#fpiFloorList::item, QListWidget#fpiRoomList::item,
QListWidget#fpiPatternList::item {
    padding: 5px 8px;
    border-radius: 4px;
}
QListWidget#fpiFloorList::item:selected,
QListWidget#fpiRoomList::item:selected,
QListWidget#fpiPatternList::item:selected {
    background: rgba(120, 170, 220, 90);
    color: #ffffff;
}
QListWidget#fpiFloorList::item:hover,
QListWidget#fpiRoomList::item:hover,
QListWidget#fpiPatternList::item:hover {
    background: rgba(255, 255, 255, 28);
}

QLabel#close {
    font-size: 17px;
    color: rgba(255, 255, 255, 150);
}
QLabel#close:hover {
    background: rgba(220, 70, 70, 190);
    color: #ffffff;
    border-radius: 6px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 60);
}
QPushButton:disabled {
    color: rgba(255, 255, 255, 90);
}
QLabel#choiceView {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
    color: #7ebf96;
}
QPushButton#choiceSlot {
    background: rgba(255, 255, 255, 26);
    border: 1px solid rgba(255, 255, 255, 46);
    border-radius: 5px;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
    color: #e9e9ec;
}
QPushButton#choiceSlot:hover {
    background: rgba(255, 255, 255, 46);
    border-color: rgba(255, 255, 255, 90);
}
QFrame#choicePopup {
    background: #1b1b20;
    border: 1px solid rgba(255, 255, 255, 70);
    border-radius: 8px;
}
QPushButton#choiceItem {
    background: rgba(255, 255, 255, 30);
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 0;
    font-size: 15px;
    color: #e9e9ec;
}
QPushButton#choiceItem:hover {
    background: rgba(255, 255, 255, 60);
}
QPushButton#choiceItem[selected="true"] {
    border-color: #8fe3a0;
    color: #8fe3a0;
}
"""


class ClickableLabel(QLabel):
    """当按钮用的标签：绘制路径和普通文字完全一致，不会出现按钮那种糊字。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class UpdateWorker(QThread):
    """后台检查更新：下载并合并，完成后通过信号把结果交回主线程。"""

    done = pyqtSignal(object)

    def __init__(self, db_path, parent=None) -> None:
        super().__init__(parent)
        self._db_path = db_path

    def run(self) -> None:
        try:
            remote = updater.fetch_remote(self._db_path)
            if remote is None:
                self.done.emit(None)
                return
            report = updater.merge(remote, self._db_path)
            remote.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - 同步失败不该拖垮界面
            logger.warning("同步失败: %s", exc)
            self.done.emit(None)
            return
        self.done.emit(report)


class ChoicePopup(QFrame):
    """3×3 图案选择面板：点击或按数字 1-9 选中，Esc 关闭。"""

    chosen = pyqtSignal(str)

    def __init__(self, choices: dict[str, str], current: str | None, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("choicePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buttons: dict[str, QPushButton] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for index, (digit, label) in enumerate(choices.items()):
            button = QPushButton(label)
            button.setObjectName("choiceItem")
            button.setFixedSize(84, 32)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, d=digit: self._choose(d))
            grid.addWidget(button, index // 3, index % 3)
            self._buttons[digit] = button
        if current in self._buttons:
            self._buttons[current].setProperty("selected", "true")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _choose(self, digit: str) -> None:
        self.chosen.emit(digit)
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        text = event.text()
        if text in self._buttons:
            self._choose(text)
            return
        super().keyPressEvent(event)

    def show_near(self, anchor: QWidget) -> None:
        """优先显示在按钮下方；贴近屏幕边缘时上翻/左右收进屏幕。"""
        self.adjustSize()
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        pos = QPoint(
            top_left.x() + anchor.width() // 2 - self.width() // 2,
            top_left.y() + anchor.height() + 4,
        )
        screen = anchor.screen()
        if screen is not None:
            area = screen.availableGeometry()
            if pos.x() + self.width() > area.right():
                pos.setX(area.right() - self.width())
            if pos.x() < area.left():
                pos.setX(area.left())
            if pos.y() + self.height() > area.bottom():
                pos.setY(top_left.y() - self.height() - 4)
        self.move(pos)
        self.show()
        self.setFocus()


class HotkeyFilter(QAbstractNativeEventFilter):
    """接收 Windows 全局热键消息。"""

    def __init__(self, callbacks: dict[int, object]) -> None:
        super().__init__()
        self._callbacks = callbacks

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt 接口
        if _user32 is None:
            return False, 0
        if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0
        if msg.message != WM_HOTKEY:
            return False, 0
        callback = self._callbacks.get(int(msg.wParam))
        if callback is not None:
            QTimer.singleShot(0, callback)
            return True, 0
        return False, 0


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        # 无边框 + 置顶 + Tool 让它不进任务栏
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("OPP Overlay")
        self._dev_db = DB_PATH.name.lower() == "seed_full.db"
        self.setMinimumWidth(MIN_WINDOW_WIDTH)
        self.setStyleSheet(STYLE)

        self._drag_offset: QPoint | None = None
        self._placed = False
        self._editing = False
        self._interactive = False
        self._hwnd = 0
        self._last_foreground_hwnd = 0
        self._hotkey_filter: HotkeyFilter | None = None
        self._hotkey_ids: set[int] = set()
        self._win_features_ready = False
        self._last_key: tuple[str | None, str | None] | None = None

        self._trial: str | None = None
        self._seed: str | None = None
        self._inputs: dict[str, QWidget] = {}
        self._slot_specs: dict[str, SlotSpec] = {}
        self._picker: ChoicePopup | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ---- 顶部状态区：第一行 Seed + 按钮，第二行关卡名 ----
        top = QHBoxLayout()
        top.setSpacing(6)
        self.seed_label = QLabel("Seed: --")
        self.seed_label.setObjectName("seed")
        self.seed_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # 关卡名：夹在 Seed 和按钮中间，只显示中文名
        self.trial_label = QLabel("")
        self.trial_label.setObjectName("trial")
        self.trial_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trial_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.trial_label.setVisible(False)

        self.refresh_button = ClickableLabel("刷新")
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setToolTip("手动刷新（F5 / 全局 Alt+N）")
        self.refresh_button.clicked.connect(self._manual_refresh)

        # 用的不是正式库时给个显眼标记，避免忘了自己在沙盒里
        self.db_badge = QLabel("dev")
        self.db_badge.setObjectName("badge")
        self.db_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.db_badge.setToolTip(f"开发数据库：{DB_PATH.name}")
        self.db_badge.setVisible(self._dev_db)

        self.close_button = ClickableLabel("×")
        self.close_button.setObjectName("close")
        self.close_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_button.setFixedSize(26, 26)
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.close)

        top.addWidget(self.seed_label)
        top.addStretch(1)
        top.addWidget(self.trial_label)
        top.addStretch(1)
        top.addWidget(self.db_badge)
        top.addWidget(self.refresh_button)
        top.addWidget(self.close_button)
        root.addLayout(top)

        # ---- 中部数据区 ----
        self.body = QWidget(self)
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.body_stack = QStackedLayout(self.body)
        self.body_stack.setContentsMargins(0, 0, 0, 0)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.body_stack.addWidget(self.grid_host)
        self.body_stack.setCurrentWidget(self.grid_host)
        self._fpi_view: FPI_MT02View | None = None
        root.addWidget(self.body)

        # ---- 底部操作区 ----
        footer = QHBoxLayout()
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_edit)
        self.collapse_button = ClickableLabel("折叠")
        self.collapse_button.setObjectName("refresh")
        self.collapse_button.setToolTip("只保留顶栏")
        self.collapse_button.setFixedHeight(24)
        self.collapse_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.collapse_button.clicked.connect(self.toggle_collapse)

        footer.addWidget(self.collapse_button)
        footer.addStretch(1)
        footer.addWidget(self.hint)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        self.footer = QWidget(self)
        self.footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.footer.setLayout(footer)
        # 只读/编辑切换时保存按钮会显隐；固定 footer 高度，避免折叠按钮上下跳。
        footer_margins = footer.contentsMargins()
        self.footer.setFixedHeight(
            max(
                self.collapse_button.sizeHint().height(),
                self.save_button.sizeHint().height(),
            )
            + footer_margins.top()
            + footer_margins.bottom()
        )
        root.addWidget(self.footer)

        # ---- 数据同步提示（默认隐藏，不占高度）----
        self.update_bar = QWidget(self)
        bar = QHBoxLayout(self.update_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.update_label = QLabel("")
        self.update_label.setObjectName("update")
        self.update_label.setWordWrap(True)
        self.ignore_button = ClickableLabel("知道了")
        self.ignore_button.setObjectName("refresh")
        self.ignore_button.clicked.connect(self.dismiss_update)
        bar.addWidget(self.update_label)
        bar.addStretch(1)
        bar.addWidget(self.ignore_button)
        self.update_bar.setVisible(False)
        root.addWidget(self.update_bar)



        self._worker = None
        self._editing_existing = False
        self._collapsed = False
        self._input_labels: dict[str, str] = {}
        self._hint_token = 0
        self._log_stamp: tuple[int, int] | None = None

        self.shortcut = QShortcut(QKeySequence("F5"), self)
        self.shortcut.activated.connect(self._manual_refresh)
        self.global_refresh_shortcut = QShortcut(QKeySequence("Alt+N"), self)
        self.global_refresh_shortcut.activated.connect(self._manual_refresh)
        self.force_save_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.force_save_shortcut.activated.connect(self._force_save)
        self.sync_shortcut = QShortcut(QKeySequence("Ctrl+F5"), self)
        self.sync_shortcut.activated.connect(self.sync_now)

        poll_seconds = int(load_config().get("poll_seconds") or POLL_MS // 1000)
        self._timer = QTimer(self)
        self._timer.setInterval(max(5, poll_seconds) * 1000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        # 启动 1.5 秒后后台检查一次更新，不阻塞界面
        QTimer.singleShot(1500, self.check_updates)

    # ---------- 日志 ----------
    @staticmethod
    def _read_log() -> tuple[str | None, str | None, str | None]:
        """一次读日志，返回 (原始 Trial ID, 原始 Seed, Stage)。"""
        return read_game_state()

    # ---------- 刷新 ----------
    def _poll(self) -> None:
        if self._editing:  # 录入中不打断
            return
        # 日志文件没变就什么都不做：省掉整读和布局重算
        log_file = find_latest_log()
        if log_file is None:
            return
        try:
            stat = log_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return
        if stamp == self._log_stamp:
            return
        self._log_stamp = stamp
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        raw_trial, raw_seed, map_name = self._read_log()
        trial = normalize_trial_id(raw_trial)
        seed = normalize_seed(raw_seed)
        key = (trial, seed)

        if not force and key == self._last_key and not self._editing:
            return
        self._last_key = key
        self._trial, self._seed = trial, seed

        # 日志里新出现的关卡先登记到 TRIAL_META（只插不改）。
        # 放在 describe_trial 之前，这次刷新就能把地图名显示出来。
        register_trial_from_log(trial, map_name)

        # 有意显示日志原文（如 58058），而不是还原后的 58：
        # 排查问题时可以直接拿这个数去日志里搜，省掉心算 ×1001 的逆运算。
        self.seed_label.setText(f"Seed: {raw_seed or '--'}")
        self._set_hint("")
        self._clear_body()

        if seed is None:
            # 状态 D：连 seed 都没有，什么都不渲染
            self._set_trial_text("")
            self._set_editing(False)
            self.body.setVisible(False)
            self._fit()
            return

        info = describe_trial(trial) if trial else None
        if info is None:
            # 状态 A：拿不到关卡信息
            self._set_trial_text("")
            self._set_editing(False)
            self.body.setVisible(False)
            self._fit()
            return

        if trial == fpi_mt02.TRIAL_ID:
            self._set_trial_text(info["name_zh"] or info["map_name"] or info["trial_id"])
            self._render_fpi_mt02(seed)
            self._set_editing(False)
            self._fit()
            return

        # 中文名还没录入时退到地图名，至少能看出是哪一关
        self._set_trial_text(info["name_zh"] or info["map_name"] or info["trial_id"])

        if not info["slots"]:
            # 没有答案表或 meta 没登记任何列：只显示 Seed 和关卡名
            self._set_editing(False)
            self._apply_collapsed()
            return

        codes = read_codes(seed, trial_id=trial)
        if codes is None:
            # 状态 B：表在，但没这条 seed，允许录入
            self._render_rows(info, dict.fromkeys(name for name, _ in info["slots"]))
            self._set_editing(True)
        else:
            # 状态 C：只读展示
            self._render_rows(info, codes, readonly=True)
            self._set_editing(False)

        self._fit()

    def _set_trial_text(self, text: str) -> None:
        """关卡名有内容才占位，没有就整行收起来。"""
        self.trial_label.setText(text)
        self.trial_label.setVisible(bool(text))

    def _clear_body(self) -> None:
        if self._picker is not None:
            self._picker.close()
            self._picker = None
        if self._fpi_view is not None:
            self.body_stack.removeWidget(self._fpi_view)
            self._fpi_view.deleteLater()
            self._fpi_view = None
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先断开父子关系，让控件立刻从布局和 findChildren 里消失；
                # 只调 deleteLater() 要等下一轮事件循环，期间 adjustSize 会算上旧控件
                widget.setParent(None)
                widget.deleteLater()
        self._inputs = {}
        self.body_stack.setCurrentWidget(self.grid_host)

    def _render_fpi_mt02(self, seed: int) -> None:
        try:
            info = load_trial_info(fpi_mt02.TRIAL_ID)
            entries = fpi_mt02.read_turns(seed)
        except (OSError, ValueError) as exc:
            logger.exception("加载 FPI_MT02 配置失败")
            self._set_hint(f"FPI_MT02 配置错误: {exc}", error=True)
            return

        view = FPI_MT02View(info)
        view.set_data(entries)
        view.set_readonly(True)
        view.editRequested.connect(self.begin_edit)
        self._fpi_view = view
        self.body_stack.addWidget(view)
        self.body_stack.setCurrentWidget(view)

    def _render_rows(
        self,
        info: dict,
        values: dict[str, str | None],
        readonly: bool = False,
    ) -> None:
        """每个图案一个小格：名字在上、值在下，横向排布。

        普通关卡值是数字输入框；配置了 choices 的关卡，只读时显示图案名，
        编辑时显示按钮，点开是 3×3 图案面板。
        """
        self._inputs = {}
        self._input_labels = {}
        self._slot_specs = info.get("slot_specs") or {}
        self._edit_snapshot = {}
        slots = info["slots"]
        per_row = 3 if len(slots) == 6 else 5  # 6 格排成 3+3，其余最多 5 个一行
        # 格子少于一行容量时居中显示，避免三个框被拉到整行宽
        center = len(slots) == 1
        offset = 1 if center else 0
        # 切换关卡后沿用同一个 QGridLayout，先清掉上一次的列拉伸
        for column_index in range(self.grid.columnCount()):
            self.grid.setColumnStretch(column_index, 0)
        if center:
            self.grid.addWidget(QWidget(), 0, 0)
            self.grid.setColumnStretch(0, 1)

        for index, (name, label) in enumerate(slots):
            tile = QWidget()
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            column = QVBoxLayout(tile)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)

            caption = QLabel(label)
            caption.setObjectName("slot")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

            value = values.get(name)
            spec = self._slot_specs.get(name)
            if spec is None:
                logger.warning("slot %s 没有配置，跳过渲染", name)
                continue
            if spec.kind == "pattern":
                field = self._make_choice_widget(name, value, readonly, spec)
            else:
                max_length, pattern, placeholder = self._input_rule(spec)
                field = QLineEdit()
                field.setMaxLength(max_length)
                field.setFixedSize(64 if max_length > 1 else 76, 32)
                field.setAlignment(Qt.AlignmentFlag.AlignCenter)

                if readonly:
                    field.setReadOnly(True)
                    field.setProperty("readOnly", "true")
                    field.setText(value or "—")
                    field.installEventFilter(self)  # 双击进入编辑
                else:
                    field.setValidator(
                        QRegularExpressionValidator(QRegularExpression(pattern))
                    )
                    field.setPlaceholderText(placeholder)
                    field.setText(value or "")
                    field.setProperty("slot", name)
                    field.installEventFilter(self)

            if not readonly:
                self._inputs[name] = field
            self._edit_snapshot[name] = self._slot_value(field)
            self._input_labels[name] = label
            column.addWidget(caption)
            column.addWidget(field, 0, Qt.AlignmentFlag.AlignHCenter)

            self.grid.addWidget(
                tile,
                index // per_row,
                index % per_row + offset,
                Qt.AlignmentFlag.AlignHCenter,
            )

        # 渲染出内容后要把 body 重新显示出来（之前可能因无数据被隐藏）
        self.body.setVisible(not self._collapsed)
        self.body.setMaximumHeight(16777215 if not self._collapsed else 0)

        if center:
            self.grid.addWidget(QWidget(), 0, len(slots) + 1)
            self.grid.setColumnStretch(len(slots) + 1, 1)
        else:
            # 刚好铺满一行时各列等宽平分
            for column_index in range(min(per_row, len(slots))):
                self.grid.setColumnStretch(column_index + offset, 1)
        self.grid.setHorizontalSpacing(2)

    def _make_choice_widget(
        self, name: str, value, readonly: bool, spec: SlotSpec
    ) -> QWidget:
        """图案关卡的一个格子：只读是标签，编辑是可点开的按钮。"""
        choices = spec.choices or {}
        digit = str(value) if value else ""
        text = choices.get(digit)
        widget: QWidget
        if readonly:
            label = QLabel(text or "—")
            label.setObjectName("choiceView")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(88, 32)
            label.setProperty("slot", name)
            label.setProperty("digit", digit)
            label.setToolTip(f"{name} = {digit or '—'}")
            label.installEventFilter(self)  # 双击进入编辑
            widget = label
        else:
            button = QPushButton(text or "请选择")
            button.setObjectName("choiceSlot")
            button.setFixedSize(88, 32)
            button.setProperty("slot", name)
            button.setProperty("digit", digit)
            button.setToolTip(f"{name}：点击选择图案")
            button.clicked.connect(lambda _=False, n=name: self._open_picker(n))
            button.installEventFilter(self)
            widget = button
        return widget

    def _input_rule(self, spec: SlotSpec) -> tuple[int, str, str]:
        """返回一个格子的 (maxLength, 输入正则, 占位符)。"""
        length = int(spec.length or 1)
        if spec.kind == "letter":
            low = str(spec.min or "A").upper()
            high = str(spec.max or "Z").upper()
            chars = f"{low}-{high}{low.lower()}-{high.lower()}"
            return length, rf"[{chars}]{{0,{length}}}", low
        if spec.kind == "number":
            hint = ("0" * (length - 1) + "1") if length > 0 else "0"
            return length, rf"\d{{0,{length}}}", hint
        return length, rf"\d{{0,{length}}}", "---"

    def _normalize_spec_value(self, name: str, text: str) -> str:
        """按 slot 配置归一化一个格子的值；不合法时抛 ValueError。"""
        spec = self._slot_specs.get(name)
        if spec is None:
            raise ValueError("槽位没有配置")
        value = text.strip()
        if not value:
            return ""
        length = int(spec.length or 1)
        if spec.kind == "letter":
            value = value.upper()
            low = str(spec.min or "A").upper()
            high = str(spec.max or "Z").upper()
            if len(value) != length or not all(low <= ch <= high for ch in value):
                raise ValueError(f"请输入 {length} 位 {low}-{high} 的字母")
            return value
        if spec.kind == "number":
            if not value.isdigit():
                raise ValueError("请输入数字")
            if len(value) > length:
                raise ValueError(f"最多 {length} 位数字")
            number = int(value)
            low = int(spec.min if spec.min is not None else 0)
            high = int(spec.max if spec.max is not None else 10**length - 1)
            if not (low <= number <= high):
                raise ValueError(f"数字需要在 {low}-{high} 之间")
            if spec.pad:
                return f"{number:0{length}d}"
            if len(value) != length:
                raise ValueError(f"需要 {length} 位数字")
            return value
        return value

    def _slot_value(self, widget: QWidget) -> str:
        """取一个格子的当前值：图案按钮取 digit，普通输入框取文本。"""
        if isinstance(widget, QPushButton):
            return str(widget.property("digit") or "")
        if isinstance(widget, QLabel):
            return str(widget.property("digit") or "")
        return widget.text().strip()  # type: ignore[attr-defined]

    def _is_changed(self) -> bool:
        return any(
            self._slot_value(widget) != self._edit_snapshot.get(name, "")
            for name, widget in self._inputs.items()
        )

    def _open_picker(self, name: str) -> None:
        anchor = self._inputs.get(name)
        spec = self._slot_specs.get(name)
        choices = (spec.choices if spec else None) or {}
        if anchor is None or not choices:
            return
        if self._picker is not None:
            self._picker.close()
        self._picker = ChoicePopup(choices, str(anchor.property("digit") or ""), self)
        self._picker.chosen.connect(lambda digit, n=name: self._pick_digit(n, digit))
        self._picker.show_near(anchor)

    def _pick_digit(self, name: str, digit: str) -> None:
        widget = self._inputs.get(name)
        if widget is None:
            return
        widget.setProperty("digit", digit)
        if isinstance(widget, QPushButton):
            spec = self._slot_specs.get(name)
            choices = (spec.choices if spec else None) or {}
            widget.setText(choices.get(digit, digit))
        if self._picker is not None:
            self._picker = None

        names = list(self._inputs)
        index = names.index(name)
        if index < len(names) - 1:
            self._inputs[names[index + 1]].setFocus()
            return
        # 最后一个位置选完：全部有值且有改动时直接保存，和 Enter 的行为一致
        if all(self._slot_value(widget) for widget in self._inputs.values()) and self._is_changed():
            self.save()

    def _refit_width(self) -> None:
        # FPI_MT02 编辑时按钮文字会变长（房间名），但不应因此把窗口撑宽；
        # 宽度在只读刷新时已经确定，编辑期间保持稳定。
        if self._editing and self._trial == fpi_mt02.TRIAL_ID:
            return
        layout = self.layout()
        if layout is None:
            return
        hint = layout.sizeHint()
        width = max(MIN_WINDOW_WIDTH, min(MAX_WINDOW_WIDTH, hint.width() + 4))
        if width != self.width():
            self.setFixedWidth(width)
        if hint.height() != self.height():
            self.setFixedHeight(hint.height())
        self.adjustSize()

    def toggle_collapse(self) -> None:
        """折叠后只留顶栏，顶栏内容仍按原有规则渲染。"""
        self._collapsed = not self._collapsed
        self.collapse_button.setText("展开" if self._collapsed else "折叠")
        self.collapse_button.setToolTip("恢复数据区" if self._collapsed else "只保留顶栏")
        self._apply_collapsed()

    def _body_has_content(self) -> bool:
        if self._fpi_view is not None:
            return True
        return self.grid.count() > 0

    def _apply_collapsed(self) -> None:
        show_body = (not self._collapsed) and self._body_has_content()
        self.body.setVisible(show_body)
        # 折叠时把 body 最大高度压成 0，避免残留高度把面板拉长
        self.body.setMaximumHeight(16777215 if show_body else 0)
        # 显隐切换后布局会缓存旧尺寸，强制失效并在下一轮事件循环重算，
        # 否则折叠/展开几次后窗口会被拉长
        self.body.updateGeometry()
        body_layout = self.body.layout()
        if body_layout is not None:
            body_layout.invalidate()
        for widget in self.body.findChildren(QWidget):
            widget.updateGeometry()
        self.footer.setVisible(True)  # 折叠按钮就在这一行，不能隐藏
        self.save_button.setVisible(self._editing and not self._collapsed)
        self.cancel_button.setVisible(self._editing and not self._collapsed)
        # 折叠时底边栏只保留折叠按钮
        self.hint.setVisible(not self._collapsed)
        if self._collapsed:
            self.update_bar.setVisible(False)
        self._fit()
        QTimer.singleShot(0, self._fit)

    def _on_return(self, name: str) -> None:
        """Enter：图案槽打开选择面板；其它槽跳到下一个框。"""
        spec = self._slot_specs.get(name)
        if spec is not None and spec.kind == "pattern":
            self._open_picker(name)
            return

        names = list(self._inputs)
        index = names.index(name)

        if index < len(names) - 1:
            self._inputs[names[index + 1]].setFocus()
            return

        filled = all(self._slot_value(field) for field in self._inputs.values())
        if filled and self._is_changed():
            self.save()
            return

        # 没填满就回到第一个空框，不保存
        empty = next((f for f in self._inputs.values() if not self._slot_value(f)), None)
        if empty is not None:
            empty.setFocus()

    def _force_save(self) -> None:
        """Ctrl+Enter：允许空值，用于清空某个字段的场景。"""
        if self._editing:
            self.save()

    def _set_hint(self, text: str, error: bool = False, auto_hide_ms: int = 3000) -> None:
        """提示文字：成功绿色、出错红色，几秒后自动消失。

        消失时直接刷新界面（而不是手工清空控件），
        这样界面状态永远来自数据库，不会出现“提示没了但显示还是旧的”。
        """
        self._hint_token += 1
        token = self._hint_token

        self.hint.setText(text)
        colour = "#ff8a80" if error else "#8fe3a0"
        self.hint.setStyleSheet(f"color: {colour};")
        self.hint.setVisible(bool(text) and not self._collapsed)

        if text and auto_hide_ms:
            QTimer.singleShot(auto_hide_ms, lambda: self._expire_hint(token))
        self._fit()

    def _expire_hint(self, token: int) -> None:
        """提示到期：没在编辑就整体刷新，正在编辑则只清掉文字（别冲掉用户输入）。"""
        if token != self._hint_token:
            return
        if self._editing:
            self.hint.setText("")
            self.hint.setVisible(False)
            self._fit()
            return
        self.refresh(force=True)

    def _set_editing(self, editing: bool, focus: bool = False) -> None:
        self._editing = editing
        self.footer.setVisible(True)
        self.save_button.setVisible(editing and not self._collapsed)
        self.cancel_button.setVisible(editing and not self._collapsed)
        if self._fpi_view is not None:
            self._fpi_view.set_readonly(not editing)
            if editing and focus:
                self._fpi_view.focus_first()
        if editing and focus:
            # 只有用户双击进入编辑时才抢焦点，自动刷新时绝不抢，
            # 否则会把游戏窗口的焦点顶掉（表现为切回游戏卡一下、鼠标跑到悬浮窗）
            first = next(iter(self._inputs.values()), None)
            if first is not None:
                first.setFocus()

    def _fit(self) -> None:
        """按内容重算窗口尺寸。

        必须先 activate() 让布局立刻重新计算，否则 adjustSize() 用的是旧尺寸：
        清空中部后窗口仍停在原高度，看起来就像"没刷新"。
        """
        # invalidate + activate 才会让 sizeHint 立刻反映新内容；
        # 再补一次下一轮事件循环的 adjustSize，处理布局延迟生效的情况
        # 子布局也要失效，否则 footer/提示条隐藏后它们的旧尺寸仍被缓存
        for child in (self.body, self.footer, self.update_bar):
            child_layout = child.layout()
            if child_layout is not None:
                child_layout.invalidate()
        self.grid.invalidate()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
            # 宽度跟着内容走：格子少的关卡不会两边空一大截
            hint = layout.sizeHint()
            width = max(MIN_WINDOW_WIDTH, min(MAX_WINDOW_WIDTH, hint.width() + 4))
            if width != self.width():
                self.setFixedWidth(width)
            # 高度也直接定死：adjustSize() 在显隐切换后经常算不准，
            # 会出现折叠/展开来回切换时面板被拉长
            if hint.height() != self.height():
                self.setFixedHeight(hint.height())
        self.adjustSize()
        self.update()
        QTimer.singleShot(0, self._refit_width)

    # ---------- 数据同步 ----------
    def sync_now(self) -> None:
        """手动同步一次，即使没有任何变化也给出回执。"""
        self._force_notice = True
        self.check_updates()

    def check_updates(self) -> None:
        """启动时后台检查一次；网络或解析失败静默忽略。"""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = UpdateWorker(DB_PATH, self)
        self._worker.done.connect(self.on_update_checked)
        self._worker.start()

    def on_update_checked(self, report) -> None:
        self._worker = None
        if report is None:
            return  # 没变化或请求失败：一个像素都不打扰

        if report.refused:
            self._show_notice(f"远端数据异常，已跳过（{report.reason}）", 8000)
            return

        parts: list[str] = []
        if report.inserted:
            parts.append(f"新增 {report.inserted} 条")
        if report.meta_updated:
            parts.append(f"配置 {report.meta_updated} 项")
        if report.new_trials:
            parts.append(f"新建空表 {len(report.new_trials)} 个")

        if report.conflicts:
            # 冲突文件每次都刷新，但同一批冲突只提示一次
            updater.write_conflicts(DB_PATH, report.conflicts)
            signature = updater.conflict_signature(report.conflicts)
            if signature != updater.acked_conflicts(DB_PATH):
                updater.ack_conflicts(DB_PATH, signature)
                parts.append(f"冲突 {len(report.conflicts)} 处（看 conflicts.txt）")

        if not parts:
            if getattr(self, "_force_notice", False):
                self._show_notice("数据已是最新", 3000)
            self._force_notice = False
            return  # 例行检查无事发生

        self._force_notice = False
        self._show_notice("同步完成：" + "、".join(parts), 8000)
        if report.inserted or report.meta_updated or report.new_trials:
            self.refresh(force=True)

    def _show_notice(self, text: str, auto_hide_ms: int = 0) -> None:
        self.update_label.setText(text)
        self.update_bar.setVisible(True)
        self._fit()
        if auto_hide_ms:
            QTimer.singleShot(auto_hide_ms, self._auto_hide_update)

    def _auto_hide_update(self) -> None:
        self.update_bar.setVisible(False)
        self._fit()

    def dismiss_update(self) -> None:
        self._auto_hide_update()

    # ---------- 编辑 ----------
    def eventFilter(self, obj, event):
        """只读态双击进入编辑；编辑态用 空格/Enter 跳转或保存。"""
        if event.type() == QEvent.Type.MouseButtonDblClick and not self._editing:
            self.begin_edit()
            return True

        if event.type() == QEvent.Type.KeyPress and self._editing:
            name = obj.property("slot")
            if name is None:
                return super().eventFilter(obj, event)

            key = event.key()
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            # Ctrl+Enter / Ctrl+空格 = 强制保存（允许空值）
            if ctrl and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._force_save()
                return True

            spec = self._slot_specs.get(str(name))
            choices = (spec.choices if spec else None) or {}
            if spec is not None and spec.kind == "pattern" and isinstance(obj, QPushButton):
                text = event.text()
                if text in choices:
                    self._pick_digit(str(name), text)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._open_picker(str(name))
                    return True
                return super().eventFilter(obj, event)

            is_advance = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
            if not is_advance:
                return super().eventFilter(obj, event)

            self._on_return(str(name))
            return True

        return super().eventFilter(obj, event)

    def begin_edit(self) -> None:
        if self._trial == fpi_mt02.TRIAL_ID:
            if self._fpi_view is None:
                return
            self._set_editing(True, focus=True)
            self._fit()
            return
        info = describe_trial(self._trial) if self._trial else None
        if info is None or not info["slots"]:
            return
        codes = read_codes(self._seed, trial_id=self._trial) if self._seed else None
        self._editing_existing = codes is not None
        self._clear_body()
        self._render_rows(info, codes or {}, readonly=False)
        self._set_editing(True, focus=True)
        self._set_hint("")
        self._fit()

    def cancel_edit(self) -> None:
        self._editing_existing = False
        self._set_hint("")
        self.refresh(force=True)

    # ---------- 保存 ----------
    def save(self) -> None:
        if self._trial is None or self._seed is None:
            return

        if self._trial == fpi_mt02.TRIAL_ID:
            self._save_fpi_mt02()
            return

        codes: dict[str, str | None] = {}
        for name, field in self._inputs.items():
            text = self._slot_value(field)
            label = getattr(self, "_input_labels", {}).get(name, name)
            spec = self._slot_specs.get(name)
            if spec is None:
                self._set_hint(f"{label} 没有配置", error=True)
                return
            if spec.kind == "pattern":
                choices = spec.choices or {}
                if text and text not in choices:
                    self._set_hint(f"{label} 的图案值不合法", error=True)
                    return
            else:
                try:
                    text = self._normalize_spec_value(name, text)
                except ValueError as exc:
                    self._set_hint(f"{label}：{exc}", error=True)
                    return
            codes[name] = text or None

        if not any(codes.values()) and not self._editing_existing:
            self._set_hint("至少填一个图案", error=True)
            return

        try:
            save_codes(self._seed, codes, trial_id=self._trial)
        except (OSError, ValueError) as exc:
            logger.exception("保存失败")
            self._set_hint(f"保存失败: {exc}", error=True)
            return

        self._editing_existing = False
        # 立刻按库里读回来的值重建，避免界面显示"看起来成功"的假象
        self.refresh(force=True)
        self._set_hint("已保存")

    def _save_fpi_mt02(self) -> None:
        if self._fpi_view is None or self._seed is None:
            return
        try:
            entries = self._fpi_view.entries()
            fpi_mt02.save_turns(self._seed, entries)
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.exception("FPI_MT02 保存失败")
            self._set_hint(f"保存失败: {exc}", error=True)
            return

        self._editing_existing = False
        self.refresh(force=True)
        self._set_hint("已保存")

    # ---------- 窗口行为 ----------
    def changeEvent(self, event) -> None:
        """窗口激活状态变化时切换光标：失焦时让指针在面板上不可见。

        游戏里指针是隐藏的，悬浮窗一旦显示默认箭头，看着就像多出来一个鼠标。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            # 图案下拉/房间弹层也是独立 popup，打开时会让主窗短暂失活；
            # 这时不要整棵控件树重设光标，否则 footer 会跟着重绘闪一下。
            if QApplication.activePopupWidget() is not None:
                return
            self._apply_cursor(self._interactive)
            if self._interactive and not self.isActiveWindow():
                # 等激活变化落定后再判断，避免被图案弹窗/模态框误触发
                QTimer.singleShot(0, self._auto_leave_interactive)

    def _auto_leave_interactive(self) -> None:
        """用户点到游戏或别的窗口时，自动回到穿透模式。"""
        if not self._interactive or self.isActiveWindow():
            return
        app = QApplication.instance()
        if app is not None and (
            app.activePopupWidget() is not None or app.activeModalWidget() is not None
        ):
            return
        if self._picker is not None:
            return
        self._set_interactive(False)

    def _apply_cursor(self, active: bool) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor if active else Qt.CursorShape.BlankCursor)
        for widget in [self, *self.findChildren(QWidget)]:
            if not active:
                widget.setCursor(Qt.CursorShape.BlankCursor)
            elif isinstance(widget, ClickableLabel):
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
            elif isinstance(widget, (QPushButton, ChoicePopup)):
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
            elif isinstance(widget, QLineEdit):
                widget.setCursor(Qt.CursorShape.IBeamCursor)
            else:
                widget.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 16, 18, 216))
        painter.drawRoundedRect(self.rect(), 10, 10)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit)
        if not self._placed:
            self._placed = True
            self._move_to_top_center()
        QTimer.singleShot(0, self._init_windows_features)

    def _init_windows_features(self) -> None:
        """首次显示后应用鼠标穿透并注册全局热键。"""
        if self._win_features_ready or sys.platform != "win32" or _user32 is None:
            return
        self._win_features_ready = True
        self._hwnd = int(self.winId())
        self._set_click_through(True)
        self._install_hotkey()

    def _set_click_through(self, enabled: bool) -> None:
        """穿透模式不接收鼠标，也不会被点击激活。"""
        if _user32 is None or not self._hwnd:
            return
        ex_style = _user32.GetWindowLongPtrW(self._hwnd, GWL_EXSTYLE)
        if enabled:
            ex_style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
        else:
            ex_style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            ex_style |= WS_EX_LAYERED
        _user32.SetWindowLongPtrW(self._hwnd, GWL_EXSTYLE, ex_style)
        _user32.SetWindowPos(
            self._hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

    def _install_hotkey(self) -> None:
        if _user32 is None or not self._hwnd or self._hotkey_filter is not None:
            return
        callbacks: dict[int, object] = {}
        failed: list[str] = []
        if _user32.RegisterHotKey(
            self._hwnd, HOTKEY_ID_TOGGLE, MOD_ALT | MOD_NOREPEAT, ord("M")
        ):
            callbacks[HOTKEY_ID_TOGGLE] = self.toggle_interactive
        else:
            failed.append("Alt+M")
        if _user32.RegisterHotKey(
            self._hwnd, HOTKEY_ID_REFRESH, MOD_ALT | MOD_NOREPEAT, ord("N")
        ):
            callbacks[HOTKEY_ID_REFRESH] = self._refresh_from_hotkey
        else:
            failed.append("Alt+N")

        if callbacks:
            app = QApplication.instance()
            if app is not None:
                self._hotkey_ids = set(callbacks)
                self._hotkey_filter = HotkeyFilter(callbacks)
                app.installNativeEventFilter(self._hotkey_filter)
        if failed:
            logger.warning("全局热键注册失败: %s", ", ".join(failed))
            self._set_hint(
                f"热键注册失败：{', '.join(failed)}", error=True, auto_hide_ms=0
            )

    def _remove_hotkey(self) -> None:
        if _user32 is not None and self._hwnd:
            for hotkey_id in list(self._hotkey_ids):
                _user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._hotkey_ids.clear()
        app = QApplication.instance()
        if app is not None and self._hotkey_filter is not None:
            app.removeNativeEventFilter(self._hotkey_filter)
        self._hotkey_filter = None

    def _manual_refresh(self) -> None:
        """按钮、F5、全局 Alt+N 共用的手动刷新，刷新后给一个可见反馈。"""
        self.refresh(force=True)
        self._set_hint("已刷新")

    def _refresh_from_hotkey(self) -> None:
        self._manual_refresh()

    def toggle_interactive(self) -> None:
        if sys.platform != "win32" or _user32 is None:
            return
        self._set_interactive(not self._interactive)

    def _set_interactive(self, interactive: bool) -> None:
        """在“鼠标穿透”和“可交互”之间切换。"""
        self._interactive = interactive
        self._drag_offset = None
        if interactive:
            self._remember_foreground()
            self._set_click_through(False)
            self.show()
            self.raise_()
            self.activateWindow()
            if _user32 is not None and self._hwnd:
                _user32.SetForegroundWindow(self._hwnd)
            self._apply_cursor(True)
            self._move_cursor_into_window()
            if self._editing:
                first = next(iter(self._inputs.values()), None)
                if first is not None:
                    first.setFocus()
            else:
                self.setFocus()
            return

        if self._picker is not None:
            self._picker.close()
            self._picker = None
        self.clearFocus()
        self._set_click_through(True)
        self._apply_cursor(False)
        self._restore_foreground()

    def _move_cursor_into_window(self) -> None:
        """进入交互模式时把指针直接放到悬浮窗中心，避免玩家找不到鼠标。"""
        if not self.isVisible():
            return
        QCursor.setPos(self.mapToGlobal(self.rect().center()))

    def _remember_foreground(self) -> None:
        """进入交互前记住游戏窗口，退出时还回去。"""
        if _user32 is None or not self._hwnd:
            return
        hwnd = _user32.GetForegroundWindow()
        if hwnd and hwnd != self._hwnd:
            self._last_foreground_hwnd = hwnd

    def _restore_foreground(self) -> None:
        """回到穿透时把焦点还给进入交互前的窗口（通常是游戏）。"""
        if _user32 is None or not self._last_foreground_hwnd:
            return
        # 用户已经点到别的窗口时不要再抢回来；只在悬浮窗仍在前台时恢复
        current = _user32.GetForegroundWindow()
        if current and current != self._hwnd:
            return
        hwnd = self._last_foreground_hwnd
        if hwnd == self._hwnd or not _user32.IsWindow(hwnd):
            self._last_foreground_hwnd = 0
            return
        _user32.SetForegroundWindow(hwnd)

    def _move_to_top_center(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + 12)

    def closeEvent(self, event) -> None:
        """点 × 时必须真的退出进程，而不是只隐藏窗口。

        Tool 窗口不会被 Qt 当作"最后一个主窗口"，光 close() 不会触发退出；
        再加上后台可能还有检查更新的线程卡在网络请求上，进程会一直活着。
        """
        self._timer.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.quit()
            if not worker.wait(1500):
                logger.warning("更新线程未按时结束，强制终止")
                worker.terminate()
                worker.wait(500)
        self._worker = None

        self._remove_hotkey()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()


def take_over_instance(app: QApplication) -> QLocalServer:
    """保证只有一个实例：已经有一个在跑就请它退出，然后由本进程接管。

    用本地套接字通知旧实例调用 close()，走的是正常退出流程
    （停定时器、收线程、落数据库），比直接杀进程干净。
    """
    import time

    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(500):
        probe.write(b"quit")
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        # 等旧实例自己走完退出流程，最多 1 秒
        for _ in range(20):
            app.processEvents()
            time.sleep(0.05)
            check = QLocalSocket()
            check.connectToServer(SERVER_NAME)
            connected = check.waitForConnected(80)
            check.disconnectFromServer()
            if not connected:
                break

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)
    return server


def _wire_quit_signal(server: QLocalServer, window: "Overlay") -> None:
    """收到其它实例的 quit 请求就让本实例退出。"""

    def on_new_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                continue
            conn.waitForReadyRead(300)
            message = bytes(conn.readAll()).strip()
            conn.disconnectFromServer()
            if message == b"quit":
                logger.info("收到其它实例的退出请求")
                window.close()

    server.newConnection.connect(on_new_connection)


def _sync_only() -> int:
    """命令行同步一次：不启动界面，直接把结果打印出来。

    本地调试用：python overlay.py --sync-only
    """
    from app_paths import DB_PATH as db_path

    print(f"数据库: {db_path}")
    remote = updater.fetch_remote(db_path)
    if remote is None:
        print("下载失败或远端不可用（详见日志）")
        return 1

    report = updater.merge(remote, db_path)
    remote.unlink(missing_ok=True)

    meta_conflicts = sum(1 for c in report.conflicts if c.slot.startswith("meta."))
    print(
        f"新增 {report.inserted} 条，配置冲突 {meta_conflicts} 项，"
        f"冲突 {len(report.conflicts)} 处"
    )
    if report.conflicts:
        path = updater.write_conflicts(db_path, report.conflicts)
        print(f"冲突已写入: {path}")
        for conflict in report.conflicts[:10]:
            seed_text = "" if conflict.seed is None else f"seed={conflict.seed} "
            print(
                f"  {seed_text}{conflict.slot}: "
                f"本地={conflict.local or '—'} 仓库={conflict.remote or '—'}"
            )
    return 0


def _selftest() -> int:
    """无界面自检：给打包后的 exe 用，结果同时打印并写到 app 目录下。

    窗口化 exe 没有控制台，stdout 看不到，所以必须落文件才能验证。
    """
    from app_paths import APP_DIR, DB_PATH

    app = QApplication(sys.argv[:1])
    window = Overlay()
    window.show()
    window.refresh(force=True)
    app.processEvents()  # 让布局生效，否则量出来的是调整前的尺寸

    lines = [
        f"db: {DB_PATH}",
        f"config: {load_config().get('database')} / poll={load_config().get('poll_seconds')}s",
        f"db_exists: {DB_PATH.is_file()}",
        f"seed: {window.seed_label.text()}",
        f"trial: {window.trial_label.text() or '(无)'}",
        f"rows: {len(window.body.findChildren(QLineEdit)) + len([x for x in window.body.findChildren(QLabel) if x.objectName() == 'choiceView'])}",
        f"choices: {sum(1 for s in window._slot_specs.values() if s.kind == 'pattern')}",
        f"size: {window.width()}x{window.height()}",
    ]
    for line in lines:
        print(line)
    (APP_DIR / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def main() -> None:
    enable_utf8_stdout()

    try:
        problem = None if ensure_database() else "缺少数据库，且包内没有模板库"
    except SystemExit as exc:  # 配置指定的库不存在
        problem = str(exc)

    if problem:
        if "--selftest" in sys.argv:
            raise SystemExit(f"启动失败: {problem}")
        app = QApplication(sys.argv[:1])
        QMessageBox.critical(None, "启动失败", problem)
        raise SystemExit(1)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--sync-only" in sys.argv:
        raise SystemExit(_sync_only())

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    server = take_over_instance(app)
    window = Overlay()
    _wire_quit_signal(server, window)
    window.show()
    window.refresh(force=True)

    # 调试用：--autoclose <毫秒> 让窗口自己关掉，便于验证打包后的退出行为
    for index, arg in enumerate(sys.argv):
        if arg == "--autoclose" and index + 1 < len(sys.argv):
            try:
                QTimer.singleShot(int(sys.argv[index + 1]), window.close)
            except ValueError:
                pass

    code = app.exec()
    window.deleteLater()
    sys.exit(code)


if __name__ == "__main__":
    main()
