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
    max_function_length: int | None
    max_parameters: int | None
    semgrep_policy: str | None
    yamllint_policy: str | None
    markdownlint_policy: str | None
    typos_enabled: bool

    @property
    def enabled(self) -> bool:
        return self.max_duplication_percent is not None and self.max_ccn is not None


_LEVELS: Final[dict[str, Profile]] = {
    "none": Profile("none", None, None, None, None, None, None, None, False),
    "minimal": Profile("minimal", 20.0, 30, 200, 10, None, None, None, True),
    "basic": Profile("basic", 15.0, 20, 150, 9, "basic", "basic", "basic", True),
    "standard": Profile("standard", 10.0, 15, 100, 7, "standard", "standard", "standard", True),
    "strict": Profile("strict", 0.0, 10, 75, 6, "strict", "strict", "strict", True),
    "hardened": Profile("hardened", 0.0, 8, 60, 5, "hardened", "hardened", "hardened", True),
    "maximum": Profile("maximum", 0.0, 5, 40, 4, "maximum", "maximum", "maximum", True),
}

LEVELS: Final[Mapping[str, Profile]] = MappingProxyType(_LEVELS)


def get_profile(name: str) -> Profile:
    normalized = name.strip().lower()
    try:
        return LEVELS[normalized]
    except KeyError as exc:
        supported = ", ".join(LEVELS)
        raise ValueError(f"Unknown quality level {name!r}. Supported levels: {supported}") from exc
