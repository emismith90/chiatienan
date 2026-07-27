"""Read-only export API for debugging production (deploy/DEBUGGING.md §6).

The droplet has no ``sqlite3`` binary and outbound SSH is blocked from some
networks (corporate LAN, sandboxed cloud agents), which left "read the prod
chatlog" needing a laptop on a hotspot. These endpoints serve the same data over
plain HTTPS so any agent with the key can pull it: the conversation log, the
ledger tables as CSV, a sanitised SQLite snapshot, and the app log.

Mounted under ``/internal/debug/*`` because Caddy already routes ``/internal/*``
to this backend — so the surface is *publicly reachable* and the key is the only
gate. That shapes every decision here:

* **Separate credential.** ``DEBUG_API_KEY``, not ``ADMIN_PASSWORD``: this
  surface exports the whole room, so it must rotate on its own and be revocable
  by unsetting one var. Unset ⇒ every route 404s, as if it did not exist.
* **Constant-time compare** and a minimum key length, so a lazy
  ``DEBUG_API_KEY=test`` cannot silently expose the ledger.
* **Read-only.** No route mutates; the SQLite snapshot is taken through
  ``Connection.backup`` (WAL-safe) against a throwaway copy.
* **Secrets never leave.** ``sessions`` holds live bearer tokens — the table is
  not exportable at any granularity, and the snapshot ships with it emptied.
  ``rooms.invite_token``, ``members.pin`` and ``members.account_number`` are
  redacted everywhere. See :data:`_REDACT`.

Redaction is deliberately not opt-out-able: a debugging convenience must not
double as a credential exfiltration path.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
import sqlite3
import tempfile
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from app.clock import now_ict
from app.config import settings
from app.db import get_db
from app.models import Base, Member, Room, RoomMessage

log = logging.getLogger("chiatienan.debug")

router = APIRouter(prefix="/internal/debug", tags=["debug"])

#: Shortest key we will accept. Long enough that a placeholder like "test" or
#: "changeme" disables the API (loudly) instead of publishing the ledger.
MIN_KEY_LEN = 24

#: Live bearer tokens. Excluded from every export path, with no override.
_FORBIDDEN_TABLES = {"sessions"}

#: ``table -> columns`` replaced with ``[redacted]`` in CSV and snapshot output.
#: Not secrets in the cryptographic sense, but each one is enough to take over
#: or join a room, or is bank PII that debugging never needs.
_REDACT = {
    "rooms": {"invite_token"},
    "members": {"pin", "account_number"},
}

_PLACEHOLDER = "[redacted]"


def dumpable_tables() -> list[str]:
    """Exportable table names, ``sessions`` removed."""
    return sorted(set(Base.metadata.tables) - _FORBIDDEN_TABLES)


def require_key(x_debug_key: str | None = Header(default=None)) -> None:
    """Gate every route on ``DEBUG_API_KEY``.

    A missing/short configured key means the feature is off, and we answer 404
    rather than 401 so an unconfigured deploy does not advertise that these
    routes exist. A *wrong* key gets 401 and a warning line — the log is how you
    notice someone probing, so it records the attempt but never the key.
    """
    configured = settings.debug_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="not found")
    if len(configured) < MIN_KEY_LEN:
        log.error(
            "DEBUG_API_KEY is set but shorter than %d chars — refusing to enable "
            "the export API. Generate one with: python -c "
            "'import secrets; print(secrets.token_urlsafe(32))'",
            MIN_KEY_LEN,
        )
        raise HTTPException(status_code=404, detail="not found")
    if not x_debug_key or not hmac.compare_digest(x_debug_key, configured):
        log.warning("debug API: rejected request with missing/invalid X-Debug-Key")
        raise HTTPException(status_code=401, detail="invalid or missing X-Debug-Key")


def scrub_attachments(att, *, keep_images: bool = False):
    """Strip base64 image payloads out of a message's ``attachments`` JSON.

    Draft attachments carry the numbers worth debugging (``items``,
    ``adjustments``, ``bill_total``), so the structure is preserved — but an
    inline bill photo is megabytes of base64 that would swamp a CSV and drag a
    diner's receipt into it. Each image becomes
    ``{"omitted": true, "bytes": N, ...}`` keeping the non-payload metadata.
    """
    if not isinstance(att, dict):
        return att
    images = att.get("images")
    if not isinstance(images, list) or keep_images:
        return att
    trimmed = []
    for img in images:
        if not isinstance(img, dict):
            trimmed.append(img)
            continue
        data = img.get("data") or ""
        meta = {k: v for k, v in img.items() if k != "data"}
        trimmed.append({**meta, "omitted": True, "bytes": len(data)})
    return {**att, "images": trimmed}


def _stream_csv(header: list[str], rows) -> StreamingResponse:
    """Stream rows as CSV without buffering the whole dump in memory."""
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        yield buf.getvalue()
        for row in rows:
            buf.seek(0), buf.truncate(0)
            w.writerow(["" if v is None else v for v in row])
            yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8")


def _sqlite_path() -> str:
    """Filesystem path behind ``DATABASE_URL``, or 400 for non-SQLite."""
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise HTTPException(status_code=400, detail="snapshot only supported for SQLite")
    path = url.split("///")[-1]
    if not path.startswith("/"):
        path = "/" + path
    return path


@router.get("/ping")
async def ping(_=Header(default=None, alias="X-Debug-Key")):
    """Cheap "is my key right, and what's in there" check."""
    require_key(_)
    with get_db().session() as s:
        return {
            "status": "ok",
            "rooms": s.scalar(select(func.count()).select_from(Room)),
            "messages": s.scalar(select(func.count()).select_from(RoomMessage)),
            "members": s.scalar(select(func.count()).select_from(Member)),
            "tables": dumpable_tables(),
            "log_file_present": bool(settings.log_file and os.path.exists(settings.log_file)),
        }


@router.get("/rooms")
async def list_rooms(_=Header(default=None, alias="X-Debug-Key")):
    """Rooms with message/member counts — how you find the real group's id."""
    require_key(_)
    with get_db().session() as s:
        out = []
        for r in s.scalars(select(Room).order_by(Room.id)):
            out.append({
                "id": r.id,
                "name": r.name,
                "created_at": str(r.created_at),
                "members": s.scalar(
                    select(func.count()).select_from(Member).where(Member.room_id == r.id)),
                "messages": s.scalar(
                    select(func.count()).select_from(RoomMessage).where(RoomMessage.room_id == r.id)),
            })
        return {"rooms": out}


def _message_rows(s, room_id: int | None, days: int | None, limit: int,
                  since_id: int | None, keep_images: bool):
    """Chatlog rows oldest→newest, with author names resolved."""
    names = {m.id: m.display_name for m in s.scalars(select(Member))}
    q = select(RoomMessage)
    if room_id is not None:
        q = q.where(RoomMessage.room_id == room_id)
    if since_id is not None:
        q = q.where(RoomMessage.id > since_id)
    if days is not None:
        # Bound in SQL: SQLite hands back naive datetimes, so an in-Python
        # compare against aware now_ict() would raise (see chat.py).
        q = q.where(RoomMessage.created_at >= now_ict() - timedelta(days=days))
    # Newest-first + limit so a cap keeps the *recent* tail, then flip to
    # chronological for reading.
    rows = s.scalars(q.order_by(RoomMessage.id.desc()).limit(limit)).all()
    for r in reversed(rows):
        att = scrub_attachments(r.attachments, keep_images=keep_images)
        yield r, names.get(r.author_member_id, "BOT" if r.author_member_id is None else "?"), att


@router.get("/conversation.csv")
async def conversation_csv(
    room_id: int | None = None,
    days: int | None = None,
    limit: int = Query(default=5000, ge=1, le=100_000),
    since_id: int | None = None,
    keep_images: bool = False,
    _=Header(default=None, alias="X-Debug-Key"),
):
    """The conversation log as CSV — the table you actually want for debugging.

    ``since_id`` makes polling cheap (pass the last id you saw). ``keep_images``
    restores base64 payloads; off by default because one bill photo dwarfs the
    entire rest of the log.
    """
    require_key(_)

    def rows():
        with get_db().session() as s:
            for r, author, att in _message_rows(s, room_id, days, limit, since_id, keep_images):
                yield [r.id, r.room_id, str(r.created_at), r.kind, r.author_member_id or "",
                       author, r.body or "",
                       json.dumps(att, ensure_ascii=False) if att else ""]

    return _stream_csv(
        ["id", "room_id", "created_at", "kind", "author_member_id", "author", "body", "attachments"],
        rows(),
    )


@router.get("/conversation.txt", response_class=PlainTextResponse)
async def conversation_txt(
    room_id: int | None = None,
    days: int | None = None,
    limit: int = Query(default=2000, ge=1, le=50_000),
    since_id: int | None = None,
    _=Header(default=None, alias="X-Debug-Key"),
):
    """Same log as a flat transcript — the readable form for spotting frictions.

    Mirrors the ``sqlite3`` dump in DEBUGGING.md §2:
    ``<ts>  [kind]  Name:  body``, with an ``[ảnh: N]`` marker so a pasted bill
    is visible instead of silently absent.
    """
    require_key(_)
    out: list[str] = []
    with get_db().session() as s:
        for r, author, att in _message_rows(s, room_id, days, limit, since_id, False):
            body = (r.body or "").replace("\n", " ")
            n_img = len((att or {}).get("images") or [])
            if n_img:
                body = f"{body} [ảnh: {n_img}]".strip()
            if r.kind not in ("text", "bot") and att:
                body = f"{body} {json.dumps(att, ensure_ascii=False)}".strip()
            out.append(f"{r.created_at}  [{r.kind}]  {author}:  {body}")
    return "\n".join(out) + ("\n" if out else "")


@router.get("/tables")
async def tables(_=Header(default=None, alias="X-Debug-Key")):
    """Exportable tables, row counts, and which columns come back redacted."""
    require_key(_)
    with get_db().session() as s:
        info = {}
        for name in dumpable_tables():
            t = Base.metadata.tables[name]
            info[name] = {
                "rows": s.scalar(select(func.count()).select_from(t)),
                "columns": [c.name for c in t.columns],
                "redacted": sorted(_REDACT.get(name, set())),
            }
        return {"tables": info, "excluded": sorted(_FORBIDDEN_TABLES)}


@router.get("/tables/{table}.csv")
async def table_csv(
    table: str,
    room_id: int | None = None,
    limit: int = Query(default=100_000, ge=1, le=1_000_000),
    _=Header(default=None, alias="X-Debug-Key"),
):
    """Dump one ledger table as CSV, redacted.

    ``table`` is matched against the SQLAlchemy metadata whitelist rather than
    interpolated, so there is no way to reach ``sessions`` (or anything else)
    through this parameter.
    """
    require_key(_)
    if table in _FORBIDDEN_TABLES or table not in Base.metadata.tables:
        raise HTTPException(status_code=404, detail=f"unknown table (see /internal/debug/tables)")
    t = Base.metadata.tables[table]
    redact = _REDACT.get(table, set())
    cols = [c.name for c in t.columns]

    def rows():
        with get_db().session() as s:
            q = select(t)
            if room_id is not None and "room_id" in t.columns:
                q = q.where(t.c.room_id == room_id)
            for row in s.execute(q.limit(limit)):
                m = row._mapping
                vals = []
                for c in cols:
                    v = m[c]
                    if c in redact:
                        vals.append(_PLACEHOLDER if v is not None else "")
                    elif c == "attachments":
                        v = scrub_attachments(v)
                        vals.append(json.dumps(v, ensure_ascii=False) if v else "")
                    elif isinstance(v, (dict, list)):
                        vals.append(json.dumps(v, ensure_ascii=False))
                    else:
                        vals.append(v)
                yield vals

    return _stream_csv(cols, rows())


def _cleanup_snapshot(path: str) -> None:
    """Remove a temp snapshot and any WAL/SHM sidecars it left behind."""
    for p in (path, f"{path}-wal", f"{path}-shm"):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


@router.get("/db")
async def db_snapshot(
    keep_images: bool = False,
    _=Header(default=None, alias="X-Debug-Key"),
):
    """A sanitised, WAL-safe SQLite snapshot to analyse locally.

    Uses ``Connection.backup``, which captures committed + WAL state
    consistently — so unlike ``scp``-ing the ``.db`` you cannot get a file that
    is stale by a checkpoint, and you do not need the ``-wal`` sidecar.

    The copy is then scrubbed *before* it is served: ``sessions`` emptied,
    :data:`_REDACT` columns nulled, and (by default) base64 image payloads
    dropped, which is usually most of the file size. What you download is a
    normal SQLite file you can open with any tool and that carries no
    credentials.
    """
    require_key(_)
    src = _sqlite_path()
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail=f"database not found at {src}")

    fd, tmp = tempfile.mkstemp(prefix="chiatienan-snapshot-", suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s_con, \
                sqlite3.connect(tmp) as d_con:
            s_con.backup(d_con)

        with sqlite3.connect(tmp) as con:
            con.execute("DELETE FROM sessions")
            for table, cols in _REDACT.items():
                for col in cols:
                    # Overwrite rather than NULL: `rooms.invite_token` is NOT
                    # NULL *and* UNIQUE, so nulling it fails and a constant
                    # would collide across rows. The rowid suffix keeps every
                    # value distinct and the column populated. Only non-NULL
                    # values are touched, so a NULL `members.pin` still means
                    # "unclaimed account" in the snapshot.
                    con.execute(
                        f"UPDATE {table} SET {col} = '{_PLACEHOLDER}-' || rowid "  # noqa: S608 - whitelisted names
                        f"WHERE {col} IS NOT NULL")
            if not keep_images:
                rows = con.execute(
                    "SELECT id, attachments FROM room_messages "
                    "WHERE attachments IS NOT NULL AND attachments LIKE '%\"images\"%'"
                ).fetchall()
                for mid, raw in rows:
                    try:
                        att = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    con.execute("UPDATE room_messages SET attachments = ? WHERE id = ?",
                                (json.dumps(scrub_attachments(att), ensure_ascii=False), mid))
            con.commit()
            # `backup` copies the source page-for-page, so the snapshot inherits
            # journal_mode=WAL — which means every scrub above landed in
            # `tmp-wal`, not in the file we are about to serve. Checkpoint and
            # leave WAL so the download is a single self-contained file with the
            # redactions actually in it. (This is the same trap DEBUGGING.md
            # calls out for `scp`, one layer down.)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("VACUUM")
    except Exception:
        _cleanup_snapshot(tmp)
        raise

    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename="chiatienan-snapshot.db",
        background=BackgroundTask(_cleanup_snapshot, tmp),
    )


def _tail(path: str, lines: int, max_bytes: int = 8_000_000) -> str:
    """Last ``lines`` lines of ``path``, reading from the end."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        block, data, found = 65536, b"", 0
        pos = size
        while pos > 0 and found <= lines and (size - pos) < max_bytes:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step)
            data = chunk + data
            found = data.count(b"\n")
        return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", "replace")


@router.get("/logs", response_class=PlainTextResponse)
async def logs(
    lines: int = Query(default=500, ge=1, le=50_000),
    _=Header(default=None, alias="X-Debug-Key"),
):
    """Tail the app log.

    Reads the file mirror configured by ``LOG_FILE`` (set up in ``main``), since
    ``docker compose logs`` is captured by the daemon and unreadable from inside
    the container. If the mirror is off or has not been written yet, say so and
    name the alternative rather than returning a confusing empty body.
    """
    require_key(_)
    path = settings.log_file
    if not path:
        raise HTTPException(status_code=404, detail="LOG_FILE is unset — file logging disabled")
    if not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail=f"no log file at {path} yet — restart the backend to start the mirror, "
                   f"or read `docker compose logs backend` on the droplet",
        )
    return _tail(path, lines)
