# syncbaton server

The server holds the lock state for projects in Syncthing folders. It runs on the machine that
runs the always-on Syncthing node and talks to that Syncthing over its REST API. Clients (the
macOS app) talk to the server over HTTP; the contract is in [api.md](api.md).

What it does:

- Keeps, per project, who holds it and when the holder last sent a heartbeat.
- Refuses check-in until its own Syncthing has received everything under the project
  (`GET /rest/db/need`), so the next person never starts from a half-synced copy.
- Records events (checkout, checkin, take_over, conflict) and serves them by long poll.
- Looks for `*.sync-conflict-*` files inside projects every 60 seconds and reports each once.

What it does not do: it does not stop anyone from editing files. Locks are a convention that the
client app follows. It also does not move or serve files; Syncthing does that.

## Install

### With uv or pip

The package is `syncbaton` (Python 3.11 or newer, one dependency: `tenacity`). From a checkout of
this repository:

```sh
uv tool install ./server        # or: pip install ./server
syncbaton --version
```

`uv tool install` puts the executable in `~/.local/bin` of the user who ran it (`uv tool dir --bin`
shows the directory); `pip install` puts it next to the Python it installs into, or in a virtualenv's
`bin`. The systemd unit runs as another user and needs an absolute path it can execute, so either
install system-wide (`sudo UV_TOOL_BIN_DIR=/usr/local/bin UV_TOOL_DIR=/opt/syncbaton uv tool install ./server`)
or change `ExecStart=` in the unit to the path you got. The unit's default is `/usr/local/bin/syncbaton`.

Without installing, `uv run --project server syncbaton ...` works from a checkout. Once the package
is on an index, `uvx syncbaton serve ...` does the same.

### Docker

```sh
docker build -t syncbaton server
docker run --rm \
  --network host \
  --user 1000:1000 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD/syncbaton.toml:/etc/syncbaton.toml:ro" \
  -v /srv/music:/srv/music:ro \
  -v syncbaton-state:/var/lib/syncbaton \
  syncbaton
```

The image runs `syncbaton serve --config /etc/syncbaton.toml --state-dir /var/lib/syncbaton`
as a non-root user. Set `--user` to the uid and gid that can read the synced folder. Paths in the
config are paths inside the container. [examples/docker-compose.yaml](examples/docker-compose.yaml)
is the same setup as a compose file.

### systemd

[examples/syncbaton.service](examples/syncbaton.service) runs it as the user Syncthing runs as,
with `NoNewPrivileges`, `ProtectSystem=strict` and the state directory as the only writable path.

```sh
sudo install -m 0640 -g syncthing syncbaton.toml /etc/syncbaton.toml
sudo cp docs/examples/syncbaton.service /etc/systemd/system/
sudo systemctl enable --now syncbaton
journalctl -u syncbaton -f
```

## Configuration

`syncbaton serve --config /etc/syncbaton.toml --state-dir /var/lib/syncbaton` (these are the
defaults). The file is checked at startup; an unknown key, a bad duration, a missing folder path or
a malformed token hash stops the server with a message that names the problem.

```toml
listen = "127.0.0.1:8790"
stale_after = "15m"

[syncthing]
url = "http://127.0.0.1:8384"
api_key_file = "/var/lib/syncbaton/syncthing-api-key"

[[folder]]
id = "music"
path = "/srv/music"
patterns = ["*.logicx"]

[users]
alex = "<sha256 hex of alex's token>"
sam = "<sha256 hex of sam's token>"

[notify]
ntfy_url = "https://ntfy.example/syncbaton"
webhook_url = "https://hooks.example/syncbaton"
```

| Key | Default | Meaning |
|---|---|---|
| `listen` | `127.0.0.1:8790` | `host:port` to bind. Port `0` picks a free port. |
| `stale_after` | `15m` | A held project whose last heartbeat is older than this can be taken over. A number followed by `s`, `m` or `h`. |
| `[syncthing].url` | required | Base URL of the Syncthing GUI/REST API. `http://` is accepted only for `127.0.0.1`, `::1` and `localhost`; any other host needs `https://`, because the API key is sent with every request. |
| `[syncthing].api_key_file` | required | File holding the Syncthing API key (whitespace around it is ignored). Write it yourself, for example from config management or a systemd `ExecStartPre`. |
| `[[folder]].id` | required | Syncthing folder ID. Also the `{folder}` in API URLs. Must be unique and contain no `/`. |
| `[[folder]].path` | required | Where that folder lives on this machine. Must exist. |
| `[[folder]].patterns` | required | Globs (`fnmatch`, case-sensitive) matched against top-level entries of the folder. A match, directory or file, is a project. |
| `[users]` | empty | User name to sha256 hex of that user's token. |
| `[notify].ntfy_url` | | Each event is POSTed here as a plain-text message with a `Title` header. |
| `[notify].webhook_url` | | Each event is POSTed here as the event JSON from the API. |

Things to know:

- Projects are only top-level entries. `Archive/Old.logicx` is not a project of a folder with
  pattern `*.logicx`. Syncthing conflict copies of a project are not listed as projects; they are
  reported as `conflict` events.
- Project names in URLs must be a single path segment that exists in the folder and matches a
  pattern. `..`, `/`, absolute paths and anything else get `404`.
- Syncthing requests are retried with exponential backoff (0.5 s, 1 s, 2 s) up to four attempts and
  at most 10 seconds in total, with a 4 second timeout per request. A check-in therefore answers
  within about 13 seconds even when Syncthing hangs. If Syncthing still does not answer, check-in
  fails with `503 syncthing_unavailable` and the project stays held. The need list is fetched in
  pages of 5000 entries, because Syncthing returns only the first 65536 entries when no page size
  is given.
- Notification failures are logged and ignored; they never fail a request.
- ntfy and webhook messages contain user names, project names and check-in notes. Use a
  self-hosted or access-controlled ntfy topic and treat the topic URL (and the webhook URL) as a
  secret.
- Stale detection compares the heartbeat with the server's wall clock. A large clock jump forward
  (a manual change, or an NTP step after a long offset) can make held projects look stale early, and
  a jump back delays staleness. Keep the server's clock under NTP.
- The conflict scan walks every project tree once a minute. Projects with very many files make
  that walk cost disk reads each time.
- State is in `state.db` inside the state directory (SQLite). The event log is not pruned.
- Logs go to stderr, one line per request: method, path, status and user. Tokens are never logged.
- The config is read once at startup. Restart after editing it.

## Tokens

Each user has a token. The config holds only its sha256, so a leaked config does not leak tokens.

```sh
syncbaton token add alex --config /etc/syncbaton.toml
```

This generates a token (`secrets.token_urlsafe(32)`), prints it once on stdout and writes its hash
into the `[users]` table of the config file. Only that table's lines change; comments and the rest
of the file stay as they are. Running it again for the same user replaces the hash, which revokes
the old token. Restart the server afterwards. Give the token to the user over a channel you trust;
they paste it into the app.

If a tool manages the config file for you, generate the hash without touching the file:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' | tee token.txt | syncbaton token hash
```

`token hash` reads a token on stdin and prints its sha256 hex.

To remove a user, delete their line from `[users]` and restart.

## Network exposure

The server speaks plain HTTP. Bearer tokens and project names travel unencrypted, so do not put it
on the open internet as is. Pick one:

- **Tailscale (or another WireGuard network).** Set `listen` to the machine's Tailscale address,
  for example `100.64.0.10:8790`, or keep `127.0.0.1:8790` and run
  `tailscale serve --bg 8790` to publish it inside the tailnet. Traffic between devices is
  encrypted by Tailscale. Use ACLs to limit which devices can reach the port.
- **TLS reverse proxy.** Bind syncbaton to `127.0.0.1` and put Caddy, nginx or Traefik in front.
  For long polling, allow responses to stay open for 60 seconds (nginx: `proxy_read_timeout 75s;`
  and `proxy_buffering off;`). Caddy:

  ```
  syncbaton.example.com {
      reverse_proxy 127.0.0.1:8790
  }
  ```

`GET /healthz` needs no token and returns `{"ok": true}`; use it for monitoring.

## Security notes

- A token authorizes every action for that user in every configured folder. There are no
  per-folder permissions. Anyone holding a token can check projects out and in as that user and
  take over stale locks.
- The server reads the synced folders (to list projects and scan for conflicts) and never writes
  to them. Run it as a user with read access only, as in the systemd example.
- The Syncthing API key gives full control of Syncthing. Keep its file readable only by the user
  that runs syncbaton (`chmod 600`).
- Token comparison uses `hmac.compare_digest` against every stored hash. Tokens are 256 bits of
  randomness, so the unsalted sha256 is not a practical target for guessing.
- Request bodies are limited to 64 KiB. There is no rate limiting; put one in the reverse proxy if
  the server is reachable by people you do not trust.
- Locks are advisory. A client that skips check-out can still edit its copy and Syncthing will
  sync it. The check-in gate protects the handover: it keeps the next person from starting before
  the server has everything the previous holder wrote.
