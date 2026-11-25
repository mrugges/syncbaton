"""Loading and validation of the TOML config file."""

import ipaddress
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from syncbaton.durations import parse_duration

DEFAULT_LISTEN = "127.0.0.1:8790"
DEFAULT_STALE_AFTER = "15m"

_HASH = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS = {"listen", "stale_after", "syncthing", "folder", "users", "notify"}
_SYNCTHING_KEYS = {"url", "api_key_file"}
_FOLDER_KEYS = {"id", "path", "patterns"}
_NOTIFY_KEYS = {"ntfy_url", "webhook_url"}


class ConfigError(Exception):
    """The config file is missing, unreadable or invalid. The message says what to fix."""


@dataclass(frozen=True)
class FolderConfig:
    id: str
    path: Path
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class SyncthingConfig:
    url: str
    api_key_file: Path


@dataclass(frozen=True)
class NotifyConfig:
    ntfy_url: str | None
    webhook_url: str | None


@dataclass(frozen=True)
class Config:
    listen_host: str
    listen_port: int
    stale_after: int
    syncthing: SyncthingConfig
    folders: tuple[FolderConfig, ...]
    users: dict[str, str] = field(default_factory=dict)
    notify: NotifyConfig = field(default_factory=lambda: NotifyConfig(None, None))


def read_text(path: Path, what: str) -> str:
    """Read a text file, turning I/O errors into a ``ConfigError`` that names ``what`` it was."""
    try:
        return path.read_text()
    except OSError as err:
        msg = f"cannot read {what} {path}: {err.strerror or err}"
        raise ConfigError(msg) from err


def load_config(path: Path) -> Config:
    text = read_text(path, "config")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        msg = f"{path}: {err}"
        raise ConfigError(msg) from err
    return parse_config(data)


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{where} must be a table"
        raise ConfigError(msg)
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    for key in table:
        if key not in allowed:
            msg = f"unknown key '{key}' in {where}: expected one of {', '.join(sorted(allowed))}"
            raise ConfigError(msg)


def _optional_string(table: dict[str, Any], key: str, where: str) -> str | None:
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str) or not value:
        msg = f"'{key}' in {where} must be a non-empty string"
        raise ConfigError(msg)
    return value


def _string(table: dict[str, Any], key: str, where: str) -> str:
    value = _optional_string(table, key, where)
    if value is None:
        msg = f"{where} is missing '{key}'"
        raise ConfigError(msg)
    return value


def _url(value: str, key: str, where: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        msg = f"'{key}' in {where} must be an http:// or https:// URL, got {value!r}"
        raise ConfigError(msg)
    return value


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _optional_url(table: dict[str, Any], key: str, where: str) -> str | None:
    value = _optional_string(table, key, where)
    return None if value is None else _url(value, key, where)


def _listen(value: Any) -> tuple[str, int]:
    text = value.strip() if isinstance(value, str) else ""
    host, sep, port_text = text.rpartition(":")
    host = host.strip("[]")
    if not sep or not host or not port_text.isdigit() or int(port_text) > 65535:
        msg = f'listen must look like "0.0.0.0:8790", got {value!r}'
        raise ConfigError(msg)
    return host, int(port_text)


def _stale_after(value: Any) -> int:
    if not isinstance(value, str):
        msg = f'stale_after must be a string such as "15m", got {value!r}'
        raise ConfigError(msg)
    try:
        return parse_duration(value)
    except ValueError as err:
        msg = f"stale_after: {err}"
        raise ConfigError(msg) from err


def _syncthing(raw: Any) -> SyncthingConfig:
    if raw is None:
        msg = "missing [syncthing] table"
        raise ConfigError(msg)
    table = _table(raw, "[syncthing]")
    _check_keys(table, _SYNCTHING_KEYS, "[syncthing]")
    url = _url(_string(table, "url", "[syncthing]"), "url", "[syncthing]")
    parts = urlsplit(url)
    if parts.scheme == "http" and not _is_loopback(parts.hostname or ""):
        msg = (
            f"'url' in [syncthing] is {url!r}: the API key is sent with every request, so use "
            "https:// unless Syncthing is on this machine (127.0.0.1, ::1 or localhost)"
        )
        raise ConfigError(msg)
    key_file = _string(table, "api_key_file", "[syncthing]")
    return SyncthingConfig(url=url, api_key_file=Path(key_file))


def _folder(raw: Any, seen: set[str]) -> FolderConfig:
    table = _table(raw, "[[folder]]")
    _check_keys(table, _FOLDER_KEYS, "[[folder]]")
    folder_id = _string(table, "id", "[[folder]]")
    path = Path(_string(table, "path", "[[folder]]"))
    where = f"folder '{folder_id}'"
    if "/" in folder_id:
        msg = f"folder id '{folder_id}' must not contain '/'"
        raise ConfigError(msg)
    if folder_id in seen:
        msg = f"duplicate folder id '{folder_id}'"
        raise ConfigError(msg)
    seen.add(folder_id)
    if not path.exists():
        msg = f"{where}: path {path} does not exist"
        raise ConfigError(msg)
    if not path.is_dir():
        msg = f"{where}: path {path} is not a directory"
        raise ConfigError(msg)
    patterns = table.get("patterns")
    if not (
        isinstance(patterns, list)
        and patterns
        and all(isinstance(p, str) and p and "/" not in p for p in patterns)
    ):
        msg = (
            f'{where}: patterns must be a non-empty list of globs without "/", such as ["*.logicx"]'
        )
        raise ConfigError(msg)
    return FolderConfig(id=folder_id, path=path, patterns=tuple(patterns))


def _users(raw: Any) -> dict[str, str]:
    table = _table(raw, "[users]")
    for name, digest in table.items():
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            msg = f"[users] {name}: expected the sha256 hex of a token (64 lowercase hex digits)"
            raise ConfigError(msg)
    return dict(table)


def parse_config(data: dict[str, Any]) -> Config:
    _check_keys(data, _TOP_KEYS, "the config file")
    host, port = _listen(data.get("listen", DEFAULT_LISTEN))
    stale_after = _stale_after(data.get("stale_after", DEFAULT_STALE_AFTER))
    syncthing = _syncthing(data.get("syncthing"))
    raw_folders = data.get("folder")
    if not isinstance(raw_folders, list) or not raw_folders:
        msg = "the config needs at least one [[folder]] table"
        raise ConfigError(msg)
    seen: set[str] = set()
    folders = tuple(_folder(f, seen) for f in raw_folders)
    users = _users(data.get("users", {}))
    notify_table = _table(data.get("notify", {}), "[notify]")
    _check_keys(notify_table, _NOTIFY_KEYS, "[notify]")
    notify = NotifyConfig(
        ntfy_url=_optional_url(notify_table, "ntfy_url", "[notify]"),
        webhook_url=_optional_url(notify_table, "webhook_url", "[notify]"),
    )
    return Config(host, port, stale_after, syncthing, folders, users, notify)


def read_api_key(syncthing: SyncthingConfig) -> str:
    """Return the Syncthing API key stored in ``api_key_file``."""
    key = read_text(syncthing.api_key_file, "api_key_file").strip()
    if not key:
        msg = f"api_key_file {syncthing.api_key_file} is empty"
        raise ConfigError(msg)
    return key
