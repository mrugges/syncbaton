import hashlib
import io
import threading
import tomllib

import pytest

from syncbaton import cli

CONFIG = """\
listen = "127.0.0.1:0"

[syncthing]
url = "http://127.0.0.1:1"
api_key_file = "%(key)s"

[[folder]]
id = "music"
path = "%(music)s"
patterns = ["*.logicx"]
"""


@pytest.fixture
def cfg_file(tmp_path, music):
    key = tmp_path / "key"
    key.write_text("k\n")
    p = tmp_path / "syncbaton.toml"
    p.write_text(CONFIG % {"key": key, "music": music})
    return p


def test_token_add_prints_token_once_and_stores_only_the_hash(cfg_file, capsys):
    assert cli.main(["token", "add", "alex", "--config", str(cfg_file)]) == 0
    out = capsys.readouterr()
    token = out.out.strip()
    assert len(token) >= 43
    text = cfg_file.read_text()
    assert token not in text
    assert tomllib.loads(text)["users"] == {"alex": hashlib.sha256(token.encode()).hexdigest()}
    assert text.startswith(CONFIG.split("%")[0])


def test_token_add_second_user_and_rotation(cfg_file, capsys):
    cli.main(["token", "add", "alex", "--config", str(cfg_file)])
    first = capsys.readouterr().out.strip()
    cli.main(["token", "add", "sam", "--config", str(cfg_file)])
    cli.main(["token", "add", "alex", "--config", str(cfg_file)])
    users = tomllib.loads(cfg_file.read_text())["users"]
    assert set(users) == {"alex", "sam"}
    assert users["alex"] != hashlib.sha256(first.encode()).hexdigest()


def test_token_add_errors(cfg_file, tmp_path, capsys):
    assert cli.main(["token", "add", "bad name", "--config", str(cfg_file)]) == 2
    assert cli.main(["token", "add", "alex", "--config", str(tmp_path / "none.toml")]) == 2
    assert "error:" in capsys.readouterr().err


def test_token_hash_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("abc\n"))
    assert cli.main(["token", "hash"]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b"abc").hexdigest()


def test_token_hash_empty_input_fails(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert cli.main(["token", "hash"]) == 2


def test_serve_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = 1\n")
    assert cli.main(["serve", "--config", str(bad), "--state-dir", str(tmp_path / "s")]) == 2
    assert "unknown key 'bogus'" in capsys.readouterr().err


def test_serve_starts_answers_and_stops(cfg_file, tmp_path):
    import http.client

    stop = threading.Event()
    addr = []
    ready = threading.Event()

    def on_ready(a):
        addr.append(a)
        ready.set()

    config = cli.load_config(cfg_file)
    t = threading.Thread(
        target=cli.run_server,
        args=(config, tmp_path / "state"),
        kwargs={"ready": on_ready, "stop": stop},
    )
    t.start()
    assert ready.wait(10)
    conn = http.client.HTTPConnection(*addr[0])
    conn.request("GET", "/healthz")
    assert conn.getresponse().status == 200
    conn.close()
    stop.set()
    t.join(10)
    assert not t.is_alive()
    assert (tmp_path / "state").is_dir()


def test_token_add_reports_unwritable_config(cfg_file, capsys):
    cfg_file.chmod(0o444)
    try:
        assert cli.main(["token", "add", "alex", "--config", str(cfg_file)]) == 2
    finally:
        cfg_file.chmod(0o644)
    assert "cannot write config" in capsys.readouterr().err


def test_sigterm_stops_the_server_when_run_on_the_main_thread(cfg_file, tmp_path):
    import os
    import signal

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        cli.run_server(
            cli.load_config(cfg_file),
            tmp_path / "state",
            ready=lambda _addr: os.kill(os.getpid(), signal.SIGTERM),
        )
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
