#!/usr/bin/env python3
"""Normalize a crontab stream to Unix LF line endings."""

import sys


def normalize_crontab_bytes(source):
    """Return crontab bytes with no carriage returns and one final newline."""
    if not isinstance(source, bytes):
        raise TypeError("crontab source must be bytes")
    if b"\0" in source:
        raise ValueError("crontab source contains a NUL byte")
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    literal_backslash_r = bytes((92, 114))
    normalized = b"\n".join(
        line[: -len(literal_backslash_r)]
        if line.endswith(literal_backslash_r)
        else line
        for line in normalized.split(b"\n")
    )
    normalized = normalized.rstrip(b"\n")
    if not normalized.strip():
        raise ValueError("crontab source is empty")
    return normalized + b"\n"


def main():
    sys.stdout.buffer.write(normalize_crontab_bytes(sys.stdin.buffer.read()))


if __name__ == "__main__":
    main()
