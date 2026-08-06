from __future__ import annotations

import pytest

from ai_code_quality.profiles import LEVELS, get_profile


def test_profiles_match_confirmed_thresholds() -> None:
    expected = {
        "none": (None, None, None, None),
        "minimal": (20.0, 30, 200, 10),
        "basic": (15.0, 20, 150, 9),
        "standard": (10.0, 15, 100, 7),
        "strict": (0.0, 10, 75, 6),
        "hardened": (0.0, 8, 60, 5),
        "maximum": (0.0, 5, 40, 4),
    }

    assert list(LEVELS) == list(expected)
    assert {
        name: (
            profile.max_duplication_percent,
            profile.max_ccn,
            profile.max_function_length,
            profile.max_parameters,
        )
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
    assert [profile.max_function_length for profile in active] == sorted(
        (profile.max_function_length for profile in active), reverse=True
    )
    assert [profile.max_parameters for profile in active] == sorted(
        (profile.max_parameters for profile in active), reverse=True
    )


def test_profiles_enable_the_approved_tool_policy_ladder() -> None:
    assert {
        name: (
            profile.semgrep_policy,
            profile.yamllint_policy,
            profile.markdownlint_policy,
            profile.typos_enabled,
        )
        for name, profile in LEVELS.items()
    } == {
        "none": (None, None, None, False),
        "minimal": (None, None, None, True),
        "basic": ("basic", "basic", "basic", True),
        "standard": ("standard", "standard", "standard", True),
        "strict": ("strict", "strict", "strict", True),
        "hardened": ("hardened", "hardened", "hardened", True),
        "maximum": ("maximum", "maximum", "maximum", True),
    }


def test_profiles_enable_the_confirmed_lint_and_coverage_ladder() -> None:
    assert {
        name: (profile.lint_policy, profile.minimum_coverage_percent)
        for name, profile in LEVELS.items()
    } == {
        "none": (None, None),
        "minimal": ("minimal", 20.0),
        "basic": ("basic", 40.0),
        "standard": ("standard", 60.0),
        "strict": ("strict", 80.0),
        "hardened": ("hardened", 90.0),
        "maximum": ("maximum", 95.0),
    }


def test_lint_and_coverage_profiles_never_loosen() -> None:
    active = [profile for profile in LEVELS.values() if profile.enabled]

    assert [profile.lint_policy for profile in active] == [
        "minimal",
        "basic",
        "standard",
        "strict",
        "hardened",
        "maximum",
    ]
    assert [profile.minimum_coverage_percent for profile in active] == sorted(
        profile.minimum_coverage_percent for profile in active
    )


def test_unknown_profile_fails_with_supported_names() -> None:
    with pytest.raises(ValueError, match="Supported levels: none, minimal, basic"):
        get_profile("ferocious")
