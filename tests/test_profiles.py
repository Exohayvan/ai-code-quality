from __future__ import annotations

import pytest

from ai_code_quality.profiles import LEVELS, get_profile


def test_profiles_match_confirmed_thresholds() -> None:
    expected = {
        "none": (None, None),
        "minimal": (20.0, 30),
        "basic": (15.0, 20),
        "standard": (10.0, 15),
        "strict": (0.0, 10),
        "hardened": (0.0, 8),
        "maximum": (0.0, 5),
    }

    assert list(LEVELS) == list(expected)
    assert {
        name: (profile.max_duplication_percent, profile.max_ccn)
        for name, profile in LEVELS.items()
    } == expected


def test_profiles_never_loosen_as_levels_increase() -> None:
    active = [profile for profile in LEVELS.values() if profile.enabled]

    assert [profile.max_duplication_percent for profile in active] == sorted(
        (profile.max_duplication_percent for profile in active), reverse=True
    )
    assert [profile.max_ccn for profile in active] == sorted(
        (profile.max_ccn for profile in active), reverse=True
    )


def test_unknown_profile_fails_with_supported_names() -> None:
    with pytest.raises(ValueError, match="Supported levels: none, minimal, basic"):
        get_profile("ferocious")
