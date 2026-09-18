"""API sinkronisasi FOKUS.

Model: offline-first. Klien (fokus.pyw) tetap simpan data lokal di JSON.
Server hanya jadi tempat bertemunya. Resolusi konflik = last-write-wins
per record, dibandingkan pakai kolom updated_at.

Auth: header X-Sync-Key harus sama dengan env SYNC_KEY.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fokus.api")

SYNC_KEY = os.getenv("SYNC_KEY", "")
KINDS = {"sprint", "session", "idea", "config", "stats", "note"}

app = FastAPI(title="FOKUS Sync API", version="1.0.0")


# ---------- model ----------
class RecordIn(BaseModel):
    kind: str
    key: str
    payload: dict[str, Any]
    updated_at: datetime
    deleted: bool = False


class PushIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    device_name: str = ""
    platform: str = ""
    records: list[RecordIn] = Field(default_factory=list)


# ---------- auth ----------
def auth(x_sync_key: str = Header(default="", alias="X-Sync-Key")) -> None:
    if not SYNC_KEY:
        raise HTTPException(500, "SYNC_KEY belum diset di server")
    # bandingkan konstan-waktu
    import hmac
    if not hmac.compare_digest(x_sync_key, SYNC_KEY):
        raise HTTPException(401, "sync-key salah")


@app.on_event("startup")
def _startup() -> None:
    db.init_pool()
    log.info("FOKUS Sync API siap")


# ---------- endpoints ----------
@app.get("/")
def root() -> dict[str, Any]:
    """Halaman depan API — biar gak bingung kalau domain dibuka manual."""
    return {
        "service": "fokus-sync",
        "desc": "API sinkronisasi antar-PC untuk FokusApp",
        "status": "ok",
        "docs": "/docs",
        "endpoints": {
            "GET  /": "info ini",
            "GET  /health": "cek server + DB",
            "POST /push": "kirim record (butuh X-Sync-Key)",
            "GET  /pull": "tarik record device lain (butuh X-Sync-Key)",
            "GET  /devices": "daftar device terdaftar",
            "GET  /stats": "ringkasan jumlah record",
        },
        "pakai_dari_app": "kamu gak perlu buka URL ini — pakai tombol ⬆Push / ⬇Pull di FokusApp",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    """Cek server + DB hidup. Dipakai Healthcheck & klien."""
    out: dict[str, Any] = {"ok": True, "service": "fokus-sync"}
    try:
        with db.get_conn() as conn:
            row = conn.execute("SELECT now() AS now, count(*) AS n FROM record").fetchone()
            out["db"] = "ok"
            out["server_time"] = row["now"].isoformat()
            out["records"] = row["n"]
    except Exception as e:                # noqa: BLE001
        out["ok"] = False
        out["db"] = f"error: {e}"
        return JSONResponse(out, status_code=503)
    return out


@app.post("/push", dependencies=[Depends(auth)])
def push(body: PushIn) -> dict[str, Any]:
    """Kirim record dari klien. Last-write-wins per (kind, key)."""
    if not body.records:
        return {"ok": True, "applied": 0, "skipped": 0}

    bad = [r.kind for r in body.records if r.kind not in KINDS]
    if bad:
        raise HTTPException(400, f"kind tidak dikenal: {sorted(set(bad))}")

    applied = skipped = 0
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO device (id, name, platform, last_seen)
               VALUES (%s, %s, %s, now())
               ON CONFLICT (id) DO UPDATE
                 SET name = EXCLUDED.name,
                     platform = EXCLUDED.platform,
                     last_seen = now()""",
            (body.device_id, body.device_name, body.platform),
        )

        for r in body.records:
            ts = r.updated_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            # Last-write-wins: hanya tulis kalau updated_at lebih baru.
            cur = conn.execute(
                """INSERT INTO record (device_id, kind, key, payload, updated_at, deleted)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (device_id, kind, key) DO UPDATE
                     SET payload    = EXCLUDED.payload,
                         updated_at = EXCLUDED.updated_at,
                         deleted    = EXCLUDED.deleted
                     WHERE record.updated_at < EXCLUDED.updated_at
                   RETURNING 1""",
                (body.device_id, r.kind, r.key, Jsonb(r.payload), ts, r.deleted),
            ).fetchone()
            if cur:
                applied += 1
            else:
                skipped += 1

        conn.execute(
            "INSERT INTO sync_log (device_id, direction, n_records) VALUES (%s,'push',%s)",
            (body.device_id, applied),
        )

    log.info("push device=%s applied=%s skipped=%s", body.device_id, applied, skipped)
    return {"ok": True, "applied": applied, "skipped": skipped}


@app.get("/pull", dependencies=[Depends(auth)])
def pull(
    device_id: str = Query(..., min_length=1),
    since_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    """Ambil record yang lebih baru dari since_seq (kecuali milik device sendiri).

    Klien menyimpan server_seq terakhir; panggilan berikutnya pakai since_seq itu.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT kind, key, device_id, payload, updated_at, deleted, server_seq
               FROM record
               WHERE server_seq > %s AND device_id <> %s
               ORDER BY server_seq
               LIMIT %s""",
            (since_seq, device_id, limit),
        ).fetchall()

        maxseq = conn.execute("SELECT COALESCE(max(server_seq), 0) AS m FROM record").fetchone()["m"]

        conn.execute(
            "INSERT INTO sync_log (device_id, direction, n_records) VALUES (%s,'pull',%s)",
            (device_id, len(rows)),
        )
        conn.execute(
            """INSERT INTO device (id, last_seen) VALUES (%s, now())
               ON CONFLICT (id) DO UPDATE SET last_seen = now()""",
            (device_id,),
        )

    return {
        "ok": True,
        "server_seq": maxseq,
        "count": len(rows),
        "records": [
            {
                "kind": r["kind"],
                "key": r["key"],
                "device_id": r["device_id"],
                "payload": r["payload"],
                "updated_at": r["updated_at"].isoformat(),
                "deleted": r["deleted"],
                "server_seq": r["server_seq"],
            }
            for r in rows
        ],
    }


@app.get("/devices", dependencies=[Depends(auth)])
def devices() -> dict[str, Any]:
    """Daftar PC yang pernah sync. Berguna buat cek sinkronisasi jalan."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.platform, d.last_seen,
                      (SELECT count(*) FROM record r WHERE r.device_id = d.id) AS n_records
               FROM device d ORDER BY d.last_seen DESC"""
        ).fetchall()
    return {
        "ok": True,
        "devices": [
            {
                "id": r["id"],
                "name": r["name"],
                "platform": r["platform"],
                "last_seen": r["last_seen"].isoformat(),
                "records": r["n_records"],
            }
            for r in rows
        ],
    }


@app.get("/stats", dependencies=[Depends(auth)])
def stats() -> dict[str, Any]:
    """Ringkasan isi server — buat memastikan data benar-benar masuk."""
    with db.get_conn() as conn:
        per_kind = conn.execute(
            "SELECT kind, count(*) AS n FROM record WHERE NOT deleted GROUP BY kind ORDER BY kind"
        ).fetchall()
        tot = conn.execute(
            "SELECT count(*) AS n FROM record WHERE NOT deleted"
        ).fetchone()["n"]
        devs = conn.execute("SELECT count(*) AS n FROM device").fetchone()["n"]
        last = conn.execute("SELECT max(at) AS a FROM sync_log").fetchone()["a"]
    return {
        "ok": True,
        "total_records": tot,
        "per_kind": {r["kind"]: r["n"] for r in per_kind},
        "devices": devs,
        "last_sync": last.isoformat() if last else None,
    }