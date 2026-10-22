"""无边框、半透明、始终置顶的悬浮窗：显示当前 Seed，能查就查，查不到就录入。

刷新策略：
- 每 60 秒扫一次日志，只有 (Trial ID, Seed) 变了才重建界面，避免打字被冲掉；
- 录入过程中暂停自动刷新；F5 或点 ⟳ 可以手动刷新。

状态（从上到下三个区）：
- 顶部：始终显示 Seed；库里有这张表时，旁边补上「中文名 CHJ_MT02」。
- 中部：表存在 -> 按表结构渲染每个图案一行；有记录则只读，没记录则可编辑。
- 底部：仅"表存在但没记录"时出现保存按钮。
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QPoint, QRegularExpression, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QRegularExpressionValidator,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_paths import enable_utf8_stdout
from log_parser import (
    extract_seed_from_log,
    extract_trial_id_from_log,
    find_latest_log,
    normalize_seed,
    normalize_trial_id,
)
from seed_lookup import describe_trial, read_codes, save_codes

logger = logging.getLogger(__name__)

POLL_MS = 60_000
WINDOW_WIDTH = 420

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
    color: #9fd2ff;
}
QLabel#hint {
    font-size: 14px;
    color: #ff8a80;
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
}
QPushButton {
    background: rgba(255, 255, 255, 36);
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 14px;
}
QPushButton#refresh {
    font-size: 13px;
    padding: 0;
    background: rgba(255, 255, 255, 28);
}
QPushButton#refresh:hover {
    background: rgba(255, 255, 255, 60);
}
QPushButton#close {
    font-size: 18px;
    padding: 0;
    background: transparent;
    color: rgba(255, 255, 255, 150);
}
QPushButton#close:hover {
    background: rgba(220, 70, 70, 190);
    color: #ffffff;
}
QPushButton#refresh {
    font-size: 11px;
    padding: 0;
    background: rgba(255, 255, 255, 28);
}
QPushButton#refresh:hover {
    background: rgba(255, 255, 255, 60);
}
QPushButton#close {
    font-size: 18px;
    padding: 0;
    background: transparent;
    color: rgba(255, 255, 255, 150);
}
QPushButton#close:hover {
    background: rgba(220, 70, 70, 190);
    color: #ffffff;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 60);
}
QPushButton:disabled {
    color: rgba(255, 255, 255, 90);
}
"""


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
        self.setWindowTitle("OPP Overlay")
        self.setFixedWidth(WINDOW_WIDTH)
        self.setStyleSheet(STYLE)

        self._drag_offset: QPoint | None = None
        self._placed = False
        self._collapsed = False
        self._editing = False
        self._last_key: tuple[str | None, str | None] | None = None

        self._trial: str | None = None
        self._seed: str | None = None
        self._inputs: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ---- 顶部状态区：第一行 Seed + 按钮，第二行关卡名 ----
        top = QHBoxLayout()
        top.setSpacing(6)
        self.seed_label = QLabel("Seed: --")
        self.seed_label.setObjectName("seed")

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setFixedSize(52, 26)
        self.refresh_button.setToolTip("手动刷新（F5）")
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("close")
        self.close_button.setFixedSize(26, 26)
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self.close)

        top.addWidget(self.seed_label)
        top.addStretch(1)
        top.addWidget(self.refresh_button)
        top.addWidget(self.close_button)
        root.addLayout(top)

        # 关卡名单独一行：名字长短不会挤动右边的按钮
        self.trial_label = QLabel("")
        self.trial_label.setObjectName("trial")
        self.trial_label.setVisible(False)
        root.addWidget(self.trial_label)

        # ---- 中部数据区 ----
        self.body = QWidget(self)
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        root.addWidget(self.body)

        # ---- 底部操作区 ----
        footer = QHBoxLayout()
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.hint)
        footer.addStretch(1)
        footer.addWidget(self.save_button)
        self.footer = QWidget(self)
        self.footer.setLayout(footer)
        root.addWidget(self.footer)

        self.shortcut = QShortcut(QKeySequence("F5"), self)
        self.shortcut.activated.connect(lambda: self.refresh(force=True))
        self.quit_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.quit_shortcut.activated.connect(self.close)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ---------- 日志 ----------
    @staticmethod
    def _read_log() -> tuple[str | None, str | None]:
        """返回 (原始 Trial ID, 原始 Seed)，都保持日志里的原样。"""
        log_file = find_latest_log()
        if log_file is None:
            return None, None
        return extract_trial_id_from_log(log_file), extract_seed_from_log(log_file)

    # ---------- 刷新 ----------
    def _poll(self) -> None:
        if self._editing:  # 录入中不打断
            return
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        raw_trial, raw_seed = self._read_log()
        trial = normalize_trial_id(raw_trial)
        seed = normalize_seed(raw_seed)
        key = (trial, seed)

        if not force and key == self._last_key and not self._editing:
            return
        self._last_key = key
        self._trial, self._seed = trial, seed

        self.seed_label.setText(f"Seed: {raw_seed or '--'}")
        self.hint.setText("")
        self._clear_body()

        if seed is None:
            # 状态 D：连 seed 都没有，什么都不渲染
            self._set_trial_text("")
            self._set_editing(False)
            self._fit()
            return

        info = describe_trial(trial) if trial else None
        if info is None:
            # 状态 A：库里没有这张表
            self._set_trial_text("")
            self._set_editing(False)
            self._fit()
            return

        title = " ".join(part for part in (info["name_zh"], info["trial_id"]) if part)
        self._set_trial_text(title)

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
        self.trial_label.setVisible(bool(text) and not self._collapsed)

    def _clear_body(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先断开父子关系，让控件立刻从布局和 findChildren 里消失；
                # 只调 deleteLater() 要等下一轮事件循环，期间 adjustSize 会算上旧控件
                widget.setParent(None)
                widget.deleteLater()
        self._inputs = {}

    def _render_rows(
        self,
        info: dict,
        values: dict[str, str | None],
        readonly: bool = False,
    ) -> None:
        self._inputs = {}
        for row, (name, label) in enumerate(info["slots"]):
            caption = QLabel(label)
            field = QLineEdit()
            field.setMaxLength(3)
            field.setFixedWidth(110)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)

            if readonly:
                field.setReadOnly(True)
                field.setProperty("readOnly", "true")
                field.setFixedWidth(80)
                field.setAlignment(Qt.AlignmentFlag.AlignRight)
                field.setText(values.get(name) or "—")
            else:
                field.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,3}")))
                field.setPlaceholderText("---")
                self._inputs[name] = field

            self.grid.addWidget(caption, row, 0)
            self.grid.addWidget(field, row, 1)

        self.grid.setColumnStretch(0, 1)

    def _set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.footer.setVisible(editing and not self._collapsed)
        if editing:
            first = next(iter(self._inputs.values()), None)
            if first is not None:
                first.setFocus()

    def _fit(self) -> None:
        self.adjustSize()

    # ---------- 保存 ----------
    def save(self) -> None:
        if self._trial is None or self._seed is None:
            return

        codes: dict[str, str | None] = {}
        for name, field in self._inputs.items():
            text = field.text().strip()
            if text and len(text) != 3:
                self.hint.setText(f"{name} 需要 3 位数字")
                return
            codes[name] = text or None

        if not any(codes.values()):
            self.hint.setText("至少填一个图案")
            return

        try:
            save_codes(self._seed, codes, trial_id=self._trial)
        except (OSError, ValueError) as exc:
            logger.exception("保存失败")
            self.hint.setText(f"保存失败: {exc}")
            return

        # 立刻按库里读回来的值重建，避免界面显示"看起来成功"的假象
        self.refresh(force=True)
        self.hint.setText("已保存")

    # ---------- 窗口行为 ----------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 16, 18, 216))
        painter.drawRoundedRect(self.rect(), 10, 10)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            self._placed = True
            self._move_to_top_center()

    def _move_to_top_center(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + 12)

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

    def mouseDoubleClickEvent(self, event) -> None:
        """双击折叠/展开，嫌挡视野时用。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._collapsed = not self._collapsed
            self.body.setVisible(not self._collapsed)
            self.trial_label.setVisible(bool(self.trial_label.text()) and not self._collapsed)
            self.footer.setVisible(self._editing and not self._collapsed)
            self._fit()
            event.accept()


def _selftest() -> int:
    """无界面自检：给打包后的 exe 用，结果同时打印并写到 app 目录下。

    窗口化 exe 没有控制台，stdout 看不到，所以必须落文件才能验证。
    """
    from app_paths import APP_DIR, DB_PATH

    app = QApplication(sys.argv[:1])
    window = Overlay()
    window.show()
    window.refresh(force=True)

    lines = [
        f"db: {DB_PATH}",
        f"db_exists: {DB_PATH.is_file()}",
        f"seed: {window.seed_label.text()}",
        f"trial: {window.trial_label.text() or '(无)'}",
        f"rows: {len(window.body.findChildren(QLineEdit))}",
        f"size: {window.width()}x{window.height()}",
    ]
    for line in lines:
        print(line)
    (APP_DIR / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def main() -> None:
    enable_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())

    app = QApplication(sys.argv)
    window = Overlay()
    window.show()
    window.refresh(force=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
