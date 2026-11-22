"""FPI_MT02 的专用多轮录入控件。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QComboBox,
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

        self.button = QPushButton("选择房间")
        self.button.setObjectName("fpiPicker")
        self.button.clicked.connect(self._show_popup)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)

        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName("fpiPopup")
        self.popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup_layout = QHBoxLayout(self.popup)
        popup_layout.setContentsMargins(6, 6, 6, 6)
        popup_layout.setSpacing(6)

        self.floor_list = QListWidget()
        self.floor_list.setObjectName("fpiFloorList")
        self.floor_list.setFixedWidth(120)
        for floor in info.floors:
            item = QListWidgetItem(floor.cn)
            item.setData(Qt.ItemDataRole.UserRole, floor)
            self.floor_list.addItem(item)
        self.floor_list.currentRowChanged.connect(self._load_rooms)

        self.room_list = QListWidget()
        self.room_list.setObjectName("fpiRoomList")
        self.room_list.setFixedWidth(170)
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
        room = item.data(Qt.ItemDataRole.UserRole)
        if room is None:
            return
        self.set_room(room)
        self.popup.hide()
        self.roomChanged.emit(room)


class EntryRow(QWidget):
    editRequested = pyqtSignal()

    def __init__(self, info: TrialInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._readonly = False
        self.setObjectName("fpiRow")

        self.picker = FloorRoomPicker(info)
        self.pattern = QComboBox()
        self.pattern.setObjectName("fpiPattern")
        self.pattern.addItem("选择图案", None)
        for target in info.targets:
            self.pattern.addItem(target.name, target)

        self.number = QLineEdit()
        self.number.setObjectName("fpiNumber")
        self.number.setPlaceholderText("1-9")
        self.number.setMaxLength(1)
        self.number.setValidator(
            QRegularExpressionValidator(QRegularExpression("[1-9]"), self.number)
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.picker, 2)
        layout.addWidget(self.pattern, 1)
        layout.addWidget(self.number, 1)

        for widget in (self.picker, self.picker.button, self.pattern, self.number):
            widget.installEventFilter(self)

    def set_entry(self, entry: Entry | None) -> None:
        self.picker.set_room(entry.room if entry else None)
        index = 0
        if entry is not None:
            for i in range(self.pattern.count()):
                target = self.pattern.itemData(i)
                if target is not None and target.name == entry.target.name:
                    index = i
                    break
        self.pattern.setCurrentIndex(index)
        self.number.setText(entry.number if entry else "")

    def entry(self) -> Entry | None:
        room = self.picker.room()
        target = self.pattern.currentData()
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
        self.pattern.setEnabled(not readonly)
        self.number.setReadOnly(readonly)
        self.number.setEnabled(not readonly)

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

        for turn in TURNS:
            tab = QWidget()
            grid = QGridLayout(tab)
            grid.setContentsMargins(0, 4, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(4)
            for column, text in enumerate(self.HEADERS):
                label = QLabel(text)
                label.setObjectName("slot")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(label, 0, column)
            rows: list[EntryRow] = []
            for row_index in range(4):
                row = EntryRow(info)
                row.editRequested.connect(self.editRequested)
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

    def set_data(self, data: dict[int, tuple[Entry, ...]]) -> None:
        for turn, rows in self._rows.items():
            entries = data.get(turn, ())
            for index, row in enumerate(rows):
                row.set_entry(entries[index] if index < len(entries) else None)

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
        first_row = self._rows[TURNS[0]][0]
        first_row.picker.button.setFocus()
