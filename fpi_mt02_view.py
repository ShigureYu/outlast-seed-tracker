"""FPI_MT02 的专用多轮录入控件。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPalette, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fpi_mt02 import TURNS, Entry
from trial_info import Room, Target, TrialInfo


class FloorRoomPicker(QWidget):
    """复合下拉：第一列楼层，第二列房间；按钮只显示房间名。"""

    roomChanged = pyqtSignal(object)

    def __init__(self, info: TrialInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._room: Room | None = None
        self._readonly = False
        self._unavailable_rooms: set[str] = set()

        self.button = QPushButton("选择房间")
        self.button.setObjectName("fpiPicker")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.button.clicked.connect(self._show_popup)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)

        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName("fpiPopup")
        self.popup.setFrameShape(QFrame.Shape.StyledPanel)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup_layout = QHBoxLayout(self.popup)
        popup_layout.setContentsMargins(6, 6, 6, 6)
        popup_layout.setSpacing(6)

        self.floor_list = QListWidget()
        self.floor_list.setObjectName("fpiFloorList")
        floor_metrics = self.floor_list.fontMetrics()
        self.floor_list.setFixedWidth(
            max(floor_metrics.horizontalAdvance(floor.cn) for floor in info.floors) + 34
        )
        for floor in info.floors:
            item = QListWidgetItem(floor.cn)
            item.setData(Qt.ItemDataRole.UserRole, floor)
            self.floor_list.addItem(item)
        self.floor_list.currentRowChanged.connect(self._load_rooms)

        self.room_list = QListWidget()
        self.room_list.setObjectName("fpiRoomList")
        room_metrics = self.room_list.fontMetrics()
        self.room_list.setFixedWidth(
            max(room_metrics.horizontalAdvance(room.cn) for room in info.rooms) + 40
        )
        self.room_list.itemClicked.connect(self._choose_room)
        self.room_list.itemDoubleClicked.connect(self._choose_room)

        popup_layout.addWidget(self.floor_list)
        popup_layout.addWidget(self.room_list)
        self.popup.hide()

    def room(self) -> Room | None:
        return self._room

    def set_room(self, room: Room | None) -> None:
        self._room = room
        self.button.setText(room.cn if room else "选择房间")
        if room is not None and not self._readonly:
            self._select_floor(room)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self.button.setEnabled(not readonly)
        self.button.setText(
            (self._room.cn if self._room else "选择房间") if not readonly
            else (self._room.cn if self._room else "—")
        )
        if readonly:
            self.popup.hide()

    def _select_floor(self, room: Room) -> None:
        for index in range(self.floor_list.count()):
            floor = self.floor_list.item(index).data(Qt.ItemDataRole.UserRole)
            if floor.en == room.floor_en:
                self.floor_list.setCurrentRow(index)
                self._load_rooms(index)
                for room_index in range(self.room_list.count()):
                    item = self.room_list.item(room_index)
                    candidate = item.data(Qt.ItemDataRole.UserRole)
                    if candidate.en == room.en:
                        self.room_list.setCurrentRow(room_index)
                        break
                break

    def _load_rooms(self, row: int) -> None:
        self.room_list.clear()
        if row < 0:
            return
        floor = self.floor_list.item(row).data(Qt.ItemDataRole.UserRole)
        for room in floor.rooms:
            item = QListWidgetItem(room.cn)
            item.setData(Qt.ItemDataRole.UserRole, room)
            if room.en in self._unavailable_rooms:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(QColor("#6f747e"))
                item.setToolTip("该房间在本轮已被其他行选择，不能重复")
            self.room_list.addItem(item)

    def _show_popup(self) -> None:
        if self._readonly:
            return
        if self._room is not None:
            self._select_floor(self._room)
        elif self.floor_list.currentRow() < 0:
            self.floor_list.setCurrentRow(0)
        else:
            self._load_rooms(self.floor_list.currentRow())
        self.popup.adjustSize()
        pos = self.button.mapToGlobal(QPoint(0, self.button.height()))
        self.popup.move(pos)
        self.popup.show()
        self.floor_list.setFocus()

    def _choose_room(self, item: QListWidgetItem) -> None:
        if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        room = item.data(Qt.ItemDataRole.UserRole)
        if room is None:
            return
        self.set_room(room)
        self.popup.hide()
        self.roomChanged.emit(room)

    def set_unavailable_rooms(self, room_names: set[str]) -> None:
        self._unavailable_rooms = set(room_names)
        if self.popup.isVisible() and self.floor_list.currentRow() >= 0:
            self._load_rooms(self.floor_list.currentRow())


class PatternPicker(QWidget):
    """图案下拉：自绘弹层，避免 QComboBox 原生 popup 造成 footer 闪烁。"""

    targetChanged = pyqtSignal(object)

    def __init__(self, info: TrialInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._target: Target | None = None
        self._readonly = False

        self.button = QPushButton("选择图案")
        self.button.setObjectName("fpiPattern")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.button.setFixedHeight(30)
        self.button.clicked.connect(self._show_popup)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)

        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName("fpiPatternPopup")
        self.popup.setFrameShape(QFrame.Shape.StyledPanel)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(4, 4, 4, 4)

        self.list = QListWidget()
        self.list.setObjectName("fpiPatternList")
        self.list.setFixedWidth(140)
        for target in info.targets:
            item = QListWidgetItem(target.name)
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.list.addItem(item)
        self.list.itemClicked.connect(self._choose_target)
        popup_layout.addWidget(self.list)
        # 弹层跟着列表宽度走，避免滚动条和右侧边框之间出现大片空白
        self.popup.setFixedWidth(self.list.width() + 10)
        self.popup.hide()

    def target(self) -> Target | None:
        return self._target

    def set_target(self, target: Target | None) -> None:
        self._target = target
        self.button.setText(target.name if target else "选择图案")
        if target is not None:
            for row in range(self.list.count()):
                item = self.list.item(row)
                candidate = item.data(Qt.ItemDataRole.UserRole)
                if candidate.name == target.name:
                    self.list.setCurrentRow(row)
                    break

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self.button.setEnabled(not readonly)
        self.button.setText(
            (self._target.name if self._target else "选择图案") if not readonly
            else (self._target.name if self._target else "—")
        )
        if readonly:
            self.popup.hide()

    def _show_popup(self) -> None:
        if self._readonly:
            return
        self.popup.adjustSize()
        pos = self.button.mapToGlobal(QPoint(0, self.button.height()))
        self.popup.move(pos)
        self.popup.show()
        self.list.setFocus()

    def open_popup(self) -> None:
        self._show_popup()
        if self.list.count() == 0:
            return
        first = self.list.item(0)
        self.list.scrollToItem(first)
        point = self.list.viewport().mapToGlobal(
            self.list.visualItemRect(first).center()
        )
        QCursor.setPos(point)

    def _choose_target(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target is None:
            return
        self.set_target(target)
        self.popup.hide()
        self.targetChanged.emit(target)


class EntryRow(QWidget):
    editRequested = pyqtSignal()
    roomChanged = pyqtSignal(object)

    def __init__(self, info: TrialInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._readonly = False
        self.setObjectName("fpiRow")

        self.picker = FloorRoomPicker(info)
        self.picker.roomChanged.connect(self._on_room_changed)
        self.picker.roomChanged.connect(self.roomChanged.emit)
        self.pattern = PatternPicker(info)

        self.number = QLineEdit()
        self.number.setObjectName("fpiNumber")
        self.number.setPlaceholderText("1-9")
        self.number.setMaxLength(1)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_palette = self.number.palette()
        placeholder_color = QColor("#8b909a")
        placeholder_palette.setColor(
            QPalette.ColorRole.PlaceholderText, placeholder_color
        )
        placeholder_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.PlaceholderText,
            placeholder_color,
        )
        self.number.setPalette(placeholder_palette)
        self.number.setValidator(
            QRegularExpressionValidator(QRegularExpression("[1-9]"), self.number)
        )
        for widget in (self.picker.button, self.pattern.button, self.number):
            widget.setFixedHeight(30)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.picker, 4)
        layout.addWidget(self.pattern, 3)
        layout.addWidget(self.number, 2)

        for widget in (
            self.picker,
            self.picker.button,
            self.pattern,
            self.pattern.button,
            self.number,
        ):
            widget.installEventFilter(self)

    def _on_room_changed(self, _room: Room) -> None:
        # 先把房间/图案都录完，数字按实际查找顺序逐个补，不自动跳数字。
        if not self._readonly:
            self.pattern.open_popup()

    def set_entry(self, entry: Entry | None) -> None:
        self.picker.set_room(entry.room if entry else None)
        self.pattern.set_target(entry.target if entry else None)
        self.number.setText(entry.number if entry else "")

    def entry(self) -> Entry | None:
        room = self.picker.room()
        target = self.pattern.target()
        number = self.number.text().strip()
        filled = [room is not None, target is not None, bool(number)]
        if not any(filled):
            return None
        if not all(filled):
            raise ValueError("请完整选择房间、图案和数字")
        if not isinstance(room, Room) or not isinstance(target, Target):
            raise ValueError("房间或图案数据无效")
        return Entry(room=room, target=target, number=number)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self.picker.set_readonly(readonly)
        self.pattern.set_readonly(readonly)
        self.number.setReadOnly(readonly)
        self.number.setEnabled(not readonly)
        # 空值只读时用灰色占位显示“—”，绿色只表示已有数据。
        self.number.setPlaceholderText(
            "—" if readonly and not self.number.text() else ("1-9" if not readonly else "")
        )
        if readonly and not self.number.text():
            self.number.setStyleSheet(
                "QLineEdit#fpiNumber:disabled { color: #8b909a; }"
            )
        else:
            self.number.setStyleSheet("")

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.editRequested.emit()
            return True
        return super().eventFilter(obj, event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.editRequested.emit()
        event.accept()


class FPI_MT02View(QWidget):
    editRequested = pyqtSignal()

    HEADERS = ("房间", "图案", "数字")

    def __init__(self, info: TrialInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._rows: dict[int, list[EntryRow]] = {}
        self.tabs = QTabWidget()
        self.tabs.setObjectName("fpiTabs")
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tab_bar = self.tabs.tabBar()
        tab_bar.setExpanding(True)
        tab_bar.setUsesScrollButtons(False)

        for turn in TURNS:
            tab = QWidget()
            tab.setObjectName("fpiTabPage")
            grid = QGridLayout(tab)
            grid.setContentsMargins(0, 4, 0, 4)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(0, 1)

            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(6)
            for stretch, text in zip((4, 3, 2), self.HEADERS):
                label = QLabel(text)
                label.setObjectName("slot")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                header_layout.addWidget(label, stretch)
            grid.addWidget(header, 0, 0, 1, 3)

            rows: list[EntryRow] = []
            for row_index in range(4):
                row = EntryRow(info)
                row.editRequested.connect(self.editRequested)
                row.roomChanged.connect(
                    lambda _room, current_turn=turn: self._refresh_room_availability(
                        current_turn
                    )
                )
                rows.append(row)
                grid.addWidget(row, row_index + 1, 0, 1, 3)
            self._rows[turn] = rows
            self.tabs.addTab(tab, f"第 {turn} 轮")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

    def _sync_tab_bar_width(self) -> None:
        self.tabs.tabBar().setMinimumWidth(self.tabs.width())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_tab_bar_width()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_tab_bar_width()

    def set_data(self, data: dict[int, tuple[Entry, ...]]) -> None:
        for turn, rows in self._rows.items():
            entries = data.get(turn, ())
            for index, row in enumerate(rows):
                row.set_entry(entries[index] if index < len(entries) else None)
        for turn in self._rows:
            self._refresh_room_availability(turn)

    def _refresh_room_availability(self, turn: int) -> None:
        rows = self._rows.get(turn, [])
        selected = {
            row.picker.room().en
            for row in rows
            if row.picker.room() is not None
        }
        for row in rows:
            own = row.picker.room()
            unavailable = selected - ({own.en} if own is not None else set())
            row.picker.set_unavailable_rooms(unavailable)

    def entries(self) -> dict[int, tuple[Entry, ...]]:
        result: dict[int, tuple[Entry, ...]] = {}
        for turn, rows in self._rows.items():
            values: list[Entry] = []
            for row in rows:
                entry = row.entry()
                if entry is not None:
                    values.append(entry)
            result[turn] = tuple(values)
        return result

    def set_readonly(self, readonly: bool) -> None:
        for rows in self._rows.values():
            for row in rows:
                row.set_readonly(readonly)

    def focus_first(self) -> None:
        # FPI_MT02 不自动聚焦数字框，避免空值时光标一直闪烁。
        return
