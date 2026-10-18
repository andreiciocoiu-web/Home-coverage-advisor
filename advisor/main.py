"""API FastAPI + servire UI. Pornire: uvicorn advisor.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, feedback, ingest, security, versions
from .analysis import analyze
from .config import load_json_config, settings
from .facts import extract, follow_up_questions
from .metadata import DOC_TYPES
from .clauses import KIND_LABELS
from .report import render_html, render_pdf
from .retrieval import index
from .scoring import current_methodology

@asynccontextmanager
async def lifespan(_app):
    db.get_conn()
    current_methodology()
    security.apply_retention()
    yield


app = FastAPI(title="Home Insurance Coverage Advisor", version="0.1.0", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_extractions: dict[str, dict] = {}  # extractii de fapte in asteptarea raspunsurilor (in memorie, TTL)
_EXTRACTION_TTL = 3600
_sync_jobs: dict[str, dict] = {}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
                                               "style-src 'self' 'unsafe-inline'; img-src 'self' data:")
    return resp


# --------------------------------------------------------------------------- auth
def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
    u = security.authenticate(token)
    if not u:
        raise HTTPException(401, "Autentificare necesara")
    return u


def require(role: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if not security.has_role(user, role):
            raise HTTPException(403, f"Rol insuficient (necesar: {role})")
        return user
    return dep


# --------------------------------------------------------------------------- UI
@app.get("/", response_class=HTMLResponse)
def index_page():
    return FileResponse(STATIC / "index.html")


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/status")
def status(user=Depends(current_user)):
    v, cfg = current_methodology()
    return {
        "llm_enabled": settings.llm_enabled, "llm_model": settings.llm_model if settings.llm_enabled else None,
        "retrieval_backend": index.backend if index.backend != "neconstruit" else "index construit la prima analiza", "methodology_version": v, "weights": cfg["weights"],
        "documents": db.q1("SELECT COUNT(*) n FROM documents WHERE status='ok' AND deleted_at IS NULL")["n"],
        "products": len(versions.products()), "retention_days": settings.retention_days,
        "doc_types": {k: v[0] for k, v in DOC_TYPES.items()}, "clause_kinds": KIND_LABELS,
        "feedback_kinds": feedback.KINDS,
    }


# --------------------------------------------------------------------------- surse & sincronizare
class SourceIn(BaseModel):
    kind: str = Field(pattern="^(drive|local)$")
    location: str = Field(min_length=1, max_length=500)
    label: str = ""


@app.get("/api/sources")
def list_sources(user=Depends(current_user)):
    return db.q("SELECT * FROM sources ORDER BY created_at DESC")


@app.post("/api/sources")
def add_source(body: SourceIn, user=Depends(require("analyst"))):
    if body.kind == "local" and not security.has_role(user, "admin"):
        raise HTTPException(403, "Sursele locale pot fi adaugate doar de administrator")
    s = ingest.register_source(body.kind, body.location.strip(), body.label)
    security.audit(user, "source.add", s["id"], {"kind": body.kind})
    return s


@app.post("/api/sources/{sid}/sync")
def start_sync(sid: str, user=Depends(require("analyst"))):
    if not db.q1("SELECT id FROM sources WHERE id=?", (sid,)):
        raise HTTPException(404, "Sursa inexistenta")
    if _sync_jobs.get(sid, {}).get("state") == "running":
        return _sync_jobs[sid]
    job = {"state": "running", "started": db.now(), "result": None, "error": None}
    _sync_jobs[sid] = job

    def run():
        try:
            job["result"] = ingest.sync_source(sid, user)
            job["state"] = "done"
        except Exception as e:
            job["state"], job["error"] = "error", str(e)
    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/api/sources/{sid}/sync")
def sync_status(sid: str, user=Depends(current_user)):
    runs = db.q("SELECT * FROM sync_runs WHERE source_id=? ORDER BY id DESC LIMIT 10", (sid,))
    for r in runs:
        r["errors"] = json.loads(r.pop("errors_json") or "[]")
    return {"job": _sync_jobs.get(sid), "runs": runs}


# --------------------------------------------------------------------------- documente
_DOC_COLS = ("id, source_id, filename, mime, size, modified_time, status, error, insurer, product, doc_type, version_label, "
             "effective_date, effective_date_evidence, hierarchy_level, metadata_overridden, page_count, ocr_pages, "
             "injection_flags, warnings_json, ingested_at, deleted_at, superseded_by")


@app.get("/api/documents")
def list_documents(include_inactive: bool = False, user=Depends(current_user)):
    where = "" if include_inactive else "WHERE deleted_at IS NULL AND superseded_by IS NULL"
    docs = db.q(f"SELECT {_DOC_COLS} FROM documents {where} ORDER BY insurer, product, hierarchy_level")
    for d in docs:
        d["injection_flags"] = json.loads(d["injection_flags"] or "[]")
        d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
        d["doc_type_label"] = DOC_TYPES.get(d["doc_type"] or "unknown", ("?", 5))[0]
        d["clause_count"] = db.q1("SELECT COUNT(*) n FROM clauses WHERE document_id=?", (d["id"],))["n"]
    rels = db.q("SELECT * FROM doc_relations")
    for r in rels:
        r["detail"] = json.loads(r.pop("detail_json") or "{}")
    return {"documents": docs, "relations": rels}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str, user=Depends(current_user)):
    d = db.q1(f"SELECT {_DOC_COLS}, metadata_json FROM documents WHERE id=?", (doc_id,))
    if not d:
        raise HTTPException(404)
    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    d["injection_flags"] = json.loads(d["injection_flags"] or "[]")
    d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
    d["clauses"] = db.q("SELECT id, ordinal, number, title, section_path, kind, text, page_start, page_end, location_label "
                        "FROM clauses WHERE document_id=? ORDER BY ordinal", (doc_id,))
    return d


class MetaIn(BaseModel):
    insurer: str | None = None
    product: str | None = None
    doc_type: str | None = None
    version_label: str | None = None
    effective_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


@app.patch("/api/documents/{doc_id}")
def patch_document(doc_id: str, body: MetaIn, user=Depends(require("reviewer"))):
    try:
        return ingest.override_metadata(doc_id, body.model_dump(exclude_unset=True), user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/documents/{doc_id}/original")
def download_original(doc_id: str, user=Depends(require("analyst"))):
    d = db.q1("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not d or not d["stored_path"]:
        raise HTTPException(404)
    security.audit(user, "document.download", doc_id)
    media = "application/pdf" if d["mime"] == "application/pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = Path(d["filename"]).name
    if not name.lower().endswith((".pdf", ".docx")):
        name += ".docx"
    return Response(ingest.read_original(d), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --------------------------------------------------------------------------- incident & analiza
class IncidentIn(BaseModel):
    text: str = Field(min_length=15, max_length=6000)


def _gc_extractions():
    now = time.time()
    for k in [k for k, v in _extractions.items() if now - v["ts"] > _EXTRACTION_TTL]:
        _extractions.pop(k, None)


@app.post("/api/incident/extract")
def extract_facts(body: IncidentIn, user=Depends(require("analyst"))):
    _gc_extractions()
    ex = extract(body.text, use_llm=settings.llm_enabled)
    eid = uuid.uuid4().hex
    _extractions[eid] = {"ts": time.time(), "text": body.text, "extraction": ex, "user": user["username"]}
    labels = load_json_config("perils.json")
    return {"extraction_id": eid, "mode": ex["mode"], "facts": ex["facts"], "loss_components": ex["loss_components"],
            "questions": follow_up_questions(ex["facts"]),
            "fact_labels": {k: v["label"] for k, v in labels["fact_fields"].items()},
            "peril_labels": {k: v["label"] for k, v in labels["perils"].items()}}


class AnalyzeIn(BaseModel):
    extraction_id: str
    answers: dict[str, str] = {}


@app.post("/api/analyses")
def run_analysis(body: AnalyzeIn, user=Depends(require("analyst"))):
    ex = _extractions.get(body.extraction_id)
    if not ex or ex["user"] != user["username"]:
        raise HTTPException(404, "Extractia a expirat sau nu exista; descrieti din nou incidentul.")
    answers = {k: v[:1000] for k, v in body.answers.items() if v and v.strip()}
    return analyze(ex["text"], ex["extraction"], answers, user)


@app.get("/api/analyses")
def list_analyses(user=Depends(current_user)):
    rows = db.q("SELECT id, created_at, username, methodology_version, mode FROM analyses ORDER BY created_at DESC LIMIT 100")
    if not security.has_role(user, "reviewer"):
        rows = [r for r in rows if r["username"] == user["username"]]
    return rows


def _load_analysis(aid: str, user: dict) -> tuple[dict, str]:
    row = db.q1("SELECT * FROM analyses WHERE id=?", (aid,))
    if not row:
        raise HTTPException(404, "Analiza inexistenta (posibil stearsa conform politicii de retentie)")
    if row["username"] != user["username"] and not security.has_role(user, "reviewer"):
        raise HTTPException(403)
    return json.loads(security.decrypt_text(row["result_json"])), security.decrypt_text(row["incident_enc"])


@app.get("/api/analyses/{aid}")
def get_analysis(aid: str, user=Depends(current_user)):
    r, text = _load_analysis(aid, user)
    return r | {"incident_text": text}


@app.get("/api/analyses/{aid}/report.{fmt}")
def export_report(aid: str, fmt: str, user=Depends(current_user)):
    r, text = _load_analysis(aid, user)
    security.audit(user, "report.export", aid, {"format": fmt})
    if fmt == "html":
        return HTMLResponse(render_html(r, text), headers={"Content-Disposition": f'attachment; filename="raport_{aid}.html"'})
    if fmt == "pdf":
        return Response(render_pdf(r, text), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="raport_{aid}.pdf"'})
    if fmt == "json":
        return JSONResponse(r | {"incident_text": text}, headers={"Content-Disposition": f'attachment; filename="raport_{aid}.json"'})
    raise HTTPException(400, "Format nesuportat (html, pdf, json)")


# --------------------------------------------------------------------------- feedback & metodologie
class FeedbackIn(BaseModel):
    kind: str
    analysis_id: str | None = None
    product_key: str | None = None
    clause_id: str | None = None
    content: dict = {}


@app.post("/api/feedback")
def post_feedback(body: FeedbackIn, user=Depends(require("reviewer"))):
    try:
        return feedback.submit(user, body.kind, body.analysis_id, body.product_key, body.clause_id, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/feedback")
def get_feedback(user=Depends(require("reviewer"))):
    rows = db.q("SELECT * FROM feedback ORDER BY id DESC LIMIT 300")
    for r in rows:
        r["content"] = json.loads(r.pop("content_json") or "{}")
    return rows


@app.post("/api/feedback/{fid}/{decision}")
def decide_feedback(fid: int, decision: str, user=Depends(require("reviewer"))):
    if decision not in ("validate", "reject"):
        raise HTTPException(400)
    try:
        return feedback.validate(user, fid, decision == "validate")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/methodology")
def get_methodology(user=Depends(current_user)):
    rows = db.q("SELECT * FROM methodology ORDER BY version DESC")
    for r in rows:
        r["config"] = json.loads(r.pop("config_json"))
    return rows


class MethodologyIn(BaseModel):
    weights: dict[str, float] = {}
    gates: dict[str, float] = {}
    note: str = Field(min_length=5, max_length=1000)


@app.post("/api/methodology")
def propose(body: MethodologyIn, user=Depends(require("reviewer"))):
    try:
        cfg = {k: v for k, v in (("weights", body.weights), ("gates", body.gates)) if v}
        return feedback.propose_methodology(user, cfg, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/methodology/{version}/{decision}")
def decide_methodology(version: int, decision: str, user=Depends(require("admin"))):
    if decision not in ("approve", "reject"):
        raise HTTPException(400)
    try:
        return feedback.decide_methodology(user, version, decision == "approve")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- audit & retentie
@app.get("/api/audit")
def get_audit(limit: int = 200, user=Depends(require("admin"))):
    rows = db.q("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
    for r in rows:
        r["detail"] = json.loads(r.pop("detail_json") or "{}")
    return rows


@app.post("/api/retention/run")
def run_retention(user=Depends(require("admin"))):
    return security.apply_retention()
