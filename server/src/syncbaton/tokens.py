"""API tokens: generation, hashing and editing of the ``[users]`` table in the config file."""

import hashlib
import re
import secrets
import tomllib

USER_NAME = re.compile(r"[A-Za-z0-9_-]+")
_USERS_HEADER = re.compile(r"\s*\[\s*users\s*\]\s*(#.*)?")
_ANY_HEADER = re.compile(r"\s*\[")
_KEY_LINE = re.compile(r"""\s*(?:[A-Za-z0-9_-]+|"[^"]*"|'[^']*')\s*=""")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def add_user_hash(text: str, user: str, digest: str) -> str:
    """Return ``text`` with ``user = "<digest>"`` set in the ``[users]`` table.

    Only the line for ``user`` is touched; comments, ordering and every other table stay as they
    were. The result is parsed again and the call fails if it is no longer valid TOML.
    """
    if not USER_NAME.fullmatch(user):
        msg = f"invalid user name {user!r}: use letters, digits, '-' and '_'"
        raise ValueError(msg)
    line = f'{user} = "{digest}"\n'
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, raw in enumerate(lines) if _USERS_HEADER.fullmatch(raw.rstrip("\r\n"))), None
    )
    if start is None:
        prefix = text if not text or text.endswith("\n") else text + "\n"
        sep = "\n" if prefix.strip() else ""
        result = f"{prefix}{sep}[users]\n{line}"
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if _ANY_HEADER.match(lines[i])), len(lines)
        )
        own = re.compile(rf"""\s*(?:{re.escape(user)}|"{re.escape(user)}")\s*=""")
        existing = next((i for i in range(start + 1, end) if own.match(lines[i])), None)
        if existing is not None:
            newline = "\n" if lines[existing].endswith("\n") else ""
            lines[existing] = line.rstrip("\n") + newline
        else:
            keys = [i for i in range(start + 1, end) if _KEY_LINE.match(lines[i])]
            at = (keys[-1] if keys else start) + 1
            if not lines[at - 1].endswith("\n"):
                lines[at - 1] += "\n"
            lines.insert(at, line)
        result = "".join(lines)
    try:
        tomllib.loads(result)
    except tomllib.TOMLDecodeError as err:
        msg = f"editing [users] produced invalid TOML (is users set some other way?): {err}"
        raise ValueError(msg) from err
    return result
