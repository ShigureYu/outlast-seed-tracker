"""关卡额外配置的统一对象模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app_paths import trial_info_path


@dataclass(frozen=True)
class Target:
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("target 名称不能为空")


@dataclass(frozen=True)
class Room:
    en: str
    cn: str
    floor_en: str
    floor_cn: str

    def __post_init__(self) -> None:
        if not self.en.strip() or not self.cn.strip():
            raise ValueError("room 的英文名和中文名不能为空")


@dataclass(frozen=True)
class Floor:
    en: str
    cn: str
    rooms: tuple[Room, ...]

    def __post_init__(self) -> None:
        if not self.en.strip() or not self.cn.strip():
            raise ValueError("floor 的英文名和中文名不能为空")
        if not self.rooms:
            raise ValueError(f"floor {self.en} 没有房间")


@dataclass(frozen=True)
class TrialInfo:
    trial_id: str
    targets: tuple[Target, ...]
    floors: tuple[Floor, ...]

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError(f"{self.trial_id} 没有 target")
        if not self.floors:
            raise ValueError(f"{self.trial_id} 没有 floor")

        target_names = [target.name for target in self.targets]
        if len(target_names) != len(set(target_names)):
            raise ValueError(f"{self.trial_id} target 重复")

        floor_names = [floor.en for floor in self.floors]
        if len(floor_names) != len(set(floor_names)):
            raise ValueError(f"{self.trial_id} floor 重复")

        room_names: list[str] = []
        for floor in self.floors:
            for room in floor.rooms:
                if room.floor_en != floor.en:
                    raise ValueError(
                        f"room {room.en} 的 floor 与 {floor.en} 不一致"
                    )
                room_names.append(room.en)
        if len(room_names) != len(set(room_names)):
            raise ValueError(f"{self.trial_id} room 英文名重复")

    @property
    def rooms(self) -> tuple[Room, ...]:
        return tuple(room for floor in self.floors for room in floor.rooms)

    def find_room(self, en: str) -> Room | None:
        for room in self.rooms:
            if room.en == en:
                return room
        return None

    def find_target(self, name: str) -> Target | None:
        for target in self.targets:
            if target.name == name:
                return target
        return None

    def floor_for_room(self, room: Room) -> Floor | None:
        for floor in self.floors:
            if floor.en == room.floor_en:
                return floor
        return None


def load_trial_info(trial_id: str, path: Path | None = None) -> TrialInfo:
    path = path or trial_info_path(trial_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"找不到关卡配置: {path}") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"读取关卡配置失败 {path}: {exc}") from None

    if not isinstance(raw, dict):
        raise ValueError(f"关卡配置顶层不是对象: {path}")

    raw_targets = raw.get("target")
    raw_locations = raw.get("location")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError(f"{path} 缺少 target 数组")
    if not isinstance(raw_locations, list) or not raw_locations:
        raise ValueError(f"{path} 缺少 location 数组")

    targets = tuple(Target(str(name).strip()) for name in raw_targets)
    floors: list[Floor] = []
    for raw_floor in raw_locations:
        if not isinstance(raw_floor, dict):
            raise ValueError(f"{path} 的 floor 不是对象")
        floor_en = str(raw_floor.get("floor_en") or "").strip()
        floor_cn = str(raw_floor.get("floor_cn") or "").strip()
        raw_rooms = raw_floor.get("rooms")
        if not isinstance(raw_rooms, list):
            raise ValueError(f"{path} 的 floor {floor_en} 缺少 rooms")
        rooms = tuple(
            Room(
                en=str(raw_room.get("room_en") or "").strip(),
                cn=str(raw_room.get("room_cn") or "").strip(),
                floor_en=floor_en,
                floor_cn=floor_cn,
            )
            for raw_room in raw_rooms
            if isinstance(raw_room, dict)
        )
        floors.append(Floor(en=floor_en, cn=floor_cn, rooms=rooms))

    return TrialInfo(
        trial_id=trial_id,
        targets=targets,
        floors=tuple(floors),
    )


__all__ = [
    "Floor",
    "Room",
    "Target",
    "TrialInfo",
    "load_trial_info",
]
