"""Sincronizare sursa -> ingestie -> clauze -> relatii -> index.

Detecteaza fisiere noi, modificate si sterse. Versiunile anterioare ale unui
fisier modificat NU sunt sterse: raman marcate 'superseded', cu originalul
criptat pastrat, pentru trasabilitatea analizelor vechi.
"""
from __future__ import annotations

import hashlib
import uuid

from . import db, security
from .clauses import extract_clauses
from .config import settings
from .drive import RemoteFile, SourceError, make_source, SUPPORTED_MIMES
from .metadata import extract_metadata
from .parsing import ValidationError, parse, scan_injection
from . import versions


def register_source(kind: str, location: str, label: str = "") -> dict:
    existing = db.q1("SELECT * FROM sources WHERE kind=? AND location=?", (kind, location))
    if existing:
        return existing
    sid = uuid.uuid4().hex[:12]
    with db.tx() as c:
        c.execute("INSERT INTO sources(id, kind, location, label, created_at, status) VALUES (?,?,?,?,?,?)",
                  (sid, kind, location, label or location, db.now(), "nou"))
    return db.q1("SELECT * FROM sources WHERE id=?", (sid,))


def _store_original(doc_id: str, data: bytes) -> str:
    settings.originals_dir.mkdir(parents=True, exist_ok=True)
    path = settings.originals_dir / f"{doc_id}.bin"
    path.write_bytes(security.encrypt_bytes(data))
    return str(path)


def read_original(doc: dict) -> bytes:
    with open(doc["stored_path"], "rb") as f:
        return security.decrypt_bytes(f.read())


def ingest_file(source_id: str, f: RemoteFile, data: bytes) -> dict:
    doc_id = uuid.uuid4().hex[:16]
    content_hash = hashlib.sha256(data).hexdigest()
    base = dict(id=doc_id, source_id=source_id, external_id=f.external_id, filename=f.path or f.name,
                mime=f.mime, content_hash=content_hash, size=len(data), modified_time=f.modified_time,
                revision=f.revision or f.checksum, ingested_at=db.now())
    try:
        kind, parsed = parse(data, f.mime, f.name)
    except ValidationError as e:
        return {**base, "status": "error", "error": str(e)}
    except Exception as e:  # PDF corupt, criptat etc.
        return {**base, "status": "error", "error": f"Eroare la parsare: {type(e).__name__}: {e}"}
    text = parsed.full_text
    if len(text.strip()) < 50:
        return {**base, "status": "error", "error": "Nu s-a putut extrage text (document gol sau OCR esuat).",
                "warnings": parsed.warnings}
    meta = extract_metadata(f.name, text)
    clauses = extract_clauses(parsed.blocks)
    stored = _store_original(doc_id, data)
    inj = scan_injection(text)
    return {**base, "status": "ok", "error": None, "stored_path": stored, "meta": meta, "clauses": clauses,
            "page_count": parsed.page_count, "ocr_pages": parsed.ocr_pages, "warnings": parsed.warnings,
            "injection": inj}


def _save(rec: dict) -> None:
    meta = rec.get("meta") or {}
    with db.tx() as c:
        c.execute(
            """INSERT INTO documents(id, source_id, external_id, filename, mime, content_hash, size, modified_time,
               revision, stored_path, status, error, insurer, product, doc_type, version_label, effective_date,
               effective_date_evidence, hierarchy_level, metadata_json, page_count, ocr_pages, injection_flags,
               ingested_at, warnings_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["id"], rec["source_id"], rec["external_id"], rec["filename"], rec["mime"], rec["content_hash"],
             rec["size"], rec["modified_time"], rec["revision"], rec.get("stored_path"), rec["status"],
             rec.get("error"), meta.get("insurer"), meta.get("product"), meta.get("doc_type"),
             meta.get("version_label"), meta.get("effective_date"),
             (meta.get("evidence") or {}).get("effective_date"), meta.get("hierarchy_level"),
             db.dumps(meta), rec.get("page_count"), rec.get("ocr_pages", 0), db.dumps(rec.get("injection") or []),
             rec["ingested_at"], db.dumps(rec.get("warnings") or [])))
        for cl in rec.get("clauses") or []:
            c.execute(
                """INSERT INTO clauses(id, document_id, ordinal, number, title, section_path, kind, text,
                   page_start, page_end, location_label) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{rec['id']}:{cl.ordinal}", rec["id"], cl.ordinal, cl.number, cl.title, cl.section_path,
                 cl.kind, cl.text, cl.page_start, cl.page_end, cl.location_label))


def sync_source(source_id: str, user: dict | None = None, source_obj=None) -> dict:
    src = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        raise SourceError("Sursa inexistenta")
    run = {"added": 0, "modified": 0, "deleted": 0, "unchanged": 0, "errors": []}
    started = db.now()
    try:
        source = source_obj or make_source(src["kind"], src["location"])
        remote = source.list_files()
    except Exception as e:
        with db.tx() as c:
            c.execute("UPDATE sources SET status=? WHERE id=?", (f"eroare: {e}", source_id))
            c.execute("INSERT INTO sync_runs(source_id, started, finished, errors_json, status) VALUES (?,?,?,?,?)",
                      (source_id, started, db.now(), db.dumps([str(e)]), "esuat"))
        security.audit(user, "sync.failed", source_id, {"error": str(e)})
        raise

    current = {d["external_id"]: d for d in db.q(
        "SELECT * FROM documents WHERE source_id=? AND deleted_at IS NULL AND superseded_by IS NULL", (source_id,))}
    seen = set()
    for f in remote:
        seen.add(f.external_id)
        prev = current.get(f.external_id)
        if f.mime not in SUPPORTED_MIMES and not f.name.lower().endswith((".pdf", ".docx")):
            if not prev or prev.get("error") is None:
                run["errors"].append({"fisier": f.path or f.name, "eroare": f"Tip nesuportat: {f.mime}"})
            continue
        if prev and prev["revision"] == (f.revision or f.checksum) and prev["status"] == "ok":
            run["unchanged"] += 1
            continue
        try:
            data = source.fetch(f)
        except Exception as e:
            run["errors"].append({"fisier": f.path or f.name, "eroare": f"Descarcare esuata: {e}"})
            continue
        if prev and prev["content_hash"] == hashlib.sha256(data).hexdigest():
            with db.tx() as c:
                c.execute("UPDATE documents SET revision=?, modified_time=? WHERE id=?",
                          (f.revision or f.checksum, f.modified_time, prev["id"]))
            run["unchanged"] += 1
            continue
        rec = ingest_file(source_id, f, data)
        _save(rec)
        if rec["status"] != "ok":
            run["errors"].append({"fisier": rec["filename"], "eroare": rec["error"]})
        if prev:
            with db.tx() as c:
                c.execute("UPDATE documents SET superseded_by=?, status=CASE WHEN status='ok' THEN 'superseded' ELSE status END WHERE id=?",
                          (rec["id"], prev["id"]))
            run["modified"] += 1
        else:
            run["added"] += 1
    for ext_id, d in current.items():
        if ext_id not in seen:
            with db.tx() as c:
                c.execute("UPDATE documents SET deleted_at=?, status='deleted' WHERE id=?", (db.now(), d["id"]))
            run["deleted"] += 1

    versions.rebuild_relations()
    from .retrieval import index
    index.invalidate()
    status = "ok" if not run["errors"] else f"ok cu {len(run['errors'])} erori"
    with db.tx() as c:
        c.execute("UPDATE sources SET last_sync=?, status=? WHERE id=?", (db.now(), status, source_id))
        c.execute("""INSERT INTO sync_runs(source_id, started, finished, added, modified, deleted, unchanged,
                     errors_json, status) VALUES (?,?,?,?,?,?,?,?,?)""",
                  (source_id, started, db.now(), run["added"], run["modified"], run["deleted"], run["unchanged"],
                   db.dumps(run["errors"]), status))
    security.audit(user, "sync.completed", source_id, {k: v for k, v in run.items() if k != "errors"} | {"erori": len(run["errors"])})
    return run | {"status": status}


def override_metadata(doc_id: str, changes: dict, user: dict) -> dict:
    allowed = {"insurer", "product", "doc_type", "version_label", "effective_date"}
    changes = {k: (v or None) for k, v in changes.items() if k in allowed}
    if not changes:
        return db.q1("SELECT * FROM documents WHERE id=?", (doc_id,))
    from .metadata import DOC_TYPES
    if "doc_type" in changes:
        if changes["doc_type"] not in DOC_TYPES:
            raise ValueError("Tip de document invalid")
        changes["hierarchy_level"] = DOC_TYPES[changes["doc_type"]][1]
    before = db.q1("SELECT insurer, product, doc_type, version_label, effective_date FROM documents WHERE id=?", (doc_id,))
    sets = ", ".join(f"{k}=?" for k in changes) + ", metadata_overridden=1"
    with db.tx() as c:
        c.execute(f"UPDATE documents SET {sets} WHERE id=?", (*changes.values(), doc_id))
    security.audit(user, "document.metadata_override", doc_id, {"inainte": before, "dupa": changes})
    versions.rebuild_relations()
    from .retrieval import index
    index.invalidate()
    return db.q1("SELECT * FROM documents WHERE id=?", (doc_id,))
