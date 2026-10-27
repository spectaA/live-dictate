from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

import httpx

from . import __version__
from .audio import Recorder, pcm_duration_ms
from .cleanup import Cleaner
from .config import Config, default_history_path, has_api_key, load_api_key, load_config
from .updater import SERVICE, UpdateError, github_client, update

REQUIRED_TOOLS = ("parec", "xdotool", "xclip", "xprop")
PACKAGED_UNIT = Path("/usr/lib/systemd/user") / SERVICE
# Their DEBUG output includes request headers, hence the API key; INFO only adds noise.
NOISY_HTTP_LOGGERS = ("websockets", "httpcore", "httpx")


def _print_output(text: str) -> None:
    print(text, flush=True)


def _check(args: argparse.Namespace) -> int:
    ok = True

    def report(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        suffix = f": {detail}" if detail else ""
        print(f"[{'ok' if passed else 'FAIL'}] {label}{suffix}")

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    report("X11 session", session == "x11", session)
    for tool in REQUIRED_TOOLS:
        report(f"tool {tool}", shutil.which(tool) is not None)

    config = load_config(args.config)
    report("config", True, str(config))

    try:
        api_key = load_api_key()
        report("OpenAI API key", True, f"{api_key[:7]}…")
    except RuntimeError as error:
        report("OpenAI API key", False, str(error))
        api_key = ""

    captured = bytearray()
    recorder = Recorder(captured.extend)
    recorder.start()
    time.sleep(1.0)
    recorder.stop()
    report("microphone", len(captured) > 0, f"{pcm_duration_ms(len(captured))} ms captured")

    if api_key and args.api:
        sample = (
            "euh alors du coup je je voulais te dire que euh la réunion est est décalée à jeudi"
        )
        cleaned = asyncio.run(_clean_once(api_key, config, sample))
        report("cleanup call", cleaned != sample, cleaned)

    return 0 if ok else 1


async def _clean_once(api_key: str, config: Config, text: str) -> str:
    cleaner = Cleaner(api_key, config)
    try:
        return (await cleaner.clean(text)).text
    finally:
        await cleaner.aclose()


def _acquire_single_instance_lock() -> IO[str] | None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(runtime_dir) if runtime_dir else Path(f"/tmp/live-dictate-{os.getuid()}")  # noqa: S108
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (directory / "live-dictate.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _run(args: argparse.Namespace) -> int:
    from .app import run_app

    # The kernel releases the lock when the process exits, even on a crash.
    lock = _acquire_single_instance_lock()
    if lock is None:
        print("live-dictate is already running; stop it first (pkill -f live-dictate).")
        return 1

    if not has_api_key():
        if not os.environ.get("DISPLAY"):
            print("No OpenAI API key: run `live-dictate setup`.", file=sys.stderr)
            return 1
        from .setup_window import run_setup

        if not run_setup(args.config):
            print("Setup cancelled; live-dictate is not running.")
            return 0

    config = load_config(args.config)
    api_key = load_api_key()
    output = _print_output if args.dry_run else None
    cleanup = config.effective_cleanup_style != "off" and not args.no_cleanup
    run_app(config, api_key, cleanup=cleanup, output=output, config_path=args.config)
    return 0


def _setup(args: argparse.Namespace) -> int:
    from .setup_window import run_setup

    if not run_setup(args.config):
        print("Setup cancelled; nothing was changed.")
        return 0
    if PACKAGED_UNIT.exists():
        subprocess.run(["systemctl", "--user", "restart", SERVICE], check=False)
        print("Saved. live-dictate is running: hold the hotkey and speak.")
    else:
        print("Saved. Start live-dictate with `live-dictate run`.")
    return 0


def _stats(args: argparse.Namespace) -> int:
    from . import history

    config = load_config(args.config)
    path = default_history_path()
    if not config.history_enabled and not path.exists():
        print("The history is off. Turn it on in `live-dictate setup`, History tab.")
        return 0
    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    entries = history.load(path, since)
    stats = history.summarize(entries)
    period = f"last {args.days} days" if args.days else "all time"
    wait = "-" if stats.average_wait_s is None else f"{stats.average_wait_s:.1f} s"
    removed = "-" if stats.removed_ratio is None else f"{stats.removed_ratio:.1%}"
    print(f"live-dictate statistics, {period} ({path})")
    print(f"  dictations                {stats.dictations:,}")
    print(f"  speaking time             {history.format_duration(stats.speaking_s)}")
    print(f"  estimated cost            ${stats.cost_usd:.2f}")
    print(f"  avg. wait after release   {wait}")
    print(f"  words dictated            {stats.words:,}")
    print(f"  words removed by cleanup  {removed}")
    for entry in entries[-args.last :] if args.last else []:
        moment = datetime.fromisoformat(entry["time"]).astimezone()
        print(f"\n{moment:%Y-%m-%d %H:%M}  {entry['outcome']}  {entry.get('style')}")
        print(f"  raw:   {entry.get('raw', '')}")
        if entry.get("text") and entry.get("text") != entry.get("raw"):
            print(f"  text:  {entry['text']}")
    return 0


def _update(args: argparse.Namespace) -> int:
    try:
        with github_client() as client:
            print(update(client, check_only=args.check))
    except (UpdateError, httpx.HTTPError) as error:
        print(f"update failed: {error}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="live-dictate",
        description="Push-to-talk dictation: hold the hotkey, speak, release to paste.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="TOML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logs")
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser("run", help="start the dictation daemon (default)")
    run.add_argument("--dry-run", action="store_true", help="print text instead of pasting it")
    run.add_argument("--no-cleanup", action="store_true", help="paste the raw transcript")

    check = commands.add_parser("check", help="verify tools, API key and microphone")
    check.add_argument("--api", action="store_true", help="also send one cleanup request")

    commands.add_parser("setup", help="open the settings window")

    stats = commands.add_parser("stats", help="summarize the dictation history")
    stats.add_argument("--days", type=int, default=30, help="period in days, 0 for all time")
    stats.add_argument("--last", type=int, default=0, help="also print the last N dictations")

    upgrade = commands.add_parser("update", help="install the latest release from GitHub")
    upgrade.add_argument("--check", action="store_true", help="only report available updates")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if args.command == "check":
        sys.exit(_check(args))
    if args.command == "setup":
        sys.exit(_setup(args))
    if args.command == "stats":
        sys.exit(_stats(args))
    if args.command == "update":
        sys.exit(_update(args))
    if args.command is None:
        args = parser.parse_args([*sys.argv[1:], "run"])
    sys.exit(_run(args))
