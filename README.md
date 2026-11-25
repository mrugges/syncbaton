# syncbaton

Check-out / check-in locking for projects in Syncthing-synced folders.

Two people sharing Logic Pro projects through Syncthing can both save the same project. Syncthing then
writes `.sync-conflict` files inside the `.logicx` package and the project can stop opening. syncbaton
makes one person the holder of a project at a time, and hands it over only after the holder's changes
have reached the always-on Syncthing node.

## How it works

- **Server** (`server/`): runs next to the always-on Syncthing node and is the only lock authority. It
  tracks who holds each project, marks a lock stale when its holder's heartbeats stop (it is released
  only by check-in or take-over), and reports conflict files. HTTP API: [docs/api.md](docs/api.md).
- **macOS app** (`macos/`): a menu bar app. It talks to the server and to the Syncthing running on your
  Mac.

A project is a top-level entry of a synced folder that matches the folder's patterns, for example
`Song A.logicx`.

1. **Check out.** Press Open. The app takes the lock, waits until your Syncthing has downloaded
   everything, then opens the project. While it is open the app sends a heartbeat every minute.
2. **Work.** The other person sees the project as held by you and can't check it out. If they open it
   anyway, the app warns them to close it without saving.
3. **Check in.** When you close the project (or press Done) the app asks for a note, tells Syncthing to
   scan the project, and waits until the server's Syncthing has everything.
4. **Sync gate.** The server refuses the check-in while its Syncthing still needs files under the
   project. The app retries until the server accepts. Only then is the project free, and the other
   person gets a notification with your note.

If a holder disappears (laptop closed, no heartbeat for `stale_after`), the project shows as stale
and the other person can take it over. The previous holder is notified.

## Quick start

### Server

See [docs/server.md](docs/server.md). You need the server URL, for example
`https://syncbaton.example`, and one token per user. The app accepts plain `http://` only for this
Mac, Tailscale addresses (100.64.0.0/10, fd7a:115c:a1e0::/48) and `*.ts.net` names; any other server
needs `https://`, and Settings refuses to save otherwise.

### macOS app

Requires macOS 15 or later and Syncthing running on your Mac with the shared folder added.

1. Download `Syncbaton.zip` from the releases page and unzip it.
2. Move `Syncbaton.app` to `/Applications`.
3. The app is ad-hoc signed, not notarized. On first launch, right-click the app, choose Open, then
   confirm.
4. Settings opens on first run. Enter the server URL and your token (stored in the Keychain). The app
   reads the Syncthing API key and folder paths from
   `~/Library/Application Support/Syncthing/config.xml`.
5. For Logic Pro, allow Accessibility access when asked (System Settings, Privacy & Security,
   Accessibility). The app uses it to read Logic's window titles and notice when you close a project.
   Without it, you press Done yourself.
6. Allow notifications.

Build from source: `cd macos && swift build`, `swift test`, and `scripts/bundle.sh` to produce
`build/Syncbaton.app` and `build/Syncbaton.zip`.

## Supported apps

| App | Open | Detects close |
|---|---|---|
| Logic Pro (`.logicx`) | yes | yes, needs Accessibility |
| Anything else | yes, with the default app | no, press Done |

To add an adapter, implement `AppAdapter` in `macos/Sources/SyncbatonCore` and register it by file
extension in `AdapterRegistry`. Contributions are welcome.

## Working tips for Logic

- Keep one project per song. The lock is per project, so a shared project holds up both people.
- Consolidate project assets (File, Project Management, Consolidate) before handing over, so the package contains
  every audio file.
- Use the same Logic version and the same sample rate on both machines.
- Freeze or bounce tracks that use third-party plugins your partner doesn't have.
- Close Logic's project before checking in. The check-in waits for files to sync, and Logic keeps
  writing to the package while the project is open.

## License

MIT. See [LICENSE](LICENSE).
