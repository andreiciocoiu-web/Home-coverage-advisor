"""Scoring determinist si ranking.

Modelul de limbaj (sau analizorul euristic) produce DOAR judecati categoriale
sustinute de citate verificate. Transformarea in puncte se face aici, dupa
reguli publice si configurabile (config/scoring.json, versionate in DB).

Trei masuri separate:
1. Scor de sustinere contractuala (0-100) - cat de puternic sustin documentele acoperirea;
2. Increderea in dovezi (Ridicata/Medie/Scazuta) - cat de fiabile si complete sunt dovezile extrase;
3. Incertitudinea rezultatului daunei (Ridicata/Medie/Scazuta) - cat depinde rezultatul de fapte
   nerezolvate, interpretare sau instrumentarea daunei.
Niciuna nu este o probabilitate calibrata de plata.
"""
from __future__ import annotations

import json

from . import db
from .config import load_json_config

OUTCOMES = {
    "LIKELY_COVERED": "PROBABIL ACOPERIT",
    "POTENTIALLY_COVERED": "POTENTIAL ACOPERIT - NECESITA CONFIRMARE",
    "PARTIALLY_COVERED": "PARTIAL ACOPERIT",
    "LIKELY_EXCLUDED": "PROBABIL EXCLUS",
    "INSUFFICIENT_INFORMATION": "INFORMATII INSUFICIENTE",
}

DIM_LABELS = {
    "coverage_match": "Potrivirea acoperirii",
    "exclusion_risk": "Gestionarea riscului de excludere",
    "clarity": "Claritate contractuala",
    "completeness": "Completitudinea acoperirii",
    "limits": "Limite si fransize",
    "conditions": "Conditii si eligibilitate",
    "evidence": "Calitatea dovezilor",
}

_EXCL_ORDER = ["none_identified", "exception_restores", "possible", "possible_material", "likely", "clearly"]
_LIMIT_ORDER = ["no_material_limit", "deductible_only", "unknown", "may_reduce", "severe"]
_COND_ORDER = ["none", "manageable", "unknown", "unmet_risk", "unmet_likely"]
_COV_SCORE = {"express": 1.0, "general": 1.0, "optional_unconfirmed": 0.5, "ambiguous": 0.5,
              "optional_not_selected": 0.0, "none_found": 0.0}


def current_methodology() -> tuple[int, dict]:
    row = db.q1("SELECT * FROM methodology WHERE status='approved' ORDER BY version DESC LIMIT 1")
    if not row:
        cfg = load_json_config("scoring.json")
        with db.tx() as c:
            c.execute("INSERT INTO methodology(created_at, created_by, config_json, status, approved_by, approved_at, note) "
                      "VALUES (?,?,?,?,?,?,?)", (db.now(), "system", json.dumps(cfg), "approved", "system", db.now(),
                                                 "Metodologia initiala (config/scoring.json)"))
        row = db.q1("SELECT * FROM methodology WHERE status='approved' ORDER BY version DESC LIMIT 1")
    return row["version"], json.loads(row["config_json"])


def _worst(values: list[str], order: list[str], default: str) -> str:
    vals = [v for v in values if v in order]
    return max(vals, key=order.index) if vals else default


def _primary(components: list[dict]) -> dict | None:
    own = [c for c in components if c["category"] != "third_party"]
    return (own or components or [None])[0]


def score_product(a: dict, facts: dict, cfg: dict) -> dict:
    R, W, G = cfg["rubrics"], cfg["weights"], cfg["gates"]
    comps = a["components"]
    prim = _primary(comps)
    dims: dict[str, dict] = {}

    # A. potrivire acoperire (componenta principala)
    cov_level = prim["coverage_level"] if prim else "none_found"
    dims["coverage_match"] = {"level": cov_level, "score": R["coverage_match"][cov_level],
                              "evidence": [e for e in (prim or {}).get("coverage_evidence", [])]}
    # B. risc de excludere (componenta principala; excluderile pe alte componente afecteaza completitudinea)
    excl_levels = [x["applies"] for x in (prim or {}).get("exclusions", []) if x.get("verified")]
    ex_level = _worst(excl_levels, _EXCL_ORDER, "none_identified")
    dims["exclusion_risk"] = {"level": ex_level, "score": R["exclusion_risk"][ex_level],
                              "evidence": [x["evidence"] for x in (prim or {}).get("exclusions", []) if x.get("verified")]}
    # C. claritate
    cl = a.get("clarity_level", "ambiguous")
    if a.get("conflicts"):
        cl = "conflicting_documents"
    if cov_level == "none_found":
        cl = "not_found"
    dims["clarity"] = {"level": cl, "score": R["clarity"][cl],
                       "evidence": [c.get("evidence") for c in a.get("conflicts", []) if c.get("evidence")]}
    # D. completitudine
    comp_scores = []
    for c in comps:
        s = _COV_SCORE.get(c["coverage_level"], 0)
        worst = _worst([x["applies"] for x in c.get("exclusions", []) if x.get("verified")], _EXCL_ORDER, "none_identified")
        if worst in ("clearly", "likely"):
            s = 0
        elif worst in ("possible", "possible_material"):
            s = min(s, 0.5)
        comp_scores.append(s)
        c["component_score"] = round(s * 100)
    completeness = round(100 * sum(comp_scores) / len(comp_scores)) if comp_scores else 0
    dims["completeness"] = {"level": f"{sum(1 for s in comp_scores if s >= 1)}/{len(comp_scores)} componente acoperite integral",
                            "score": completeness, "evidence": []}
    dims["completeness"]["level"] = (f"{sum(1 for s in comp_scores if s >= 1)} din {len(comp_scores)} componente cu acoperire "
                                     "neconditionata de fapte nestabilite")
    # E. limite si fransize (doar pentru componentele cu acoperire)
    lim_levels = [l["impact"] for c in comps if _COV_SCORE.get(c["coverage_level"], 0) > 0
                  for l in c.get("limits", []) if l.get("verified")]
    li = _worst(lim_levels, _LIMIT_ORDER, "unknown")
    dims["limits"] = {"level": li, "score": R["limits"][li],
                      "evidence": [l["evidence"] for c in comps for l in c.get("limits", []) if l.get("verified")]}
    # F. conditii
    cond_levels = [x["status"] for c in comps for x in c.get("conditions", []) if x.get("verified")]
    co = _worst(cond_levels, _COND_ORDER, "unknown" if not cond_levels else "none")
    if cond_levels and all(s == "met" for s in cond_levels):
        co = "none"
    dims["conditions"] = {"level": co, "score": R["conditions"][co],
                          "evidence": [x["evidence"] for c in comps for x in c.get("conditions", []) if x.get("verified")]}
    # G. calitatea dovezilor
    n_ver, n_unv = a.get("n_verified", 0), a.get("n_unverified", 0)
    if n_ver == 0:
        ev_score = 0
    else:
        ev_score = 100 * n_ver / (n_ver + n_unv)
        cov_levels_docs = [e.get("hierarchy_level") or 5 for e in dims["coverage_match"]["evidence"]]
        best = min(cov_levels_docs) if cov_levels_docs else 5
        ev_score *= {1: 1.0, 2: 0.9, 3: 0.7, 4: 0.4}.get(best, 0.3)
        if a.get("version_undetermined"):
            ev_score -= 15
        if a.get("ocr_in_evidence"):
            ev_score -= 10
        if a.get("injection_in_evidence"):
            ev_score -= 10
    ev_score = max(0, round(ev_score))
    dims["evidence"] = {"level": f"{n_ver} citate verificate, {n_unv} neverificate", "score": ev_score, "evidence": []}

    total_w = sum(W.values())
    overall = sum(dims[k]["score"] * W[k] for k in W) / total_w
    caps = []
    cov_ev_levels = [e.get("hierarchy_level") or 5 for e in dims["coverage_match"]["evidence"]]
    if ex_level == "clearly":
        overall = min(overall, G["clear_exclusion_cap"])
        caps.append(f"Plafonat la {G['clear_exclusion_cap']}: o excludere verificata pare sa se aplice clar.")
    if not dims["coverage_match"]["evidence"]:
        overall = min(overall, G["no_verified_coverage_cap"])
        caps.append(f"Plafonat la {G['no_verified_coverage_cap']}: nu exista o clauza de acoperire verificata.")
    elif min(cov_ev_levels) >= 3:
        overall = min(overall, G["only_secondary_documents_cap"])
        caps.append(f"Plafonat la {G['only_secondary_documents_cap']}: acoperirea rezulta doar din IPID/brosura, nu din conditiile contractuale.")

    # ------------------------------------------------ clasificare rezultat
    reasons = []
    if ex_level in ("clearly", "likely"):
        outcome = "LIKELY_EXCLUDED"
        reasons.append("O excludere identificata si verificata pare sa impiedice acoperirea componentei principale.")
    elif cov_level == "optional_not_selected":
        outcome = "LIKELY_EXCLUDED"
        reasons.append("Acoperirea relevanta este optionala si nu apare ca selectata in oferta/polita analizata.")
    elif cov_level == "none_found" or n_ver == 0:
        outcome = "INSUFFICIENT_INFORMATION"
        reasons.append("Nu a fost identificata o clauza de acoperire verificata pentru eveniment.")
    elif ex_level in ("possible", "possible_material") or cov_level in ("optional_unconfirmed", "ambiguous") or co in ("unmet_risk", "unmet_likely") or (co == "unknown" and cond_levels) \
            or a.get("version_undetermined") or a.get("conflicts"):
        outcome = "POTENTIALLY_COVERED"
        if ex_level == "possible":
            reasons.append("Exista excluderi a caror aplicare depinde de fapte inca nestabilite.")
        if ex_level == "possible_material":
            reasons.append("Faptele stabilite declanseaza o excludere; acoperirea depinde de o exceptie inca neconfirmata.")
        if cov_level == "optional_unconfirmed":
            reasons.append("Acoperirea este optionala; cumpararea ei nu este confirmata de oferta/polita.")
        if cov_level == "ambiguous":
            reasons.append("Formularea acoperirii este ambigua fata de faptele descrise.")
        if co == "unmet_likely":
            reasons.append("O obligatie contractuala pare nerespectata; efectul asupra despagubirii depinde de formularea "
                           "contractului, de legatura cauzala cu dauna si de lege - necesita evaluare umana.")
        if co == "unmet_risk" or (co == "unknown" and cond_levels):
            reasons.append("Conditii contractuale relevante nu pot fi verificate cu informatiile disponibile.")
        if a.get("version_undetermined"):
            reasons.append("Versiunea aplicabila a documentelor nu a putut fi stabilita.")
        if a.get("conflicts"):
            reasons.append("Documentele produsului contin prevederi aparent contradictorii.")
    elif any(_COV_SCORE.get(c["coverage_level"], 0) == 0 for c in comps) or li in ("severe", "may_reduce"):
        outcome = "PARTIALLY_COVERED"
        if any(_COV_SCORE.get(c["coverage_level"], 0) == 0 for c in comps):
            reasons.append("Nu toate componentele pierderii au acoperire identificata (ex. daunele la terti).")
        if li in ("severe", "may_reduce"):
            reasons.append("O limita/sublimita poate reduce semnificativ despagubirea.")
    else:
        outcome = "LIKELY_COVERED"
        reasons.append("Faptele sunt compatibile cu o acoperire expresa si nu a fost identificata o excludere materiala in documentele analizate.")
    uncovered = [c["component"] for c in comps if _COV_SCORE.get(c["coverage_level"], 0) == 0]
    if outcome in ("POTENTIALLY_COVERED", "LIKELY_COVERED") and uncovered:
        reasons.append("Atentie: fara acoperire identificata pentru: " + "; ".join(uncovered) + ".")

    # ------------------------------------------------ incredere si incertitudine
    ev_conf = "Ridicata" if ev_score >= 75 else "Medie" if ev_score >= 50 else "Scazuta"
    unc_reasons = []
    for fk in a.get("unknown_material_facts", []):
        unc_reasons.append(f"Fapt material nestabilit: {fk}")
    for c in comps:
        for x in c.get("exclusions", []):
            if x.get("verified") and x["applies"] in ("possible", "possible_material"):
                unc_reasons.append(f"Excludere posibila ({x['evidence'].get('clause_number')}): depinde de fapte")
        for x in c.get("conditions", []):
            if x.get("verified") and x["status"] in ("unknown", "unmet_risk"):
                unc_reasons.append(f"Conditie neverificata ({x['evidence'].get('clause_number')})")
    if cov_level == "optional_unconfirmed":
        unc_reasons.append("Acoperire optionala neconfirmata")
    if a.get("version_undetermined"):
        unc_reasons.append("Versiune aplicabila nedeterminata")
    unc = "Ridicata" if len(unc_reasons) >= 3 else "Medie" if unc_reasons else "Scazuta"
    if outcome == "INSUFFICIENT_INFORMATION":
        unc = "Ridicata"

    return {
        "dimensions": dims, "overall": int(overall + 0.5), "caps": caps,
        "outcome": outcome, "outcome_label": OUTCOMES[outcome], "outcome_reasons": reasons,
        "evidence_confidence": ev_conf, "uncertainty": unc, "uncertainty_reasons": unc_reasons[:8],
    }


def rank(products: list[dict], cfg: dict) -> dict:
    G = cfg["gates"]
    order = {"LIKELY_COVERED": 0, "PARTIALLY_COVERED": 1, "POTENTIALLY_COVERED": 1,
             "INSUFFICIENT_INFORMATION": 3, "LIKELY_EXCLUDED": 4}
    products.sort(key=lambda p: (order[p["score"]["outcome"]], -p["score"]["overall"]))
    for i, p in enumerate(products, start=1):
        p["rank"] = i
    verdict = {"type": None, "text": ""}
    eligible = [p for p in products if p["score"]["outcome"] in ("LIKELY_COVERED", "POTENTIALLY_COVERED", "PARTIALLY_COVERED")
                and p["score"]["overall"] >= G["min_score_for_recommendation"]]
    if not eligible:
        verdict = {"type": "none", "text": "Nu se poate face o recomandare fiabila: niciun produs nu are dovezi contractuale "
                                            "suficient de puternice pentru acest incident, pe baza documentelor analizate."}
    else:
        top = eligible[0]
        ties = [p for p in eligible[1:] if abs(p["score"]["overall"] - top["score"]["overall"]) <= G["equivalence_threshold"]
                and p["score"]["outcome"] == top["score"]["outcome"]]
        if ties:
            names = ", ".join(f"{p['product_label']}" for p in [top] + ties)
            verdict = {"type": "equivalent", "text": f"Produsele {names} sunt practic echivalente pentru acest incident "
                                                       f"(diferenta de scor <= {G['equivalence_threshold']} puncte, acelasi rezultat). "
                                                       "Diferentiati-le dupa limitele, fransizele si conditiile din detalii."}
        else:
            verdict = {"type": "top", "text": f"{top['product_label']} are cea mai puternica baza contractuala pentru incidentul descris "
                                                f"(scor {top['score']['overall']}/100, {top['score']['outcome_label']})."}
    for p in products:
        p["recommendation"] = _recommendation(p, eligible, verdict)
    return verdict


def _recommendation(p: dict, eligible: list[dict], verdict: dict) -> str:
    o = p["score"]["outcome"]
    if verdict["type"] == "top" and eligible and p is eligible[0]:
        return "Cea mai puternica baza contractuala" + (" (confirmati punctele deschise)" if o != "LIKELY_COVERED" else "")
    if verdict["type"] == "equivalent" and p in eligible and verdict["text"].find(p["product_label"]) >= 0:
        return "Echivalent cu primul clasat"
    return {
        "LIKELY_COVERED": "Alternativa viabila",
        "POTENTIALLY_COVERED": "De confirmat cu asiguratorul",
        "PARTIALLY_COVERED": "Acoperire partiala",
        "LIKELY_EXCLUDED": "Nerecomandat pentru acest incident",
        "INSUFFICIENT_INFORMATION": "Nu se poate evalua",
    }[o]
