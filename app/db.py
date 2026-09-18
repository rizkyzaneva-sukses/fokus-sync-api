"""Koneksi DB + skema. Postgres via psycopg3 connection pool."""
import os
import time
import logging
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger("fokus.db")

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: ConnectionPool | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Satu baris per (device, kind, key). Sync = last-write-wins pakai updated_at.
CREATE TABLE IF NOT EXISTS record (
    device_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'sprint' | 'session' | 'idea' | 'config' | 'stats'
    key         TEXT NOT NULL,          -- id record di sisi klien
    payload     JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    deleted     BOOLEAN NOT NULL DEFAULT FALSE,
    server_seq  BIGSERIAL,
    PRIMARY KEY (device_id, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_record_kind_seq ON record (kind, server_seq);
CREATE INDEX IF NOT EXISTS idx_record_updated  ON record (updated_at);

-- Audit log tiap push/pull biar gampang debug
CREATE TABLE IF NOT EXISTS sync_log (
    id          BIGSERIAL PRIMARY KEY,
    device_id   TEXT NOT NULL,
    direction   TEXT NOT NULL,          -- 'push' | 'pull'
    n_records   INTEGER NOT NULL DEFAULT 0,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_synclog_device ON sync_log (device_id, at DESC);
"""


def init_pool(retries: int = 10) -> ConnectionPool:
    """Bikin pool koneksi + migrasi skema. Retry kalau DB belum siap."""
    global _pool
    if _pool is not None:
        return _pool

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL kosong")

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            p = ConnectionPool(
                conninfo=DATABASE_URL,
                min_size=1,
                max_size=8,
                kwargs={"row_factory": dict_row},
                timeout=10,
            )
            with p.connection() as conn:
                conn.execute(SCHEMA)
            _pool = p
            log.info("DB pool siap (percobaan %s)", attempt)
            return p
        except Exception as e:            # noqa: BLE001
            last_err = e
            log.warning("DB belum siap (percobaan %s/%s): %s", attempt, retries, e)
            time.sleep(2)

    raise RuntimeError(f"Gagal konek DB setelah {retries} percobaan: {last_err}")


@contextmanager
def get_conn():
    if _pool is None:
        init_pool()
    with _pool.connection() as conn:      # type: ignore[union-attr]
        yield conn