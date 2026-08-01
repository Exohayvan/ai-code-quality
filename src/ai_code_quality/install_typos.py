from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

TYPOS_VERSION: Final = "1.48.0"
_MAX_ARCHIVE_BYTES: Final = 20 * 1024 * 1024
_BASE_URL: Final = f"https://github.com/crate-ci/typos/releases/download/v{TYPOS_VERSION}"


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{_BASE_URL}/{self.name}"


_ASSETS: Final[dict[tuple[str, str], Asset]] = {
    ("Linux", "x86_64"): Asset(
        "typos-v1.48.0-x86_64-unknown-linux-musl.tar.gz",
        "72a930c9a94fc3914aa56835c5b859c892a797d40c1c42638b98d93f16ff519c",
    ),
    ("Linux", "aarch64"): Asset(
        "typos-v1.48.0-aarch64-unknown-linux-musl.tar.gz",
        "2960ae07bc1ffe19e4895e4359394dd349c9c31de78aac3a124b6e4aeb206698",
    ),
    ("Darwin", "x86_64"): Asset(
        "typos-v1.48.0-x86_64-apple-darwin.tar.gz",
        "f4335c255db3d57374484e0e96505c8910c0e2fa6d8813b15de529c98f93b1a9",
    ),
    ("Darwin", "arm64"): Asset(
        "typos-v1.48.0-aarch64-apple-darwin.tar.gz",
        "7dcaf386ec255995dcbaf629641f961574b7e8785203921115eab75cbf1ca107",
    ),
    ("Windows", "AMD64"): Asset(
        "typos-v1.48.0-x86_64-pc-windows-msvc.zip",
        "ce018a2352da7c1b23bd2684019ee279d2080dc063087020e80c1247d11b0743",
    ),
}


def asset_for(system: str, machine: str) -> Asset:
    aliases = {"arm64": "arm64", "aarch64": "aarch64", "AMD64": "AMD64", "x86_64": "x86_64"}
    normalized = aliases.get(machine, machine)
    try:
        return _ASSETS[(system, normalized)]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported platform for typos: {system}/{machine}") from exc


def verify_digest(payload: bytes, expected: str) -> None:
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected:
        raise RuntimeError(
            f"typos archive checksum mismatch: expected {expected}, observed {observed}"
        )


def _download(asset: Asset) -> bytes:
    request = urllib.request.Request(
        asset.url,
        headers={"User-Agent": "ai-code-quality-action"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read(_MAX_ARCHIVE_BYTES + 1)
    except OSError as exc:
        raise RuntimeError(f"Unable to download pinned typos archive: {exc}") from exc
    if len(payload) > _MAX_ARCHIVE_BYTES:
        raise RuntimeError("Pinned typos archive exceeds the download size limit")
    verify_digest(payload, asset.sha256)
    return payload


def _executable_bytes(asset: Asset, payload: bytes) -> tuple[str, bytes]:
    if asset.name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [
                name
                for name in archive.namelist()
                if not name.endswith("/") and Path(name).name == "typos.exe"
            ]
            if len(members) != 1:
                raise RuntimeError("Pinned typos archive has an unexpected executable layout")
            return "typos.exe", archive.read(members[0])
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.isfile() and Path(member.name).name == "typos"
        ]
        if len(members) != 1:
            raise RuntimeError("Pinned typos archive has an unexpected executable layout")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise RuntimeError("Unable to read the pinned typos executable")
        return "typos", stream.read()


def install(destination: Path) -> Path:
    asset = asset_for(platform.system(), platform.machine())
    payload = _download(asset)
    executable_name, executable = _executable_bytes(asset, payload)
    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / executable_name
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
            stream.write(executable)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        temporary_path.chmod(
            temporary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        temporary_path.replace(final_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return final_path


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned typos CLI")
    parser.add_argument("destination", type=Path)
    options = parser.parse_args(arguments)
    installed = install(options.destination)
    print(installed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
