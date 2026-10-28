from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_code_quality.checks.opaque_go import classify
from ai_code_quality.models import ToolFinding

REVISION = "e25ba8c21ef6"
VERSION = f"v0.0.0-20251113190631-{REVISION}"


def fixture(tmp_path: Path, body: str, target: str = "go.mod") -> ToolFinding:
    text = f"--- a/{target}\n+++ b/{target}\n@@ -16,1 +16,1 @@\n{body}\n"
    (tmp_path / "dependency.patch").write_text(text)
    column = body.index(REVISION) + 1
    return ToolFinding(
        tool="typos", rule="typo", path="dependency.patch", line=4, column=column,
        end_line=4, end_column=column + len(REVISION) - 1,
        message=f"Possible typo {REVISION!r}", severity="warning",
    )


@pytest.mark.parametrize("prefix", [" \t", "+\t", " require "])
def test_mod_revision(tmp_path: Path, prefix: str) -> None:
    finding = fixture(tmp_path, f"{prefix}golang.org/x/exp {VERSION}")
    if prefix.startswith("+"):
        path = tmp_path / "dependency.patch"
        path.write_text(path.read_text().replace("-16,1", "-15,0"))
    evidence = classify(finding, tmp_path)
    assert evidence is not None
    assert evidence.revision == REVISION
    assert len(evidence.source_sha256) == 64
    assert finding.message == f"Possible typo {REVISION!r}"


@pytest.mark.parametrize("suffix", ["", "/go.mod"])
def test_sum_revision(tmp_path: Path, suffix: str) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}{suffix} h1:{'A' * 43}=", "go.sum")
    assert classify(finding, tmp_path) is not None


@pytest.mark.parametrize("version", [
    f"v1.2.3-pre.0.20251113190631-{REVISION}",
    f"v1.2.4-0.20251113190631-{REVISION}+incompatible",
])
def test_other_canonical_forms(tmp_path: Path, version: str) -> None:
    assert classify(fixture(tmp_path, f" golang.org/x/exp {version}"), tmp_path) is not None


@pytest.mark.parametrize("body,target", [
    (f" prose mentions {VERSION}", "go.mod"),
    (f" // golang.org/x/exp {VERSION}", "go.mod"),
    (f" golang.org/x/exp {VERSION} // prose", "go.mod"),
    (f" golang.org/x/exp {VERSION}", "README.md"),
    (f" golang.org/x/exp {VERSION} h1:bad", "go.sum"),
    (f" golang.org/x/exp v0.0.0-20251313190631-{REVISION}", "go.mod"),
    (f" golang.org/x/exp {VERSION}a", "go.mod"),
    (f" golang.org/x/exp v1.2.3-{REVISION}", "go.mod"),
])
def test_non_dependency_tokens_remain(tmp_path: Path, body: str, target: str) -> None:
    assert classify(fixture(tmp_path, body, target), tmp_path) is None


@pytest.mark.parametrize("change", [
    {"path_context": True}, {"column": 1}, {"end_line": 5},
    {"message": "Possible typo 'different'"}, {"tool": "lint"},
    {"path": "../dependency.patch"}, {"path": "missing.patch"},
])
def test_bad_finding_evidence(tmp_path: Path, change: dict) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    assert classify(replace(finding, **change), tmp_path) is None


@pytest.mark.parametrize("mutation", ["truncate", "extra", "header", "overlap", "prose"])
def test_entire_patch_must_validate(tmp_path: Path, mutation: str) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    text = path.read_text()
    mutations = {
        "truncate": text.replace("+16,1", "+16,2"),
        "extra": text + " extra\n",
        "header": text.replace("+++ b/go.mod", "+++ b/go.sum"),
        "overlap": text + "@@ -16,1 +16,1 @@\n same\n",
        "prose": "Example patch:\n" + text,
    }
    path.write_text(mutations[mutation])
    assert classify(finding, tmp_path) is None


def test_substring_only_and_module_finding_not_classified(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    substring = replace(finding, column=finding.column + 3, end_column=finding.column + 4,
                        message="Possible typo 'ba'")
    assert classify(substring, tmp_path) is not None
    path_finding = replace(finding, column=2, end_column=7, message="Possible typo 'golang'")
    assert classify(path_finding, tmp_path) is None


@pytest.mark.parametrize("module", ["golang..org/x", "golang.org/x/..", "golang.org/x.",
                                    "-bad.org/x", "golang.org/x!bad"])
def test_malformed_module_path(tmp_path: Path, module: str) -> None:
    assert classify(fixture(tmp_path, f" {module} {VERSION}"), tmp_path) is None


def test_removed_record_is_not_classified(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f"-golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    path.write_text(path.read_text().replace("+16,1", "+15,0"))
    assert classify(finding, tmp_path) is None


def test_noncanonical_prerelease(tmp_path: Path) -> None:
    version = f"v1.2.3-01.0.20251113190631-{REVISION}"
    assert classify(fixture(tmp_path, f" golang.org/x/exp {version}"), tmp_path) is None


@pytest.mark.parametrize("separator", [" ", "\t", " \t"])
def test_sum_rejects_require_directive(tmp_path: Path, separator: str) -> None:
    body = f" require{separator}golang.org/x/exp {VERSION} h1:{'A' * 43}="
    assert classify(fixture(tmp_path, body, "go.sum"), tmp_path) is None


@pytest.mark.parametrize("target", ["go.mod", "./go.mod"])
def test_duplicate_normalized_target_rejected(tmp_path: Path, target: str) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    text = path.read_text()
    path.write_text(text + text.replace("a/go.mod", f"a/{target}")
                   .replace("b/go.mod", f"b/{target}"))
    assert classify(finding, tmp_path) is None


@pytest.mark.parametrize("target", ["./go.mod", "nested//go.mod", "nested/../go.mod"])
def test_noncanonical_target_rejected(tmp_path: Path, target: str) -> None:
    assert classify(fixture(tmp_path, f" golang.org/x/exp {VERSION}", target), tmp_path) is None


def test_inconsistent_unchanged_gap_rejected(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    path.write_text(path.read_text() + "@@ -20,1 +21,1 @@\n context\n")
    assert classify(finding, tmp_path) is None


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    finding = fixture(outside, f" golang.org/x/exp {VERSION}")
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / finding.path).symlink_to(outside / finding.path)
    assert classify(finding, repository) is None


def test_contained_symlink_uses_verified_target_bytes(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    (tmp_path / "alias.patch").symlink_to(tmp_path / finding.path)
    result = classify(replace(finding, path="alias.patch"), tmp_path)
    assert result is not None
    assert result.path == "alias.patch"


def test_first_insertion_only_hunk(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f"+\tgolang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    path.write_text(path.read_text().replace("@@ -16,1 +16,1 @@", "@@ -0,0 +1 @@"))
    assert classify(finding, tmp_path) is not None


@pytest.mark.parametrize("first,second", [
    ("@@ -0,0 +1 @@\n+new\n", "@@ -1 +2 @@"),
    ("@@ -1 +0,0 @@\n-old\n", "@@ -2 +1 @@"),
])
def test_empty_range_then_context(tmp_path: Path, first: str, second: str) -> None:
    finding = fixture(tmp_path, f" golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    text = path.read_text().replace("@@ -16,1 +16,1 @@", first + second)
    path.write_text(text)
    assert classify(replace(finding, line=6, end_line=6), tmp_path) is not None


def test_invalid_zero_range_coordinate_gap(tmp_path: Path) -> None:
    finding = fixture(tmp_path, f"+golang.org/x/exp {VERSION}")
    path = tmp_path / finding.path
    path.write_text(path.read_text().replace("@@ -16,1 +16,1 @@", "@@ -0,0 +2 @@"))
    assert classify(finding, tmp_path) is None
