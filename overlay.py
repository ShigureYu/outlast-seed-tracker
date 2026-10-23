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

from PyQt6.QtCore import QPoint, QRegularExpression, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import updater
from app_paths import (
    DB_PATH,
    DEFAULT_DB_NAME,
    enable_utf8_stdout,
    ensure_database,
    load_config,
)
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
BADGE_EXTRA_WIDTH = 60  # 测试库标记占用的额外宽度

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
        self._test_db = DB_PATH.name != DEFAULT_DB_NAME
        # 测试库要额外放一个标记，给它留出宽度，正式库下窗口宽度不变
        self.setFixedWidth(WINDOW_WIDTH + (BADGE_EXTRA_WIDTH if self._test_db else 0))
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
        self.seed_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # 关卡名：夹在 Seed 和按钮中间，只显示中文名
        self.trial_label = QLabel("")
        self.trial_label.setObjectName("trial")
        self.trial_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trial_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.trial_label.setVisible(False)

        self.refresh_button = ClickableLabel("刷新")
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setToolTip("手动刷新（F5）")
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))

        # 用的不是正式库时给个显眼标记，避免忘了自己在沙盒里
        self.db_badge = QLabel("测试")
        self.db_badge.setObjectName("badge")
        self.db_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.db_badge.setToolTip(f"当前数据库：{DB_PATH.name}")
        self.db_badge.setVisible(self._test_db)

        self.close_button = ClickableLabel("×")
        self.close_button.setObjectName("close")
        self.close_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_button.setFixedSize(26, 26)
        self.close_button.setToolTip("关闭（Esc）")
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

        # ---- 数据同步提示（默认隐藏，不占高度）----
        self.update_bar = QWidget(self)
        bar = QHBoxLayout(self.update_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.update_label = QLabel("")
        self.update_label.setObjectName("update")
        self.sync_button = ClickableLabel("同步")
        self.sync_button.setObjectName("refresh")
        self.sync_button.clicked.connect(self.apply_update)
        self.ignore_button = ClickableLabel("忽略")
        self.ignore_button.setObjectName("refresh")
        self.ignore_button.clicked.connect(self.dismiss_update)
        bar.addWidget(self.update_label)
        bar.addStretch(1)
        bar.addWidget(self.sync_button)
        bar.addWidget(self.ignore_button)
        self.update_bar.setVisible(False)
        root.addWidget(self.update_bar)

        # ---- 冲突面板（非模态、限高可滚动，默认隐藏）----
        self.conflict_box = QWidget(self)
        box = QVBoxLayout(self.conflict_box)
        box.setContentsMargins(0, 0, 0, 0)
        self.conflict_scroll = QScrollArea(self.conflict_box)
        self.conflict_scroll.setWidgetResizable(True)
        self.conflict_scroll.setMaximumHeight(260)
        self.conflict_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        self.conflict_layout = QVBoxLayout(inner)
        self.conflict_layout.setContentsMargins(0, 0, 0, 0)
        self.conflict_layout.setSpacing(4)
        self.conflict_scroll.setWidget(inner)
        box.addWidget(self.conflict_scroll)
        self.conflict_box.setVisible(False)
        root.addWidget(self.conflict_box)

        self._conflicts: list = []
        self._choices: dict = {}
        self._worker = None

        self.shortcut = QShortcut(QKeySequence("F5"), self)
        self.shortcut.activated.connect(lambda: self.refresh(force=True))
        self.quit_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.quit_shortcut.activated.connect(self.close)

        poll_seconds = int(load_config().get("poll_seconds") or POLL_MS // 1000)
        self._timer = QTimer(self)
        self._timer.setInterval(max(5, poll_seconds) * 1000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        # 启动 1.5 秒后后台检查一次更新，不阻塞界面
        QTimer.singleShot(1500, self.check_updates)

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

        # 有意显示日志原文（如 58058），而不是还原后的 58：
        # 排查问题时可以直接拿这个数去日志里搜，省掉心算 ×1001 的逆运算。
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

        self._set_trial_text(info["name_zh"] or info["trial_id"])

        if not info["slots"]:
            # 表存在但 meta 没登记任何列：只显示 Seed 和关卡名
            self._set_editing(False)
            self._fit()
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
        """每个图案一个小格：名字在上、数字在下，横向排布。

        以前是"名字贴左、数字贴右"两列布局，420px 宽度里中间空一大截，
        眼睛要来回扫；竖排之后名字和数字天然对齐。
        """
        self._inputs = {}
        slots = info["slots"]
        per_row = 5  # 一行最多 5 个，超过就换行（比如以后某关有 7 个图案）

        for index, (name, label) in enumerate(slots):
            tile = QWidget()
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            column = QVBoxLayout(tile)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)

            caption = QLabel(label)
            caption.setObjectName("slot")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

            field = QLineEdit()
            field.setMaxLength(3)
            field.setFixedWidth(64)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)

            if readonly:
                field.setReadOnly(True)
                field.setProperty("readOnly", "true")
                field.setText(values.get(name) or "—")
            else:
                field.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,3}")))
                field.setPlaceholderText("---")
                self._inputs[name] = field

            column.addWidget(caption)
            column.addWidget(field, 0, Qt.AlignmentFlag.AlignHCenter)

            self.grid.addWidget(
                tile, index // per_row, index % per_row, Qt.AlignmentFlag.AlignHCenter
            )

        # 各列等宽平分，避免所有小格挤在左边
        for column_index in range(min(per_row, len(slots))):
            self.grid.setColumnStretch(column_index, 1)
        self.grid.setHorizontalSpacing(2)

    def _set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.footer.setVisible(editing and not self._collapsed)
        if editing:
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
        self.grid.invalidate()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.adjustSize()
        self.update()
        QTimer.singleShot(0, self.adjustSize)

    # ---------- 数据同步 ----------
    def check_updates(self) -> None:
        """启动时后台检查一次；没有任何网络或解析失败会打扰用户。"""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = UpdateWorker(DB_PATH, self)
        self._worker.done.connect(self.on_update_checked)
        self._worker.start()

    def on_update_checked(self, report) -> None:
        self._worker = None
        if report is None:
            return  # 没变化或失败：界面完全不动

        if report.refused:
            # 远端数据异常：只提示，不给合并入口
            self.update_label.setText(f"远端数据异常，已跳过（{report.reason}）")
            self.sync_button.setVisible(False)
            self.ignore_button.setText("知道了")
            self.update_bar.setVisible(True)
            self._pending_report = None
            self._fit()
            return

        if report.conflicts:
            self.update_label.setText(f"有 {len(report.conflicts)} 处数据不一致")
            self.sync_button.setText("处理")
            self.ignore_button.setVisible(True)
            self._pending_report = report
        else:
            parts = []
            if report.inserted:
                parts.append(f"{report.inserted} 条新记录")
            if report.meta_updated:
                parts.append(f"{report.meta_updated} 项关卡配置")
            self.update_label.setText("已同步 " + "、".join(parts) if parts else "数据已是最新")
            self.sync_button.setText("完成")
            self.ignore_button.setVisible(False)
            self._pending_report = None
            QTimer.singleShot(4000, self._auto_hide_update)
            self.refresh(force=True)

        self.update_bar.setVisible(True)
        self._fit()

    def apply_update(self) -> None:
        report = getattr(self, "_pending_report", None)
        if report is None:
            self.update_bar.setVisible(False)
            self._fit()
            return
        self.show_conflicts(report.conflicts)

    def _auto_hide_update(self) -> None:
        """提示停留几秒后收起，并重新按内容收缩窗口，避免底部留白。"""
        self.update_bar.setVisible(False)
        self._fit()

    def dismiss_update(self) -> None:
        self.update_bar.setVisible(False)
        self.sync_button.setVisible(True)
        self.ignore_button.setText("忽略")
        self._pending_report = None
        self._fit()

    CONFLICT_PAGE = 10
    CONFLICT_BULK_THRESHOLD = 30

    def _clear_conflict_box(self) -> None:
        while self.conflict_layout.count():
            item = self.conflict_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def show_conflicts(self, conflicts) -> None:
        self._conflicts = list(conflicts)
        self._choices = {c.key: True for c in conflicts}  # 默认全部用远端
        self._page = 0

        # 按 seed 聚合：同一 seed 的多个字段合成一行，行数先除以槽位数
        groups: dict[tuple[str, int], list] = {}
        for conflict in conflicts:
            groups.setdefault((conflict.trial_id, conflict.seed), []).append(conflict)
        self._groups = sorted(groups.items(), key=lambda kv: kv[0][1])

        self._clear_conflict_box()
        self.update_bar.setVisible(False)
        self.conflict_box.setVisible(True)

        if len(conflicts) > self.CONFLICT_BULK_THRESHOLD:
            self._render_bulk()
        else:
            self._render_page()
        self._fit()

    def _render_bulk(self) -> None:
        """冲突太多时逐条选没有意义，改成整批决定。"""
        title = QLabel(
            f"检测到 {len(self._conflicts)} 处不一致（{len(self._groups)} 个种子），"
            f"可能不是同一份数据源。\n整批切换，或先保留本地再手工核对。"
        )
        title.setWordWrap(True)
        title.setObjectName("update")
        self.conflict_layout.addWidget(title)

        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        use_remote = QPushButton("全部用远端")
        use_remote.clicked.connect(lambda: self._bulk_apply(True))
        keep_local = QPushButton("全部保留本地")
        keep_local.clicked.connect(lambda: self._bulk_apply(False))
        line.addWidget(use_remote)
        line.addWidget(keep_local)
        line.addStretch(1)
        self.conflict_layout.addWidget(row)

        self.conflict_scroll.setMaximumHeight(120)

    def _render_page(self) -> None:
        total_pages = max(1, -(-len(self._groups) // self.CONFLICT_PAGE))
        start = self._page * self.CONFLICT_PAGE
        page = self._groups[start : start + self.CONFLICT_PAGE]

        title = QLabel(
            f"{len(self._conflicts)} 处不一致 / {len(self._groups)} 个种子"
            f"   第 {self._page + 1}/{total_pages} 页"
        )
        title.setObjectName("update")
        self.conflict_layout.addWidget(title)

        for (_trial, seed), items in page:
            summary = ", ".join(
                f"{c.slot} {c.local or '—'}→{c.remote or '—'}" for c in items[:2]
            )
            if len(items) > 2:
                summary += f" 等 {len(items)} 项"

            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            text = QLabel(f"{seed}   {summary}")
            choice = QPushButton("用远端")
            choice.setObjectName("refresh")
            choice.setCheckable(True)
            choice.setChecked(self._choices.get(items[0].key, True))
            choice.setFixedSize(64, 24)
            choice.toggled.connect(
                lambda checked, group=items: self._set_group(group, checked)
            )
            line.addWidget(text)
            line.addStretch(1)
            line.addWidget(choice)
            self.conflict_layout.addWidget(row)

        nav = QWidget()
        nav_line = QHBoxLayout(nav)
        nav_line.setContentsMargins(0, 0, 0, 0)
        prev_button = QPushButton("上一页")
        prev_button.setEnabled(self._page > 0)
        prev_button.clicked.connect(lambda: self._turn_page(-1))
        next_button = QPushButton("下一页")
        next_button.setEnabled(self._page + 1 < total_pages)
        next_button.clicked.connect(lambda: self._turn_page(1))
        apply_button = QPushButton("应用")
        apply_button.clicked.connect(self.apply_conflicts)
        nav_line.addWidget(prev_button)
        nav_line.addWidget(next_button)
        nav_line.addStretch(1)
        nav_line.addWidget(apply_button)
        self.conflict_layout.addWidget(nav)

        self.conflict_scroll.setMaximumHeight(260)

    def _set_group(self, items, checked: bool) -> None:
        for conflict in items:
            self._choices[conflict.key] = checked

    def _turn_page(self, delta: int) -> None:
        self._page = max(0, self._page + delta)
        self._clear_conflict_box()
        self._render_page()
        self._fit()

    def _bulk_apply(self, use_remote: bool) -> None:
        self._choices = {c.key: use_remote for c in self._conflicts}
        self.apply_conflicts()

    def apply_conflicts(self) -> None:
        if not self._conflicts:
            return
        changed = updater.apply_conflicts(DB_PATH, self._conflicts, self._choices)
        self._conflicts = []
        self._pending_report = None
        self.conflict_box.setVisible(False)
        self.update_label.setText(f"已同步 {changed} 处")
        self.sync_button.setText("完成")
        self.ignore_button.setVisible(False)
        self.update_bar.setVisible(True)
        self.refresh(force=True)
        self._fit()

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

    def closeEvent(self, event) -> None:
        """点 × 或按 Esc 时必须真的退出进程，而不是只隐藏窗口。

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
    app.processEvents()  # 让布局生效，否则量出来的是调整前的尺寸

    lines = [
        f"db: {DB_PATH}",
        f"config: {load_config().get('database')} / poll={load_config().get('poll_seconds')}s",
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

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = Overlay()
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
