"""Parsing of durations such as ``15m``."""

import re

_PATTERN = re.compile(r"(\d+)\s*([smh])")
_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Return the number of seconds in ``text`` (a positive integer plus ``s``, ``m`` or ``h``)."""
    match = _PATTERN.fullmatch(text.strip())
    seconds = int(match.group(1)) * _SECONDS[match.group(2)] if match else 0
    if seconds <= 0:
        msg = f"invalid duration {text!r}: use a positive number and s, m or h, for example 15m"
        raise ValueError(msg)
    return seconds
