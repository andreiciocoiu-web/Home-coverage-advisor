"""Teste API: roluri, flux complet, export, feedback cu principiul celor 4 ochi."""
import time

import pytest
from fastapi.testclient import TestClient

from advisor import config, security


@pytest.fixture(scope="module")
def client(env, tmp_path_factory):
    users = tmp_path_factory.mktemp("u") / "users.json"
    config.settings.users_file = str(users)
    config.settings.auth_disabled = False
    tokens = {r: security.create_user(f"u_{r}", r) for r in ("viewer", "analyst", "reviewer", "admin")}
    tokens["reviewer2"] = security.create_user("u_reviewer2", "reviewer")
    from advisor.main import app
    with TestClient(app) as c:
        yield c, tokens


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_auth_required(client):
    c, _ = client
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers=H("gresit")).status_code == 401


def test_role_enforcement(client):
    c, t = client
    assert c.post("/api/incident/extract", json={"text": "O teava de apa s-a spart in baie."}, headers=H(t["viewer"])).status_code == 403
    assert c.get("/api/audit", headers=H(t["reviewer"])).status_code == 403
    assert c.post("/api/sources", json={"kind": "local", "location": "/tmp"}, headers=H(t["analyst"])).status_code == 403


def test_full_flow_and_exports(client):
    c, t = client
    ex = c.post("/api/incident/extract", json={"text": "Un apartament din Bucuresti a fost avariat dupa ce o teava de apa s-a spart in baie. "
                                                        "Apa a deteriorat parchetul, mobila si tavanul apartamentului de dedesubt. "
                                                        "Locuinta a fost nelocuita timp de trei saptamani."}, headers=H(t["analyst"])).json()
    assert ex["questions"]
    r = c.post("/api/analyses", json={"extraction_id": ex["extraction_id"], "answers": {"sudden_or_gradual": "brusc"}},
               headers=H(t["analyst"])).json()
    assert r["products"] and r["disclaimer"]
    row = r["products"][0]["table"]
    for col in ("rank", "insurer", "product", "outcome", "score", "evidence_confidence", "main_clause", "main_exclusion",
                "deductible_or_sublimit", "missing_info", "recommendation"):
        assert col in row
    for fmt, ctype in (("html", "text/html"), ("pdf", "application/pdf"), ("json", "application/json")):
        resp = c.get(f"/api/analyses/{r['id']}/report.{fmt}", headers=H(t["analyst"]))
        assert resp.status_code == 200 and ctype in resp.headers["content-type"]
    # alt analist nu vede analiza; reviewer-ul o vede
    assert c.get(f"/api/analyses/{r['id']}", headers=H(t["viewer"])).status_code == 403
    assert c.get(f"/api/analyses/{r['id']}", headers=H(t["reviewer"])).status_code == 200
    # extractia nu poate fi folosita de alt utilizator
    assert c.post("/api/analyses", json={"extraction_id": ex["extraction_id"]}, headers=H(t["reviewer"])).status_code == 404


def test_feedback_four_eyes(client):
    c, t = client
    fb = c.post("/api/feedback", json={"kind": "clause_relevance", "clause_id": "x:1", "content": {"relevant": False, "text": "irelevanta"}},
                headers=H(t["reviewer"])).json()
    assert fb["status"] == "pending"
    assert c.post(f"/api/feedback/{fb['id']}/validate", headers=H(t["reviewer"])).status_code == 403
    assert c.post(f"/api/feedback/{fb['id']}/validate", headers=H(t["reviewer2"])).json()["status"] == "validated"


def test_methodology_requires_other_admin(client):
    c, t = client
    bad = c.post("/api/methodology", json={"weights": {"coverage_match": 50}, "note": "test suma"}, headers=H(t["reviewer"]))
    assert bad.status_code == 400  # ponderile nu mai insumeaza 100
    ok = c.post("/api/methodology", json={"weights": {"coverage_match": 35, "evidence": 0}, "note": "test propunere"}, headers=H(t["reviewer"])).json()
    assert ok["status"] == "proposed"
    assert c.post(f"/api/methodology/{ok['version']}/approve", headers=H(t["reviewer"])).status_code == 403
    assert c.post(f"/api/methodology/{ok['version']}/approve", headers=H(t["admin"])).json()["status"] == "approved"


def test_audit_log_records_actions(client):
    c, t = client
    actions = {a["action"] for a in c.get("/api/audit", headers=H(t["admin"])).json()}
    assert {"analysis.run", "report.export", "feedback.submit", "methodology.approve"} <= actions


def test_metadata_override_audited(client):
    c, t = client
    docs = c.get("/api/documents", headers=H(t["viewer"])).json()["documents"]
    d = next(x for x in docs if x["filename"] == "Alfa_Locuinta_Plus_IPID.pdf")
    assert c.patch(f"/api/documents/{d['id']}", json={"version_label": "IPID-2025"}, headers=H(t["analyst"])).status_code == 403
    r = c.patch(f"/api/documents/{d['id']}", json={"version_label": "IPID-2025"}, headers=H(t["reviewer"])).json()
    assert r["version_label"] == "IPID-2025" and r["metadata_overridden"] == 1


def test_security_headers(client):
    c, _ = client
    r = c.get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and "Content-Security-Policy" in r.headers
