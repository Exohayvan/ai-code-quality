from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CloneFragment:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class DuplicationClone:
    first: CloneFragment
    second: CloneFragment
    lines: int
    tokens: int
    language: str


@dataclass(frozen=True, slots=True)
class DuplicationResult:
    percentage: float
    duplicated_lines: int
    total_lines: int
    clones: tuple[DuplicationClone, ...]


@dataclass(frozen=True, slots=True)
class ComplexityFunction:
    path: str
    start_line: int
    end_line: int
    symbol: str
    ccn: int
    length: int = 0
    parameter_count: int = 0


@dataclass(frozen=True, slots=True)
class ToolFinding:
    tool: str
    rule: str
    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    message: str
    severity: str
    suggestions: tuple[str, ...] = ()
    path_context: bool = False


@dataclass(frozen=True, slots=True)
class ScanResult:
    duplication: DuplicationResult
    functions: tuple[ComplexityFunction, ...]
    semgrep: tuple[ToolFinding, ...] = ()
    yamllint: tuple[ToolFinding, ...] = ()
    markdownlint: tuple[ToolFinding, ...] = ()
    typos: tuple[ToolFinding, ...] = ()
