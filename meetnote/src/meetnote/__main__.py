"""Command line: `meetnote` opens the app; subcommands record, recover and check the setup."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import __version__, models, paths, session, transcriber
from .transcript import LABEL, clock


def _progress(name: str, have: int, total: int) -> None:
    pct = 100 * have // total if total else 100
    print(f"\r  {name}: {have / 1e6:7.1f} of {total / 1e6:.1f} MB ({pct}%)",
          end="", file=sys.stderr, flush=True)
    if have >= total:
        print(file=sys.stderr)


def _missing_models() -> list[str]:
    return [name for name in models.DEFAULT if not models.is_installed(name)]


def cmd_models(args: argparse.Namespace) -> int:
    names = args.names or list(models.DEFAULT)
    unknown = [n for n in names if n not in models.CATALOG]
    if unknown:
        print(f"unknown model: {', '.join(unknown)}; choose from {', '.join(models.CATALOG)}",
              file=sys.stderr)
        return 2
    for name in names:
        if models.is_installed(name):
            print(f"{name}: already installed", file=sys.stderr)
            continue
        print(f"{name}: downloading to {models.model_dir(name)}", file=sys.stderr)
        models.download(name, _progress)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .core import Controller, acquire_app_lock

    if _missing_models():
        print("speech models missing; run: meetnote models", file=sys.stderr)
        return 1
    if acquire_app_lock() is None:
        print("meetnote is already running", file=sys.stderr)
        return 1
    tty = sys.stdout.isatty()
    out = threading.Lock()

    def emit(kind: str, data: dict) -> None:
        with out:
            if kind == "line":
                print(f"\r[{clock(data['w0'])}] {LABEL[data['src']]}: {data['text']}\033[K" if tty
                      else f"[{clock(data['w0'])}] {LABEL[data['src']]}: {data['text']}", flush=True)
            elif kind == "levels" and tty:
                bar = lambda v: "#" * round(v * 10) + "." * (10 - round(v * 10))  # noqa: E731
                print(f"\r  me [{bar(data['me'])}]  others [{bar(data['others'])}]\033[K",
                      end="", flush=True)
            elif kind == "device":
                print(f"\r{LABEL[data['src']]} device: {data['name']}\033[K" if tty
                      else f"{LABEL[data['src']]} device: {data['name']}", flush=True)
            elif kind in ("adopted", "interrupted"):
                print(f"session {data['id']}: {kind}", flush=True)

    def interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    # Ctrl+C and SIGTERM both stop the recording cleanly, also when started in the background.
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    ctl = Controller(emit=emit)
    ctl.recover()
    if ctl.recorder is None:
        ctl.start(args.title)
    print(f"Recording into {ctl.session.folder}\nPress Ctrl+C to stop.", flush=True)
    try:
        while True:
            ctl.poll()
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    s = ctl.stop()
    print("\rStopped. Finishing the transcript (Ctrl+C again leaves it running in the background)…",
          flush=True)
    try:
        while s is not None and ctl.transcribing(s.id):
            ctl.poll()
            time.sleep(0.25)
        ctl.poll()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    s = session.load(args.id)
    return transcriber.main([str(s.folder)])


def cmd_sessions(args: argparse.Namespace) -> int:
    for s in session.list_sessions():
        print(f"{s.id}  {s.status:<11}  {s.title}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    def check(label: str, passed: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"{'ok  ' if passed else 'FAIL'}  {label}" + (f"  ({hint})" if hint and not passed else ""))

    for tool in ("parec", "pactl", "timeout"):
        check(f"{tool} found", shutil.which(tool) is not None, "install pulseaudio-utils / coreutils")
    try:
        server = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=5,
                                env={**os.environ, "LC_ALL": "C"})
        check("sound server reachable", server.returncode == 0, "is PipeWire running?")
    except (OSError, subprocess.SubprocessError):
        check("sound server reachable", False, "is PipeWire running?")
    for name in models.DEFAULT:
        check(f"model {name}", models.is_installed(name), "run: meetnote models")
    claude = shutil.which("claude")
    check("claude CLI found", claude is not None, "install Claude Code for questions and summaries")
    if claude:
        try:
            status = json.loads(subprocess.run([claude, "auth", "status"], capture_output=True,
                                               text=True, timeout=20).stdout or "{}")
        except (OSError, ValueError, subprocess.SubprocessError):
            status = {}
        check("claude logged in with a Claude subscription", status.get("authMethod") == "claude.ai",
              f"auth method is {status.get('authMethod')!r}; run `claude` and /login")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2  # noqa: F401
        check("GTK 3 and WebKit2GTK 4.1 for the window", True)
    except (ImportError, ValueError):
        check("GTK 3 and WebKit2GTK 4.1 for the window", False,
              "sudo apt install gir1.2-webkit2-4.1, and build the venv with --system-site-packages")
    print(f"data: {paths.data_dir()}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meetnote", description="Lightweight, crash-safe meeting notes for Linux.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log details to stderr")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("record", help="record and transcribe in the terminal, without the window")
    p.add_argument("--title", default="", help="meeting title")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help="finish the transcript of a session")
    p.add_argument("id", help="session id, as listed by `meetnote sessions`")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("sessions", help="list recorded sessions")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("models", help="download the speech and voice-activity models")
    p.add_argument("names", nargs="*", help=f"any of: {', '.join(models.CATALOG)}")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("doctor", help="check the setup")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not args.cmd:
        from . import app

        return app.main()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
