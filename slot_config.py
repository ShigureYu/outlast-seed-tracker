"""TRIAL_META.input_config 的数据对象：SlotSpec / InputConfig。

JSON 存储格式保持不变：

    {"slots": [...], "choices": {...} | null}

slot 级 choices 非空时覆盖 trial 级 choices。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

KINDS = ("number", "letter", "pattern")


def _text_choices(raw) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {str(k): str(v) for k, v in raw.items()}


@dataclass(frozen=True)
class SlotSpec:
    """一个密码格的配置。"""

    column: str
    label: str
    kind: str
    length: int | None = None
    min: int | str | None = None
    max: int | str | None = None
    pad: bool = True
    choices: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.column or not isinstance(self.column, str):
            raise ValueError("slot.column 必须是非空字符串")
        if not self.label or not isinstance(self.label, str):
            raise ValueError(f"slot {self.column} 缺少 name_zh")
        if self.kind not in KINDS:
            raise ValueError(f"slot {self.column} 的 kind 不合法: {self.kind!r}")

        if self.kind == "pattern":
            if self.choices is not None and not isinstance(self.choices, dict):
                raise ValueError(f"slot {self.column} 的 choices 不是对象")
            object.__setattr__(self, "length", None)
            object.__setattr__(self, "min", None)
            object.__setattr__(self, "max", None)
            object.__setattr__(self, "pad", False)
            return

        if not isinstance(self.length, int) or isinstance(self.length, bool) or self.length < 1:
            raise ValueError(f"slot {self.column} 的 length 不合法: {self.length!r}")

        if self.kind == "number":
            low = self.min if self.min is not None else 0
            high = self.max if self.max is not None else 10**self.length - 1
            if (
                not isinstance(low, int)
                or isinstance(low, bool)
                or not isinstance(high, int)
                or isinstance(high, bool)
                or low > high
            ):
                raise ValueError(f"slot {self.column} 的 min/max 不合法: {low!r}/{high!r}")
            object.__setattr__(self, "min", low)
            object.__setattr__(self, "max", high)
            object.__setattr__(self, "pad", bool(self.pad))
            return

        low = str(self.min or "A").upper()
        high = str(self.max or "Z").upper()
        if len(low) != 1 or len(high) != 1 or low > high:
            raise ValueError(f"slot {self.column} 的字母范围不合法: {low!r}/{high!r}")
        object.__setattr__(self, "min", low)
        object.__setattr__(self, "max", high)
        object.__setattr__(self, "pad", False)

    def to_dict(self) -> dict:
        data = {
            "column": self.column,
            "name_zh": self.label,
            "kind": self.kind,
            "length": self.length,
            "min": self.min,
            "max": self.max,
        }
        if self.kind == "pattern":
            data["choices"] = self.choices
        elif self.kind == "number":
            data["pad"] = self.pad
        return data


@dataclass(frozen=True)
class InputConfig:
    """一关的完整输入配置。"""

    slots: tuple[SlotSpec, ...]
    choices: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("input_config.slots 不能为空")
        if self.choices is not None and not isinstance(self.choices, dict):
            raise ValueError("input_config.choices 不是对象")
        seen: set[str] = set()
        for slot in self.slots:
            if slot.column in seen:
                raise ValueError(f"input_config 列重复: {slot.column}")
            seen.add(slot.column)
            if slot.kind == "pattern" and not (slot.choices or self.choices):
                raise ValueError(f"pattern 槽 {slot.column} 没有 choices")

    def choices_for(self, slot: SlotSpec) -> dict[str, str]:
        """槽内 choices 优先，否则用 trial 级 choices。"""
        return dict(slot.choices or self.choices or {})

    def to_json(self) -> str:
        return json.dumps(
            {"slots": [slot.to_dict() for slot in self.slots], "choices": self.choices},
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str | None) -> InputConfig | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("TRIAL_META.input_config 不是合法 JSON: %r", raw)
            return None
        if not isinstance(parsed, dict):
            logger.warning("TRIAL_META.input_config 顶层不是对象")
            return None

        raw_slots = parsed.get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            logger.warning("TRIAL_META.input_config.slots 不是非空数组")
            return None

        top_choices = _text_choices(parsed.get("choices"))

        try:
            slots: list[SlotSpec] = []
            for item in raw_slots:
                if not isinstance(item, dict):
                    raise ValueError(f"slot 不是对象: {item!r}")
                slots.append(
                    SlotSpec(
                        column=item.get("column"),
                        label=item.get("name_zh") or item.get("column"),
                        kind=str(item.get("kind") or "").lower(),
                        length=item.get("length"),
                        min=item.get("min"),
                        max=item.get("max"),
                        pad=item.get("pad", True),
                        choices=_text_choices(item.get("choices")),
                    )
                )
            return cls(tuple(slots), top_choices)
        except ValueError as exc:
            logger.warning("TRIAL_META.input_config 不合法: %s", exc)
            return None

    def resolved_slots(self) -> list[SlotSpec]:
        """把 pattern 槽的 choices 解析成最终值后返回。"""
        return [
            replace(slot, choices=self.choices_for(slot))
            if slot.kind == "pattern"
            else slot
            for slot in self.slots
        ]


__all__ = ["KINDS", "InputConfig", "SlotSpec"]
