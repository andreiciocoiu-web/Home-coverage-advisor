"""Teste de analiza, scoring si scenarii (inclusiv metrici agregate)."""
import json

from advisor import analysis, facts
from advisor.config import load_json_config
from advisor.scoring import score_product


def _run(text, answers=None):
    ex = facts.extract(text, use_llm=False)
    return analysis.analyze(text, ex, answers or {}, None)


def _p(res, key):
    return next(p for p in res["products"] if p["product_key"] == key)


ALFA, BETA, GAMA, DELTA = ("Asigurator Alfa (fictiv) :: Locuinta Plus", "Asigurator Beta (fictiv) :: Casa Sigura",
                           "Asigurator Gama (fictiv) :: Basic Home", "Asigurator Delta (fictiv) :: Total Protect")


def test_every_cited_finding_is_verified(env):
    res = _run("O teava de apa s-a spart si a inundat parchetul si tavanul vecinului de dedesubt.")
    for p in res["products"]:
        for c in p["assessment"]["components"]:
            for e in c["coverage_evidence"]:
                assert e["verified"] and e["quote"]
        assert p["assessment"]["n_unverified"] == 0


def test_similar_events_different_outcomes(env):
    sudden = _run("O teava de apa s-a spart brusc in baie si a udat parchetul. Locuim in casa.")
    gradual = _run("De cateva luni apar pete de umezeala din cauza unei scurgeri lente la teava de apa. Locuim in casa.")
    assert _p(sudden, ALFA)["score"]["outcome"] != "LIKELY_EXCLUDED"
    assert _p(gradual, ALFA)["score"]["outcome"] == "LIKELY_EXCLUDED"
    assert _p(sudden, DELTA)["score"]["outcome"] == "LIKELY_COVERED"
    assert _p(gradual, DELTA)["score"]["outcome"] == "LIKELY_EXCLUDED"


def test_optional_coverage_not_selected(env):
    res = _run("O supratensiune in reteaua electrica a ars televizorul.")
    g = _p(res, GAMA)
    assert g["assessment"]["components"][0]["coverage_level"] == "optional_not_selected"
    assert g["score"]["outcome"] == "LIKELY_EXCLUDED"


def test_endorsement_overrides_base_clause(env):
    res = _run("O teava de apa s-a spart brusc. Locuim in casa.")
    d = _p(res, DELTA)
    amounts = {l["amount_text"] for c in d["assessment"]["components"] for l in c["limits"]}
    assert "400 lei" in amounts and "200 lei" not in amounts  # Art. 4 modificat prin actul aditional


def test_no_forced_winner(env):
    res = _run("S-a stricat ceva in casa. Ce asigurare ma ajuta?")
    assert res["verdict"]["type"] == "none"


def test_injection_does_not_change_scoring(env):
    res = _run("O supratensiune in reteaua electrica a ars televizorul. Locuim in casa.")
    d = _p(res, DELTA)
    assert d["score"]["overall"] < 100
    assert any("text suspect" in l for l in d["version_limitations"])


def test_clear_exclusion_caps_score():
    cfg = load_json_config("scoring.json")
    ev = {"clause_number": "7", "hierarchy_level": 1}
    a = {"components": [{"component": "x", "category": "building", "coverage_level": "express",
                         "coverage_evidence": [ev], "exclusions": [{"applies": "clearly", "verified": True, "evidence": ev}],
                         "limits": [], "conditions": []}],
         "conflicts": [], "clarity_level": "clear_direct", "n_verified": 2, "n_unverified": 0, "unknown_material_facts": []}
    s = score_product(a, {}, cfg)
    assert s["overall"] <= cfg["gates"]["clear_exclusion_cap"] and s["outcome"] == "LIKELY_EXCLUDED"


def test_missing_information_is_not_favorable():
    cfg = load_json_config("scoring.json")
    a = {"components": [{"component": "x", "category": "building", "coverage_level": "none_found", "coverage_evidence": [],
                         "exclusions": [], "limits": [], "conditions": []}],
         "conflicts": [], "clarity_level": None, "n_verified": 0, "n_unverified": 0, "unknown_material_facts": []}
    s = score_product(a, {}, cfg)
    assert s["outcome"] == "INSUFFICIENT_INFORMATION" and s["overall"] <= cfg["gates"]["no_verified_coverage_cap"]


def test_llm_path_fabricated_quotes_are_rejected(env, monkeypatch):
    """Simuleaza raspunsul modelului: un citat real si unul inventat. Cel inventat nu sustine scorul."""
    from advisor import config, llm
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test-key")

    def fake_call_tool(system, user, tool_name, schema, description, max_tokens=None):
        assert "<document_data>" in user  # documentele sunt trimise ca date incapsulate
        import re
        ids = re.findall(r'<clause id="([^"]+)"', user)
        real = next(i for i in ids if "5.2" in user.split(f'<clause id="{i}"')[1].split("</clause>")[0][:400])
        return {"components": [{"component": "Parchet", "category": "building", "coverage_level": "express",
                                "coverage_findings": [{"clause_id": real, "quote": "apa provenita din conducte", "explanation": "ok"},
                                                      {"clause_id": real, "quote": "acopera orice dauna fara exceptie", "explanation": "inventat"}],
                                "exclusions": [], "limits": [], "conditions": []}],
                "conflicts": [], "clarity_level": "clear_direct", "summary": "test"}

    monkeypatch.setattr(llm, "call_tool", fake_call_tool)
    text = "O teava de apa s-a spart brusc si a udat parchetul. Locuim in casa."
    ex = facts.extract(text, use_llm=False)
    res = analysis.analyze(text, ex, {}, None)
    a = _p(res, ALFA)
    assert a["mode"] == "llm"
    assert a["assessment"]["n_unverified"] >= 1 and a["assessment"]["n_verified"] >= 1
    assert all(e["verified"] for e in a["assessment"]["components"][0]["coverage_evidence"])
