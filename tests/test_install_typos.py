from __future__ import annotations

import hashlib

import pytest

from ai_code_quality.install_typos import asset_for, verify_digest


@pytest.mark.parametrize(
    ("system", "machine", "suffix"),
    [
        ("Linux", "x86_64", "x86_64-unknown-linux-musl.tar.gz"),
        ("Linux", "aarch64", "aarch64-unknown-linux-musl.tar.gz"),
        ("Darwin", "x86_64", "x86_64-apple-darwin.tar.gz"),
        ("Darwin", "arm64", "aarch64-apple-darwin.tar.gz"),
        ("Windows", "AMD64", "x86_64-pc-windows-msvc.zip"),
    ],
)
def test_typos_assets_are_pinned_per_supported_runner(
    system: str, machine: str, suffix: str
) -> None:
    asset = asset_for(system, machine)

    assert asset.name == f"typos-v1.48.0-{suffix}"
    assert asset.url.endswith(f"/v1.48.0/{asset.name}")
    assert len(asset.sha256) == 64


def test_unknown_typos_platform_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="Unsupported platform"):
        asset_for("Plan9", "mips")


def test_typos_download_digest_is_verified() -> None:
    payload = b"pinned archive"
    verify_digest(payload, hashlib.sha256(payload).hexdigest())

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_digest(payload, "0" * 64)
