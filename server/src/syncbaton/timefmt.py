"""RFC 3339 UTC timestamps as used by the HTTP API."""

from datetime import UTC, datetime


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
