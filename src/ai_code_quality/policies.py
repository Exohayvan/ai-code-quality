from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

_POLICY_ORDER: Final = {
    name: index for index, name in enumerate(("basic", "standard", "strict", "hardened", "maximum"))
}

_SEMGREP_RULES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "basic",
        {
            "id": "ai-quality.python.eval",
            "languages": ["python"],
            "message": "Avoid eval on potentially untrusted input",
            "severity": "ERROR",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "eval(...)",
        },
    ),
    (
        "basic",
        {
            "id": "ai-quality.python.exec",
            "languages": ["python"],
            "message": "Avoid dynamic exec",
            "severity": "ERROR",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "exec(...)",
        },
    ),
    (
        "basic",
        {
            "id": "ai-quality.python.subprocess-shell",
            "languages": ["python"],
            "message": "Avoid subprocess calls with shell=True",
            "severity": "ERROR",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern-either": [
                {"pattern": "subprocess.run(..., shell=True, ...)"},
                {"pattern": "subprocess.Popen(..., shell=True, ...)"},
                {"pattern": "subprocess.call(..., shell=True, ...)"},
                {"pattern": "subprocess.check_call(..., shell=True, ...)"},
                {"pattern": "subprocess.check_output(..., shell=True, ...)"},
            ],
        },
    ),
    (
        "basic",
        {
            "id": "ai-quality.javascript.eval",
            "languages": ["javascript", "typescript"],
            "message": "Avoid eval on potentially untrusted input",
            "severity": "ERROR",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "eval(...)",
        },
    ),
    (
        "standard",
        {
            "id": "ai-quality.python.pickle-load",
            "languages": ["python"],
            "message": "Loading pickle data can execute arbitrary code",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern-either": [{"pattern": "pickle.load(...)"}, {"pattern": "pickle.loads(...)"}],
        },
    ),
    (
        "standard",
        {
            "id": "ai-quality.python.unsafe-yaml-load",
            "languages": ["python"],
            "message": "Use yaml.safe_load or an explicit SafeLoader",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "patterns": [
                {"pattern": "yaml.load(...)"},
                {"pattern-not": "yaml.load(..., Loader=yaml.SafeLoader, ...)"},
                {"pattern-not": "yaml.load(..., Loader=yaml.CSafeLoader, ...)"},
            ],
        },
    ),
    (
        "standard",
        {
            "id": "ai-quality.javascript.child-process-exec",
            "languages": ["javascript", "typescript"],
            "message": "Avoid shell command execution with child_process.exec",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "MEDIUM"},
            "pattern": "child_process.exec(...)",
        },
    ),
    (
        "strict",
        {
            "id": "ai-quality.python.requests-no-verify",
            "languages": ["python"],
            "message": "Do not disable TLS certificate verification",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "requests.$METHOD(..., verify=False, ...)",
        },
    ),
    (
        "strict",
        {
            "id": "ai-quality.python.tempfile-mktemp",
            "languages": ["python"],
            "message": "Use a securely created temporary file instead of tempfile.mktemp",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "tempfile.mktemp(...)",
        },
    ),
    (
        "strict",
        {
            "id": "ai-quality.javascript-new-function",
            "languages": ["javascript", "typescript"],
            "message": "Avoid dynamically constructed functions",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "MEDIUM"},
            "pattern": "new Function(...)",
        },
    ),
    (
        "hardened",
        {
            "id": "ai-quality.python.weak-hash",
            "languages": ["python"],
            "message": "Avoid weak hashes for security-sensitive uses",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "MEDIUM"},
            "pattern-either": [{"pattern": "hashlib.md5(...)"}, {"pattern": "hashlib.sha1(...)"}],
        },
    ),
    (
        "hardened",
        {
            "id": "ai-quality.python.unverified-ssl-context",
            "languages": ["python"],
            "message": "Do not create an unverified TLS context",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "ssl._create_unverified_context(...)",
        },
    ),
    (
        "hardened",
        {
            "id": "ai-quality.javascript.reject-unauthorized",
            "languages": ["javascript", "typescript"],
            "message": "Do not disable TLS certificate verification",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "HIGH"},
            "pattern": "{..., rejectUnauthorized: false, ...}",
        },
    ),
    (
        "maximum",
        {
            "id": "ai-quality.python.os-system",
            "languages": ["python"],
            "message": "Prefer argument-vector subprocess APIs over os.system",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "MEDIUM"},
            "pattern": "os.system(...)",
        },
    ),
    (
        "maximum",
        {
            "id": "ai-quality.javascript.document-write",
            "languages": ["javascript", "typescript"],
            "message": "Avoid document.write because it can create injection risks",
            "severity": "WARNING",
            "metadata": {"category": "security", "confidence": "MEDIUM"},
            "pattern": "document.write(...)",
        },
    ),
)


def _validate_policy(policy: str) -> int:
    try:
        return _POLICY_ORDER[policy]
    except KeyError as exc:
        raise ValueError(f"Unknown quality policy: {policy}") from exc


def semgrep_config(policy: str) -> dict[str, Any]:
    maximum = _validate_policy(policy)
    return {
        "rules": [
            deepcopy(rule) for minimum, rule in _SEMGREP_RULES if _POLICY_ORDER[minimum] <= maximum
        ]
    }


def yamllint_config(policy: str) -> dict[str, Any]:
    _validate_policy(policy)
    if policy == "basic":
        return {
            "extends": "relaxed",
            "rules": {
                "document-start": "disable",
                "line-length": "disable",
                "truthy": {
                    "level": "warning",
                    "allowed-values": ["true", "false"],
                    "check-keys": False,
                },
            },
        }
    maximum = {"standard": 120, "strict": 120, "hardened": 100, "maximum": 80}[policy]
    level = "warning" if policy == "standard" else "error"
    return {
        "extends": "default",
        "rules": {
            "document-start": "disable",
            "line-length": {
                "max": maximum,
                "level": level,
                "allow-non-breakable-words": True,
                "allow-non-breakable-inline-mappings": True,
            },
            "truthy": {
                "level": level,
                "allowed-values": ["true", "false"],
                "check-keys": False,
            },
        },
    }


def markdownlint_config(policy: str) -> dict[str, Any]:
    _validate_policy(policy)
    if policy == "basic":
        return {
            "default": False,
            "MD001": True,
            "MD009": True,
            "MD010": True,
            "MD018": True,
            "MD022": True,
            "MD031": True,
            "MD040": True,
            "MD041": True,
            "MD013": False,
        }
    config: dict[str, Any] = {
        "default": True,
        "MD013": False,
        "MD024": {"siblings_only": True},
        "MD033": False,
        "MD036": False,
    }
    if policy in {"strict", "hardened", "maximum"}:
        maximum = {"strict": 120, "hardened": 100, "maximum": 80}[policy]
        config["MD013"] = {"line_length": maximum, "code_blocks": False, "tables": False}
    return config
