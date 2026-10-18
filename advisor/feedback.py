"""Revizuire umana: feedback auditat, separat de faptele contractuale.

Regula: feedback-ul nu modifica niciodata textul documentelor sau rezultatele
analizelor deja efectuate. Doar feedback-ul VALIDAT (de un alt reviewer decat
autorul, sau de un administrator) influenteaza ordonarea la regasire. Un
rezultat real de dauna este inregistrat ca observatie individuala, nu ca regula.
"""
from __future__ import annotations

import json
import os

from . import db, security

KINDS = {
    "interpretation_correction": "Confirmare / corectare interpretare",
    "clause_relevance": "Clauza relevanta / irelevanta",
    "insurer_clarification": "Clarificare primita de la asigurator",
    "claim_outcome": "Rezultat real al daunei (cand este disponibil legal)",
    "recommendation_error": "Recomandare anterioara incorecta - explicatie",
}


def submit(user: dict, kind: str, analysis_id: str | None, product_key: str | None, clause_id: str | None, content: dict) -> dict:
    if kind not in KINDS:
        raise ValueError("Tip de feedback necunoscut")
    if kind == "claim_outcome":
        content = {**content, "nota": "Observatie individuala; nu este generalizata automat la alte cazuri."}
    with db.tx() as c:
        cur = c.execute("INSERT INTO feedback(created_at, reviewer, analysis_id, product_key, clause_id, kind, content_json, status) "
                        "VALUES (?,?,?,?,?,?,?,?)", (db.now(), user["username"], analysis_id, product_key, clause_id, kind,
                                                    json.dumps(content, ensure_ascii=False), "pending"))
        fid = cur.lastrowid
    security.audit(user, "feedback.submit", str(fid), {"kind": kind, "analysis": analysis_id, "clause": clause_id})
    return db.q1("SELECT * FROM feedback WHERE id=?", (fid,))


def validate(user: dict, fid: int, approve: bool) -> dict:
    fb = db.q1("SELECT * FROM feedback WHERE id=?", (fid,))
    if not fb:
        raise ValueError("Feedback inexistent")
    if fb["reviewer"] == user["username"] and user["role"] != "admin":
        raise PermissionError("Feedback-ul trebuie validat de alt reviewer decat autorul (principiul celor 4 ochi)")
    with db.tx() as c:
        c.execute("UPDATE feedback SET status=?, validated_by=?, validated_at=? WHERE id=?",
                  ("validated" if approve else "rejected", user["username"], db.now(), fid))
    security.audit(user, "feedback.validate" if approve else "feedback.reject", str(fid))
    from .retrieval import index
    index.invalidate()
    return db.q1("SELECT * FROM feedback WHERE id=?", (fid,))


def propose_methodology(user: dict, config: dict, note: str) -> dict:
    from .scoring import current_methodology
    _, cur = current_methodology()
    new = json.loads(json.dumps(cur))
    for k in ("weights", "gates"):
        if k in config:
            for kk, vv in config[k].items():
                if kk not in new[k]:
                    raise ValueError(f"Parametru necunoscut: {k}.{kk}")
                new[k][kk] = float(vv)
    if abs(sum(new["weights"].values()) - 100) > 0.01:
        raise ValueError("Ponderile trebuie sa insumeze 100")
    with db.tx() as c:
        cur_ = c.execute("INSERT INTO methodology(created_at, created_by, config_json, status, note) VALUES (?,?,?,?,?)",
                         (db.now(), user["username"], json.dumps(new), "proposed", note))
        v = cur_.lastrowid
    security.audit(user, "methodology.propose", str(v), {"note": note, "weights": new["weights"]})
    return db.q1("SELECT * FROM methodology WHERE version=?", (v,))


def decide_methodology(user: dict, version: int, approve: bool) -> dict:
    m = db.q1("SELECT * FROM methodology WHERE version=?", (version,))
    if not m or m["status"] != "proposed":
        raise ValueError("Nu exista o propunere in asteptare cu aceasta versiune")
    if m["created_by"] == user["username"] and os.environ.get("HCA_ALLOW_SELF_APPROVAL") != "1":
        raise PermissionError("Propunerea trebuie aprobata de alt administrator decat autorul")
    with db.tx() as c:
        c.execute("UPDATE methodology SET status=?, approved_by=?, approved_at=? WHERE version=?",
                  ("approved" if approve else "rejected", user["username"], db.now(), version))
    security.audit(user, "methodology.approve" if approve else "methodology.reject", str(version))
    return db.q1("SELECT * FROM methodology WHERE version=?", (version,))
