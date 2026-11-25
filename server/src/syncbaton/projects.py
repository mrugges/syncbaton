"""Projects are top-level entries of a synced folder whose names match the folder's patterns."""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from syncbaton.config import FolderConfig

CONFLICT_GLOB = "*.sync-conflict-*"
_CONFLICT_MARK = re.compile(r"\.sync-conflict-\d{8}-\d{6}(?:-[A-Z0-9]+)?")


@dataclass(frozen=True)
class ConflictHit:
    project: str
    path: str  # relative to the folder root, "/" separated


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def is_conflict_name(name: str) -> bool:
    return fnmatchcase(name, CONFLICT_GLOB)


def paths_under(paths: Iterable[str], project: str) -> list[str]:
    """Paths equal to ``project`` or inside it, keeping their order."""
    prefix = project + "/"
    return [p for p in paths if p == project or p.startswith(prefix)]


def _top_level(folder: FolderConfig) -> list[str]:
    try:
        return sorted(entry.name for entry in os.scandir(folder.path))
    except OSError:
        return []


def list_project_names(folder: FolderConfig) -> list[str]:
    return [
        name
        for name in _top_level(folder)
        if matches_any(name, folder.patterns) and not is_conflict_name(name)
    ]


def resolve_project(folder: FolderConfig, name: str) -> str | None:
    """Return ``name`` if it is a project of ``folder``, else None.

    ``name`` comes from a URL. Anything that is not a single path segment, or that does not name
    an existing, matching top-level entry, is rejected.
    """
    if name in ("", ".", "..") or "/" in name or "\0" in name:
        return None
    if not matches_any(name, folder.patterns) or is_conflict_name(name):
        return None
    if not os.path.lexists(folder.path / name):
        return None
    return name


def scan_conflicts(folder: FolderConfig) -> list[ConflictHit]:
    """Find Syncthing conflict copies inside projects and conflicted copies of projects."""
    hits: list[ConflictHit] = []
    for name in _top_level(folder):
        if not matches_any(name, folder.patterns):
            continue
        if is_conflict_name(name):
            hits.append(ConflictHit(_CONFLICT_MARK.sub("", name, count=1), name))
            continue
        top = folder.path / name
        if not top.is_dir() or top.is_symlink():
            continue
        for current, dirs, files in os.walk(top, followlinks=False):
            for entry in dirs + files:
                if is_conflict_name(entry):
                    relative = os.path.relpath(os.path.join(current, entry), folder.path)
                    hits.append(ConflictHit(name, relative.replace(os.sep, "/")))
    return sorted(hits, key=lambda hit: hit.path)
