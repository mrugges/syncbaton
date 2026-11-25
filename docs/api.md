# HTTP API

The server and the macOS app both follow this document. All bodies are JSON (`Content-Type:
application/json`), all times are RFC 3339 UTC strings (`2026-01-02T03:04:05Z`).

## Authentication

Every request except `GET /healthz` carries `Authorization: Bearer <token>`. The server stores only
`sha256(token)` (lowercase hex) per user. A missing or unknown token gets `401 {"error":
"unauthorized"}`. The user making the request is the one the token belongs to.

## Errors

Errors are `{"error": "<code>", ...extra}` with these codes:

| Status | `error` | When |
|---|---|---|
| 400 | `bad_request` | Malformed JSON or missing field |
| 401 | `unauthorized` | Missing or unknown token |
| 404 | `not_found` | Unknown folder or project, or unknown route |
| 409 | `held` | Checkout while another user holds the project (extra: `holder`, `since`) |
| 409 | `not_stale` | `take_over` while the holder's heartbeat is still fresh (extra: `holder`, `since`) |
| 409 | `not_holder` | Heartbeat or checkin by a user who does not hold the project |
| 409 | `still_syncing` | Checkin while the server's Syncthing still needs files under the project (extra: `files`, up to 20 paths) |
| 503 | `syncthing_unavailable` | Checkin while the server cannot get an answer from its Syncthing (it retries for up to 10 s first; the response arrives within about 13 s). The project stays held; try again later |

## Routes

### `GET /healthz`
No auth. `200 {"ok": true}`.

### `GET /info`
`200 {"user": "alex", "device_id": "<server's Syncthing device ID>", "folders": [{"id": "music"}], "stale_after_seconds": 900}`

`device_id` is `null` while the server cannot reach its Syncthing; ask again later.

### `GET /folders/{folder}/projects`
`200 {"projects": [Project, ...]}` sorted by name, where

```json
{
  "name": "Song A.logicx",
  "holder": "alex",                    // null when free
  "since": "2026-01-02T03:04:05Z",     // null when free
  "heartbeat": "2026-01-02T03:10:00Z", // null when free
  "stale": false,                      // true when held and heartbeat older than stale_after
  "last_checkin": {"user": "sam", "at": "2026-01-01T20:00:00Z", "note": "new bass"}  // or null
}
```

A project is a top-level entry (directory or file) of the folder's path whose name matches one of the
folder's glob `patterns`. Syncthing conflict copies of a project (`Song A.sync-conflict-...logicx`) are
not projects. Names are URL path segments, percent-encoded. A name that is not a single segment, does
not exist or does not match a pattern is `404 not_found` for every route below.

### `POST /folders/{folder}/projects/{name}/checkout`
Body optional (empty or `{}` is fine): `{"take_over": true}`; `take_over` must be a boolean, else `400`.
- Free, or already held by the caller: `200 Project` (re-checkout refreshes the heartbeat).
- Held by someone else, fresh: `409 held`, or with `take_over`: `409 not_stale`.
- Held by someone else, stale, with `take_over`: `200 Project`, emits `take_over`.

### `POST /folders/{folder}/projects/{name}/heartbeat`
Holder only: `200 Project`. Otherwise `409 not_holder`.

### `POST /folders/{folder}/projects/{name}/checkin`
Body: `{"note": "what changed"}` (note may be empty or omitted; a non-string note is `400`). Holder only, else `409 not_holder`.
The server asks its Syncthing `GET /rest/db/need?folder=<id>` and, if any path in
`progress`/`queued`/`rest` lies under the project, answers `409 still_syncing`. Otherwise releases the
project, records `last_checkin`, `200 Project` (now free).

### `GET /events?since=<id>&wait=<seconds>`
Long poll. Returns as soon as there are events with `id > since`, or after `wait` seconds (max 60,
default 30) with an empty list. `since` defaults to the latest id (so a new client gets only new
events). `since` and `wait` are non-negative integers, else `400`; a larger `wait` is capped at 60. At most 500 events are returned per call; `last_id` is the id of the last one returned. When the list is empty it is `min(since, latest id)`: normally `since` itself, but if the client's `since` is ahead of the server (state was reset) it is the server's latest id, so the next poll does not wait for ids that will never come. Pass it as the next `since`. `200 {"events": [Event, ...], "last_id": 42}`, where

```json
{"id": 43, "at": "...", "type": "checkout|checkin|take_over|conflict", "folder": "music",
 "project": "Song A.logicx", "user": "alex", "note": "new bass", "previous_holder": "sam", "path": "..."}
```

`note` only on `checkin`, `previous_holder` only on `take_over`, `path` only on `conflict` (the
conflict file's path relative to the folder; `user` is null). Each conflict file is reported once.
