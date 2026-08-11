#!/usr/bin/env python3
"""Stream a command through a pseudo-terminal into a durable log file."""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import signal
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-regex", help="Write only complete lines matching this regex")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def write_filtered(
    handle: object,
    data: bytes,
    pending: bytes,
    pattern: re.Pattern[bytes] | None,
) -> bytes:
    if pattern is None:
        handle.write(data)
        return pending
    pending += data
    boundary = pending.rfind(b"\n")
    if boundary < 0:
        return pending
    complete, pending = pending[: boundary + 1], pending[boundary + 1 :]
    for line in complete.splitlines(keepends=True):
        if pattern.search(line):
            handle.write(line)
    return pending


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        args.command,
        stdin=subprocess.DEVNULL,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    stopping = False
    stop_deadline = 0.0
    pattern = re.compile(args.include_regex.encode()) if args.include_regex else None
    pending = b""

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping, stop_deadline
        if not stopping:
            stopping = True
            stop_deadline = time.monotonic() + 5.0
            process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    with output.open("wb") as handle:
        while True:
            readable, _, _ = select.select([master_fd], [], [], 0.25)
            if readable:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    data = b""
                if data:
                    pending = write_filtered(handle, data, pending, pattern)
                    handle.flush()
            if process.poll() is not None:
                break
            if stopping and time.monotonic() >= stop_deadline:
                process.kill()

        while True:
            readable, _, _ = select.select([master_fd], [], [], 0)
            if not readable:
                break
            try:
                data = os.read(master_fd, 65536)
            except OSError:
                break
            if not data:
                break
            pending = write_filtered(handle, data, pending, pattern)
        if pending and (pattern is None or pattern.search(pending)):
            handle.write(pending)
        handle.flush()
    os.close(master_fd)
    return 0 if stopping else process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
