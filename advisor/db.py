"""Persistenta SQLite. Schema este creata idempotent la pornire."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, location TEXT NOT NULL, label TEXT,
  created_at TEXT, last_sync TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, started TEXT, finished TEXT,
  added INTEGER DEFAULT 0, modified INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
  unchanged INTEGER DEFAULT 0, errors_json TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, source_id TEXT, external_id TEXT, filename TEXT, mime TEXT,
  content_hash TEXT, size INTEGER, modified_time TEXT, revision TEXT, stored_path TEXT,
  status TEXT, error TEXT, insurer TEXT, product TEXT, doc_type TEXT, version_label TEXT,
  effective_date TEXT, effective_date_evidence TEXT, hierarchy_level INTEGER,
  metadata_json TEXT, metadata_overridden INTEGER DEFAULT 0, page_count INTEGER,
  ocr_pages INTEGER DEFAULT 0, injection_flags TEXT, ingested_at TEXT, deleted_at TEXT,
  warnings_json TEXT, superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_ext ON documents(source_id, external_id);
CREATE TABLE IF NOT EXISTS clauses (
  id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER, number TEXT, title TEXT,
  section_path TEXT, kind TEXT, text TEXT, page_start INTEGER, page_end INTEGER,
  location_label TEXT
);
CREATE INDEX IF NOT EXISTS ix_clauses_doc ON clauses(document_id);
CREATE TABLE IF NOT EXISTS doc_relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_a TEXT, doc_b TEXT, relation TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS analyses (
  id TEXT PRIMARY KEY, created_at TEXT, username TEXT, incident_enc TEXT,
  facts_json TEXT, answers_json TEXT, result_json TEXT, methodology_version INTEGER, mode TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, reviewer TEXT, analysis_id TEXT,
  product_key TEXT, clause_id TEXT, kind TEXT, content_json TEXT, status TEXT,
  validated_by TEXT, validated_at TEXT
);
CREATE TABLE IF NOT EXISTS methodology (
  version INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, created_by TEXT,
  config_json TEXT, status TEXT, approved_by TEXT, approved_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, username TEXT, role TEXT, action TEXT,
  target TEXT, detail_json TEXT
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def reset_connection() -> None:
    """Folosit in teste pentru a schimba directorul de date."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def q(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | list = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
