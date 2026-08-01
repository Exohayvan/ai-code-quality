from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Profile:
    """Immutable thresholds for one quality level."""

    name: str
    max_duplication_percent: float | None
    max_ccn: int | None

    @property
    def enabled(self) -> bool:
        return self.max_duplication_percent is not None and self.max_ccn is not None


_LEVELS: Final[dict[str, Profile]] = {
    "none": Profile("none", None, None),
    "minimal": Profile("minimal", 20.0, 30),
    "basic": Profile("basic", 15.0, 20),
    "standard": Profile("standard", 10.0, 15),
    "strict": Profile("strict", 0.0, 10),
    "hardened": Profile("hardened", 0.0, 8),
    "maximum": Profile("maximum", 0.0, 5),
}

LEVELS: Final[Mapping[str, Profile]] = MappingProxyType(_LEVELS)


def get_profile(name: str) -> Profile:
    normalized = name.strip().lower()
    try:
        return LEVELS[normalized]
    except KeyError as exc:
        supported = ", ".join(LEVELS)
        raise ValueError(f"Unknown quality level {name!r}. Supported levels: {supported}") from exc
