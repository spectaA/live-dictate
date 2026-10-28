from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest

from live_dictate import __version__, updater
from live_dictate.updater import (
    UpdateError,
    deb_asset_name,
    parse_checksums,
    parse_release,
    parse_version,
    update,
)

DEB_CONTENT = b"fake deb"


def _release_payload(version: str, with_checksums: bool = True) -> dict[str, object]:
    base = f"https://github.com/spectaA/live-dictate/releases/download/v{version}"
    assets = [{"name": deb_asset_name(version), "browser_download_url": f"{base}/package.deb"}]
    if with_checksums:
        assets.append({"name": "SHA256SUMS", "browser_download_url": f"{base}/SHA256SUMS"})
    return {"tag_name": f"v{version}", "assets": assets}


def _client(version: str, digest: str | None = None) -> httpx.Client:
    digest = digest or hashlib.sha256(DEB_CONTENT).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=_release_payload(version))
        if request.url.path.endswith("/SHA256SUMS"):
            return httpx.Response(200, text=f"{digest}  {deb_asset_name(version)}\n")
        return httpx.Response(200, content=DEB_CONTENT)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _bump(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def test_parse_version() -> None:
    assert parse_version("v1.2.10") > parse_version("1.2.9")
    with pytest.raises(UpdateError):
        parse_version("1.0.0rc1")


def test_parse_release_requires_deb_and_checksums() -> None:
    release = parse_release(_release_payload("1.2.3"))
    assert release.version == "1.2.3"
    assert release.deb_name == "live-dictate_1.2.3_amd64.deb"
    with pytest.raises(UpdateError, match="SHA256SUMS"):
        parse_release(_release_payload("1.2.3", with_checksums=False))


def test_parse_checksums_accepts_binary_marker() -> None:
    assert parse_checksums("abc  a.deb\nDEF *b.deb\n\n") == {"a.deb": "abc", "b.deb": "def"}


def test_update_reports_up_to_date() -> None:
    assert "up to date" in update(_client(__version__), check_only=False)


def test_update_check_only_does_not_install() -> None:
    installed: list[Path] = []
    outcome = update(_client(_bump(__version__)), check_only=True, installer=installed.append)
    assert "update available" in outcome
    assert installed == []


def test_update_refuses_source_checkouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "is_package_install", lambda: False)
    with pytest.raises(UpdateError, match="git pull"):
        update(_client(_bump(__version__)), check_only=False)


def test_update_installs_verified_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "is_package_install", lambda: True)
    installed: list[bytes] = []
    update(
        _client(_bump(__version__)),
        check_only=False,
        installer=lambda path: installed.append(path.read_bytes()),
    )
    assert installed == [DEB_CONTENT]


def test_update_rejects_checksum_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "is_package_install", lambda: True)
    with pytest.raises(UpdateError, match="checksum mismatch"):
        update(_client(_bump(__version__), digest="0" * 64), check_only=False)


@pytest.mark.parametrize(
    ("returncode", "message"),
    [(126, "password dialog was closed"), (100, r"package install failed \(exit 100\)")],
)
def test_install_failure_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, message: str
) -> None:
    def fail(command: list[str], check: bool) -> None:
        raise subprocess.CalledProcessError(returncode, command)

    monkeypatch.setattr(updater.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(updater.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(updater.subprocess, "run", fail)
    with pytest.raises(UpdateError, match=message):
        updater.install_deb(tmp_path / "package.deb")
