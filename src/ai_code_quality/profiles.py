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
    lint_policy: str | None
    minimum_coverage_percent: float | None

    @property
    def enabled(self) -> bool:
        return self.max_duplication_percent is not None and self.max_ccn is not None


def _profile(
    name: str,
    duplication: float,
    ccn: int,
    function_length: int,
    parameters: int,
    *,
    coverage: float,
    existing_policy: str | None,
) -> Profile:
    return Profile(
        name=name,
        max_duplication_percent=duplication,
        max_ccn=ccn,
        max_function_length=function_length,
        max_parameters=parameters,
        semgrep_policy=existing_policy,
        yamllint_policy=existing_policy,
        markdownlint_policy=existing_policy,
        typos_enabled=True,
        lint_policy=name,
        minimum_coverage_percent=coverage,
    )


_LEVELS: Final[dict[str, Profile]] = {
    "none": Profile(
        "none", None, None, None, None, None, None, None, False, None, None
    ),
    "minimal": _profile(
        "minimal", 20.0, 30, 200, 10, coverage=20.0, existing_policy=None
    ),
    "basic": _profile(
        "basic", 15.0, 20, 150, 9, coverage=40.0, existing_policy="basic"
    ),
    "standard": _profile(
        "standard", 10.0, 15, 100, 7, coverage=60.0, existing_policy="standard"
    ),
    "strict": _profile(
        "strict", 0.0, 10, 75, 6, coverage=80.0, existing_policy="strict"
    ),
    "hardened": _profile(
        "hardened", 0.0, 8, 60, 5, coverage=90.0, existing_policy="hardened"
    ),
    "maximum": _profile(
        "maximum", 0.0, 5, 40, 4, coverage=95.0, existing_policy="maximum"
    ),
}

LEVELS: Final[Mapping[str, Profile]] = MappingProxyType(_LEVELS)


def get_profile(name: str) -> Profile:
    normalized = name.strip().lower()
    try:
        return LEVELS[normalized]
    except KeyError as exc:
        supported = ", ".join(LEVELS)
        raise ValueError(f"Unknown quality level {name!r}. Supported levels: {supported}") from exc
