from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import __version__

REPOSITORY = "spectaA/live-dictate"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
INSTALL_PREFIX = Path("/opt/live-dictate")
CHECKSUMS_ASSET = "SHA256SUMS"
SERVICE = "live-dictate.service"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    deb_name: str
    deb_url: str
    checksums_url: str


def parse_version(tag: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in tag.removeprefix("v").split("."))
    except ValueError as error:
        raise UpdateError(f"unsupported version format: {tag!r}") from error


def deb_asset_name(version: str) -> str:
    return f"live-dictate_{version}_amd64.deb"


def parse_release(payload: dict[str, Any]) -> Release:
    version = str(payload.get("tag_name", "")).removeprefix("v")
    assets = {asset["name"]: asset["browser_download_url"] for asset in payload.get("assets", [])}
    deb_name = deb_asset_name(version)
    missing = [name for name in (deb_name, CHECKSUMS_ASSET) if name not in assets]
    if missing:
        raise UpdateError(f"release {version} is missing {', '.join(missing)}")
    return Release(version, deb_name, assets[deb_name], assets[CHECKSUMS_ASSET])


def parse_checksums(text: str) -> dict[str, str]:
    checksums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            digest, name = parts
            checksums[name.lstrip("*")] = digest.lower()
    return checksums


def is_package_install() -> bool:
    return Path(__file__).resolve().is_relative_to(INSTALL_PREFIX)


def install_deb(path: Path) -> None:
    command = ["apt-get", "install", "--yes", str(path)]
    if os.geteuid() != 0:
        command.insert(0, "sudo" if sys.stdin.isatty() else "pkexec")
    # The package's postinst restarts the running daemons of logged-in users.
    subprocess.run(command, check=True)


def _download_verified(client: httpx.Client, release: Release, directory: Path) -> Path:
    checksums = parse_checksums(client.get(release.checksums_url).raise_for_status().text)
    expected = checksums.get(release.deb_name)
    if not expected:
        raise UpdateError(f"{CHECKSUMS_ASSET} has no entry for {release.deb_name}")
    content = client.get(release.deb_url).raise_for_status().content
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise UpdateError(f"checksum mismatch for {release.deb_name}")
    path = directory / release.deb_name
    path.write_bytes(content)
    # apt reads local packages as the unprivileged _apt user.
    directory.chmod(0o755)
    path.chmod(0o644)
    return path


def update(
    client: httpx.Client,
    check_only: bool,
    installer: Callable[[Path], None] = install_deb,
) -> str:
    """Returns a human-readable outcome; raises UpdateError or httpx.HTTPError on failure."""
    release = parse_release(client.get(LATEST_RELEASE_URL).raise_for_status().json())
    if parse_version(release.version) <= parse_version(__version__):
        return f"live-dictate {__version__} is up to date"
    if check_only:
        return f"update available: {__version__} -> {release.version}"
    if not is_package_install():
        raise UpdateError("this copy runs from a source checkout; update it with git pull")
    with tempfile.TemporaryDirectory() as directory:
        installer(_download_verified(client, release, Path(directory)))
    return f"updated live-dictate {__version__} -> {release.version}"


def github_client() -> httpx.Client:
    return httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"live-dictate/{__version__}",
        },
    )
