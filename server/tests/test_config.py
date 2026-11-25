from pathlib import Path

import pytest

from syncbaton.config import ConfigError, load_config, parse_config, read_api_key

HASH = "c" * 64


@pytest.fixture
def raw(tmp_path: Path) -> dict:
    (tmp_path / "music").mkdir()
    return {
        "listen": "127.0.0.1:9000",
        "stale_after": "10m",
        "syncthing": {"url": "http://127.0.0.1:8384", "api_key_file": str(tmp_path / "key")},
        "folder": [{"id": "music", "path": str(tmp_path / "music"), "patterns": ["*.logicx"]}],
        "users": {"alex": HASH},
        "notify": {"ntfy_url": "https://ntfy.example/topic", "webhook_url": "http://h/hook"},
    }


def test_parses_full_config(raw, tmp_path):
    cfg = parse_config(raw)
    assert (cfg.listen_host, cfg.listen_port, cfg.stale_after) == ("127.0.0.1", 9000, 600)
    assert cfg.folders[0].path == tmp_path / "music"


def test_defaults(raw):
    for key in ("listen", "stale_after", "users", "notify"):
        del raw[key]
    cfg = parse_config(raw)
    assert (cfg.listen_host, cfg.listen_port, cfg.stale_after) == ("127.0.0.1", 8790, 900)
    assert cfg.users == {}
    assert cfg.notify.ntfy_url is None


def test_load_from_file_and_toml_syntax_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("listen = ")
    with pytest.raises(ConfigError, match="c.toml"):
        load_config(p)
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.toml")


def mutate(raw, path, value):
    node = raw
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("bogus",), 1, "unknown key 'bogus'"),
        (("syncthing", "bogus"), 1, r"unknown key 'bogus' in \[syncthing\]"),
        (("folder", 0, "bogus"), 1, r"unknown key 'bogus' in \[\[folder\]\]"),
        (("notify", "bogus"), 1, r"unknown key 'bogus' in \[notify\]"),
        (("listen",), "nocolon", "listen"),
        (("listen",), "host:notaport", "listen"),
        (("listen",), "host:70000", "listen"),
        (("listen",), 8790, "listen"),
        (("stale_after",), "soon", "stale_after"),
        (("stale_after",), 15, "stale_after"),
        (("syncthing",), KeyError, r"missing \[syncthing\]"),
        (("syncthing", "url"), KeyError, "url"),
        (("syncthing", "url"), "ftp://x", "url"),
        (("syncthing", "api_key_file"), KeyError, "api_key_file"),
        (("folder",), [], r"at least one \[\[folder\]\]"),
        (("folder",), KeyError, r"at least one \[\[folder\]\]"),
        (("folder", 0, "id"), KeyError, "id"),
        (("folder", 0, "id"), "a/b", "folder id"),
        (("folder", 0, "path"), "/definitely/not/here", "does not exist"),
        (("folder", 0, "patterns"), [], "patterns"),
        (("folder", 0, "patterns"), "*.logicx", "patterns"),
        (("folder", 0, "patterns"), ["a/b"], "patterns"),
        (("folder", 0, "patterns"), KeyError, "patterns"),
        (("users", "alex"), "not-a-hash", "sha256"),
        (("users", "alex"), 5, "sha256"),
        (("notify", "ntfy_url"), "not a url", "ntfy_url"),
    ],
)
def test_validation_messages(raw, path, value, message):
    mutate(raw, path, value)
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_duplicate_folder_id(raw):
    raw["folder"].append(dict(raw["folder"][0]))
    with pytest.raises(ConfigError, match="duplicate folder id"):
        parse_config(raw)


def test_folder_path_must_be_directory(raw, tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    raw["folder"][0]["path"] = str(f)
    with pytest.raises(ConfigError, match="not a directory"):
        parse_config(raw)


def test_read_api_key_from_file(raw, tmp_path):
    (tmp_path / "key").write_text("  secret-value\n")
    assert read_api_key(parse_config(raw).syncthing) == "secret-value"


def test_read_api_key_errors(raw, tmp_path):
    cfg = parse_config(raw)
    with pytest.raises(ConfigError, match="cannot read"):
        read_api_key(cfg.syncthing)
    (tmp_path / "key").write_text("\n")
    with pytest.raises(ConfigError, match="empty"):
        read_api_key(cfg.syncthing)


def test_tables_must_be_tables(raw):
    raw["users"] = 5
    with pytest.raises(ConfigError, match=r"\[users\] must be a table"):
        parse_config(raw)


def test_empty_optional_string_is_rejected(raw):
    raw["notify"]["ntfy_url"] = ""
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_config(raw)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8384", "http://localhost:8384", "http://[::1]:8384", "https://st.example"],
)
def test_syncthing_url_allowed(raw, url):
    raw["syncthing"]["url"] = url
    assert parse_config(raw).syncthing.url == url


@pytest.mark.parametrize("url", ["http://st.example:8384", "http://192.168.1.5:8384"])
def test_plain_http_to_a_non_loopback_syncthing_is_refused(raw, url):
    raw["syncthing"]["url"] = url
    with pytest.raises(ConfigError, match="https"):
        parse_config(raw)
