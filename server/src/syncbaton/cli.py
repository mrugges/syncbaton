"""Command line: ``syncbaton serve`` and ``syncbaton token add|hash``."""

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from syncbaton import __version__
from syncbaton.app import App
from syncbaton.config import Config, ConfigError, load_config, read_api_key, read_text
from syncbaton.conflicts import ConflictScanner
from syncbaton.notify import Notifier
from syncbaton.server import make_server
from syncbaton.store import Store
from syncbaton.syncthing import SyncthingClient
from syncbaton.tokens import add_user_hash, hash_token, new_token

DEFAULT_CONFIG = Path("/etc/syncbaton.toml")
DEFAULT_STATE_DIR = Path("/var/lib/syncbaton")

logger = logging.getLogger("syncbaton")


def run_server(
    config: Config,
    state_dir: Path,
    *,
    ready: Callable[[tuple[str, int]], None] | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Serve until ``stop`` is set (or SIGINT/SIGTERM arrives when running on the main thread)."""
    api_key = read_api_key(config.syncthing)
    state_dir.mkdir(parents=True, exist_ok=True)
    store = Store(state_dir / "state.db")
    notifier = Notifier(config.notify)
    store.add_listener(notifier.notify)
    app = App(config, store, SyncthingClient(config.syncthing.url, api_key))
    scanner = ConflictScanner(config.folders, store)
    server = make_server(app, config.listen_host, config.listen_port)
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
    scanner.start()
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    logger.info(
        "listening on %s:%s, %d folder(s), stale_after=%ds",
        host,
        port,
        len(config.folders),
        config.stale_after,
    )
    if ready:
        ready((str(host), int(port)))
    try:
        stop.wait()
    finally:
        logger.info("shutting down")
        server.shutdown()
        server.server_close()
        scanner.stop()
        notifier.close()
        store.close()


def _serve(args: argparse.Namespace) -> int:
    run_server(load_config(args.config), args.state_dir)
    return 0


def _token_add(args: argparse.Namespace) -> int:
    text = read_text(args.config, "config")
    token = new_token()
    try:
        updated = add_user_hash(text, args.user, hash_token(token))
    except ValueError as err:
        raise ConfigError(str(err)) from err
    try:
        args.config.write_text(updated)
    except OSError as err:
        msg = f"cannot write config {args.config}: {err.strerror or err}"
        raise ConfigError(msg) from err
    print(token)
    print(
        f"Token for {args.user} written (as a hash) to {args.config}. "
        "Restart syncbaton to load it. This is the only time the token is shown.",
        file=sys.stderr,
    )
    return 0


def _token_hash(_: argparse.Namespace) -> int:
    token = sys.stdin.read().strip()
    if not token:
        msg = "no token on stdin"
        raise ConfigError(msg)
    print(hash_token(token))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syncbaton", description=__doc__)
    parser.add_argument("--version", action="version", version=f"syncbaton {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    serve.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    serve.set_defaults(func=_serve)

    token = commands.add_parser("token", help="manage API tokens").add_subparsers(
        dest="token_command", required=True
    )
    add = token.add_parser("add", help="create a token for a user and store its hash in the config")
    add.add_argument("user")
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    add.set_defaults(func=_token_add)
    digest = token.add_parser("hash", help="print the sha256 hash of a token read from stdin")
    digest.set_defaults(func=_token_hash)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
