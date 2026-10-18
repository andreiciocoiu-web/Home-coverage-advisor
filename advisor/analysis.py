"""Motorul de analiza a acoperirii si de detectie a excluderilor.

Flux pentru fiecare produs:
1. documente aplicabile (versions.resolve_applicable) pentru data de referinta;
2. regasire hibrida a clauzelor relevante (acoperire, excluderi, limite, conditii, oferta);
3. evaluare categoriala per componenta a pierderii:
   - mod LLM (Claude, iesire structurata) - daca este configurata cheia API;
   - mod euristic (reguli explicite) - fallback offline si baza pentru teste;
4. verificarea literala a fiecarui citat (evidence.verify);
5. scoring determinist (scoring.score_product) si ranking.
"""
from __future__ import annotations

import re
import uuid

from . import db, security, versions
from .config import load_json_config, settings
from .evidence import evidence_record, verify
from .facts import apply_answers, follow_up_questions
from .retrieval import feedback_boosts, index
from .scoring import DIM_LABELS, OUTCOMES, current_methodology, rank, score_product
from .textnorm import normalize

DISCLAIMER = ("Rezultatul nu garanteaza despagubirea. Decizia finala depinde de contractul aplicabil, de faptele reale, "
              "de dovezile depuse si de evaluarea daunei de catre asigurator, in conditiile legii. Scorurile masoara "
              "puterea dovezilor contractuale disponibile, nu probabilitatea de plata.")
NS = "Nespecificat in documentele analizate"

# grupuri de cuvinte cheie (toate grupurile trebuie sa apara) pentru recunoasterea unei clauze de acoperire
PERIL_KW = {
    "water_damage": [r"\bapa\b|\bapei\b|\bapelor\b", r"conduct|instalati|teav|robinet|calorifer"],
    "roof_infiltration": [r"acoperis|invelitoar", r"\bapa\b|apei|precipitati|ploa"],
    "flood": [r"inundati"],
    "fire": [r"incendi"],
    "electrical": [r"fenomen\w* electric|supratensiun|scurtcircuit|scurt-circuit"],
    "theft": [r"\bfurt(ul|ului|uri|urile)?\b"],
    "vandalism": [r"vandal"],
    "glass_breakage": [r"geam|sticl|oglind"],
    "accidental_damage": [r"(daun|avari|distruger|deterior|pagub)\w* accidental\w*|toate riscurile"],
    "natural_hazards": [r"furtun|grindin|cutremur|zapad"],
    "civil_liability": [r"raspunder\w* civil"],
}

EXCLUSION_THEMES = [
    # (tema, regex in textul excluderii, fapt asociat, riscuri pentru care tema este relevanta)
    ("gradual", r"treptat|lent\b|lenta|in timp|continu|infiltrati|umezeal|mucegai|condens|igrasi",
     "sudden_or_gradual", {"water_damage", "roof_infiltration", "flood"}),
    ("wear", r"uzur|coroziun|rugin|intretiner|vechime|deteriorar\w* progresiv",
     "maintenance_wear", {"water_damage", "roof_infiltration", "electrical", "natural_hazards", "accidental_damage"}),
    ("unoccupied", r"nelocuit|neocupat|nelocuita|nu este locuit|fara a fi locuit",
     "unoccupied_period_days", {"water_damage", "theft", "vandalism", "roof_infiltration", "fire"}),
    ("groundwater", r"freatic|refular", "probable_cause", {"water_damage", "flood"}),
    ("no_forced_entry", r"fara urme de efractie|fara efractie|neincuiat|ferestre deschise|usi deschise",
     "preventive_measures", {"theft"}),
    ("construction_defect", r"vici\w* ascuns|defect\w* de (constructie|executie|proiectare)|executie defectuoasa",
     "probable_cause", {"water_damage", "roof_infiltration", "natural_hazards"}),
    ("negligence", r"neglijen|culpa grava|intentie|intentionat", "negligence", None),
]

_OPTIONAL_RE = r"optional|clauz\w* (suplimentar|aditional|optional)|la cererea (asiguratului|contractantului)|contra (unei )?prim\w* suplimentar|numai daca (a fost|este) (inclus|mentionat|specificat)|daca (a fost|este) (inclus|optat|selectat)"
_EXCEPTION_RE = r"(cu exceptia|exceptand|nu se aplica (in cazul|daca|atunci)|cu conditia ca|in afara cazului)"
_AMOUNT_RE = r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d+)?|\d+(?:,\d+)?)\s*(lei|ron|eur|euro|%)"


# ============================================================================ utilitare
def _segments(text: str) -> list[str]:
    """Imparte o clauza in segmente citabile (randuri / propozitii / litere), ca subsiruri exacte."""
    segs = []
    # reface randurile rupte de layout-ul PDF (rand care nu se termina cu punctuatie de final)
    joined, buf = [], ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        buf = f"{buf} {line}".strip() if buf else line
        if " | " in line or re.search(r"[.;:!?]$", line) or re.match(r"^\s*([a-z]|\d+(\.\d+)*)[.)]\s", line) and len(line) < 15:
            joined.append(buf)
            buf = ""
    if buf:
        joined.append(buf)
    for line in joined:
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.;])\s+(?=[a-zA-ZĂÂÎȘȚ(])", line)
        segs.extend(p.strip() for p in parts if len(p.strip()) > 10)
    return segs or [text.strip()]


def _matches_all(text: str, groups: list[str]) -> bool:
    t = normalize(text)
    return all(re.search(g, t) for g in groups)


def _best_quote(text: str, groups: list[str], extra: str | None = None) -> str | None:
    cands = [s for s in _segments(text) if _matches_all(s, groups) and (not extra or re.search(extra, normalize(s)))]
    if not cands:
        cands = [s for s in _segments(text) if re.search(groups[0], normalize(s))]
    if not cands:
        return None
    q = min(cands, key=len)
    return q[:500]


def _fact(facts: dict, key: str) -> tuple:
    f = facts.get(key) or {}
    return f.get("value"), f.get("status", "unknown")


# ============================================================================ regasire
def gather_candidates(product_docs: list[dict], perils: list[str], components: list[dict]) -> dict[str, dict]:
    cfg = load_json_config("perils.json")["perils"]
    doc_ids = {d["id"] for d in product_docs}
    boosts = feedback_boosts()
    found: dict[str, dict] = {}

    def add(rows, tag):
        for r in rows:
            r = found.setdefault(r["id"], r)
            r.setdefault("tags", set()).add(tag)

    perils_all = list(dict.fromkeys(perils + (["civil_liability"] if any(c["category"] == "third_party" for c in components) else [])))
    for p in perils_all:
        pc = cfg.get(p)
        if not pc:
            continue
        for qy in pc["coverage_queries"]:
            add(index.search(qy, doc_ids, top_k=6, boosts=boosts), f"coverage:{p}")
        for qy in pc["exclusion_queries"]:
            add(index.search(qy, doc_ids, kinds={"exclusion", "endorsement_change", "other"}, top_k=6, boosts=boosts), f"exclusion:{p}")
        add(index.search(pc["label"] + " limita de despagubire sublimita fransiza", doc_ids,
                         kinds={"limit", "deductible", "schedule", "coverage"}, top_k=5, boosts=boosts), f"limit:{p}")
        add(index.search(pc["label"] + " obligatii asigurat masuri conditii avizare", doc_ids,
                         kinds={"obligation", "claims"}, top_k=4, boosts=boosts), f"condition:{p}")
    _apply_endorsements(product_docs, found)
    # toate clauzele de tip excludere care contin teme relevante (excluderile generale nu au mereu cuvintele riscului)
    for r in db.q("""SELECT c.*, d.insurer, d.product, d.doc_type, d.hierarchy_level, d.filename, d.effective_date,
                     d.version_label, d.ocr_pages FROM clauses c JOIN documents d ON d.id=c.document_id
                     WHERE c.document_id IN (%s) AND (c.kind IN ('exclusion','endorsement_change','schedule')
                     OR d.doc_type IN ('offer','schedule','endorsement'))""" % ",".join("?" * len(doc_ids)), list(doc_ids)):
        if r["id"] not in found:
            r["retrieval"] = {"score": 0, "ranks": {"structural": 0}}
            add([r], "structural")
    _apply_endorsements(product_docs, found)
    return found


def _apply_endorsements(product_docs: list[dict], found: dict[str, dict]) -> None:
    """Clauzele din conditii modificate expres printr-un act aditional aplicabil sunt scoase din analiza
    (raman in audit). Se detecteaza formule de tipul 'Articolul 4 se modifica / se inlocuieste / se abroga'."""
    end_ids = [d["id"] for d in product_docs if d["doc_type"] == "endorsement"]
    base_ids = {d["id"] for d in product_docs if d["doc_type"] in ("policy_conditions", "special_conditions")}
    if not end_ids or not base_ids:
        return
    modified = set()
    for r in db.q("SELECT text FROM clauses WHERE document_id IN (%s)" % ",".join("?" * len(end_ids)), end_ids):
        for m in re.finditer(r"art(?:icolul|\.)?\s*(\d+(?:\.\d+)*)\s*(?:din [^.]{0,40})?se (modifica|inlocuieste|abroga)", normalize(r["text"])):
            modified.add(m.group(1))
    for cid in list(found):
        c = found[cid]
        if c["document_id"] in base_ids and c.get("number") and any(c["number"] == n or c["number"].startswith(n + ".") for n in modified):
            found.pop(cid)


# ============================================================================ analizor euristic
def _coverage_for(component: dict, perils: list[str], cands: dict[str, dict], offers: list[dict]) -> dict:
    if component["category"] == "third_party":
        perils = ["civil_liability"]
    else:
        perils = [p for p in perils if p != "civil_liability"] or perils
    best = None
    for p in perils:
        groups = PERIL_KW.get(p)
        if not groups:
            continue
        for c in cands.values():
            if c["kind"] in ("exclusion", "claims"):
                continue
            if not _matches_all(c["text"], groups):
                continue
            # o clauza care doar EXCLUDE riscul nu este acoperire
            q = _best_quote(c["text"], groups)
            if not q or re.search(r"nu se acorda|nu (acopera|despagubeste)|sunt excluse|se exclude", normalize(q)):
                continue
            if c["kind"] in ("coverage", "endorsement_change"):
                level = "express"
            elif c["doc_type"] in ("offer", "schedule"):
                continue  # oferta confirma selectia, nu defineste acoperirea
            elif re.search(r"toate riscurile", normalize(c["text"])):
                level = "general"
            else:
                level = "ambiguous"
            rank_key = ({"express": 0, "general": 1, "ambiguous": 2}[level], c["hierarchy_level"] or 5,
                        -c.get("retrieval", {}).get("score", 0))
            if best is None or rank_key < best[0]:
                best = (rank_key, c, q, level, p)
    if not best and perils and component["category"] != "third_party":
        # acoperire de tip "toate riscurile" (all risks): sustine acoperirea doar la nivel general
        for c in sorted(cands.values(), key=lambda c: c["hierarchy_level"] or 5):
            if c["kind"] in ("coverage", "endorsement_change") and (c["hierarchy_level"] or 5) <= 2:
                q = _best_quote(c["text"], [r"toate riscurile|all risk"])
                if q and re.search(r"toate riscurile|all risk", normalize(q)) and not re.search(r"fransiz|se modifica", normalize(q)):
                    return {"level": "general", "peril": perils[0], "optional": False, "offer_status": None,
                            "findings": [{"clause_id": c["id"], "quote": q,
                                          "explanation": "Acoperire formulata general (toate riscurile, cu exceptia celor excluse); "
                                                         "riscul nu este numit expres, deci incadrarea depinde de definitii si excluderi."}]}
    if not best:
        return {"level": "none_found", "findings": [], "peril": perils[0] if perils else None}
    _, c, q, level, p = best
    findings = [{"clause_id": c["id"], "quote": q,
                 "explanation": f"Clauza mentioneaza expres riscul „{load_json_config('perils.json')['perils'][p]['label']}” "
                                f"(tip clauza: {c['kind']}, document: {c['filename']})."}]
    # acoperire optionala?
    ctx = normalize(" ".join([c.get("title") or "", c.get("section_path") or "", c["text"]]))
    optional = re.search(_OPTIONAL_RE, ctx)
    offer_status = None
    if optional:
        offer_status, offer_finding = _offer_status(p, offers)
        if offer_status == "selected":
            findings.append(offer_finding)
        elif offer_status == "not_selected":
            level = "optional_not_selected"
            findings.append(offer_finding)
        else:
            level = "optional_unconfirmed"
    return {"level": level, "findings": findings, "peril": p, "optional": bool(optional), "offer_status": offer_status}


def _offer_status(peril: str, offers: list[dict]) -> tuple[str, dict | None]:
    groups = PERIL_KW.get(peril, [])
    for c in offers:
        for line in c["text"].split("\n"):
            ln = normalize(line)
            if groups and re.search(groups[0], ln) and (len(groups) < 2 or re.search(groups[-1], ln) or peril == "civil_liability"):
                if re.search(r"\b(nu|neinclus\w*|neselectat\w*|exclus\w*)\b|\|\s*-\s*(\||$)", ln):
                    return "not_selected", {"clause_id": c["id"], "quote": line.strip()[:400],
                                            "explanation": "Oferta/polita indica acoperirea ca neinclusa."}
                if re.search(r"\b(da|inclus\w*|selectat\w*)\b|\d", ln):
                    return "selected", {"clause_id": c["id"], "quote": line.strip()[:400],
                                        "explanation": "Oferta/polita confirma includerea acoperirii optionale."}
    return "unknown", None


def _exclusions_for(component: dict, perils: list[str], cands: dict[str, dict], facts: dict) -> list[dict]:
    if component["category"] == "third_party":
        rel_perils = {"civil_liability"} | set(perils)
    else:
        rel_perils = set(p for p in perils if p != "civil_liability") or set(perils)
    out = []
    other_kw = {p: g[0] for p, g in PERIL_KW.items() if p not in rel_perils}
    for c in cands.values():
        if c["kind"] not in ("exclusion", "endorsement_change"):
            continue
        if c["kind"] == "endorsement_change" and not re.search(r"exclu|nu se acorda|nu (acopera|despagubeste)", normalize(c["text"])):
            continue
        head = normalize((c.get("section_path") or "") + " " + (c.get("title") or ""))
        own_kw = [PERIL_KW[p][0] for p in rel_perils if p in PERIL_KW]
        # excludere specifica altei garantii (ex. "Excluderi specifice furtului") -> nu se aplica
        if any(re.search(k, head) for k in other_kw.values()) and not any(re.search(k, head) for k in own_kw):
            continue
        for seg in _segments(c["text"]):
            s = normalize(seg)
            for theme, pat, fact_key, theme_perils in EXCLUSION_THEMES:
                if not re.search(pat, s):
                    continue
                if theme_perils is not None and not (theme_perils & rel_perils):
                    continue
                applies, why = _theme_applies(theme, s, facts, component)
                sudden_v0, sudden_st0 = _fact(facts, "sudden_or_gradual")
                if applies is None and theme == "gradual" and sudden_st0 == "provided" and sudden_v0 == "brusc" \
                        and re.search(_EXCEPTION_RE + r".{0,120}(brusc|brust|accidental|neprevazut|spargere)", s):
                    # excluderea exista, dar exceptia ei pentru evenimente bruste corespunde faptelor declarate
                    applies, why = "exception_restores", ("Excluderea daunelor treptate are o exceptie pentru evenimente bruste; "
                                                          "utilizatorul a declarat ca evenimentul a fost brusc.")
                if applies is None:
                    continue
                exception_quote = None
                m = re.search(_EXCEPTION_RE, s)
                if m:
                    # segmentul original si cel normalizat au aceeasi structura; taiem din original la acelasi cuvant
                    mo = re.search(_EXCEPTION_RE, normalize(seg))
                    exception_quote = seg[mo.start():][:300] if mo else None
                    ex_n = normalize(exception_quote or "")
                    sudden_v, sudden_st = _fact(facts, "sudden_or_gradual")
                    shut_v, shut_st = _fact(facts, "water_shutoff")
                    if applies == "exception_restores":
                        pass
                    elif theme in ("gradual", "wear") and re.search(r"brusc|brust|accidental|neprevazut|spargere", ex_n):
                        if sudden_st == "provided" and sudden_v == "treptat":
                            why += " Exceptia (eveniment brusc) nu se aplica: utilizatorul a indicat o dauna treptata."
                        elif sudden_st == "provided" and sudden_v == "brusc":
                            applies, why = "exception_restores", why + " Exceptia de la excludere (eveniment brusc) pare sa restabileasca acoperirea."
                        elif applies in ("clearly", "likely"):
                            applies, why = "possible_material", why + " Acoperirea depinde de exceptia pentru evenimente bruste, neconfirmata."
                        else:
                            why += " Clauza prevede o exceptie pentru evenimente bruste/accidentale; caracterul brusc trebuie confirmat."
                    elif theme == "unoccupied" and re.search(r"opri|goli|apa|robinet", ex_n) and applies in ("clearly", "likely"):
                        if shut_st == "provided" and shut_v == "da":
                            applies, why = "exception_restores", why + " Utilizatorul a confirmat oprirea apei, ceea ce corespunde exceptiei de la excludere."
                        elif shut_st == "provided" and shut_v == "nu":
                            why += " Utilizatorul a confirmat ca apa NU a fost oprita; exceptia nu se aplica."
                        else:
                            applies, why = "possible_material", why + " Exceptia (oprirea apei / golirea instalatiilor) nu este confirmata."
                    elif applies in ("clearly", "likely"):
                        applies, why = "possible_material", why + " Exista o exceptie de la excludere a carei aplicare trebuie verificata."
                im = re.match(r"^\s*([a-z])\)", seg)
                out.append({"clause_id": c["id"], "quote": seg[:500], "applies": applies, "theme": theme,
                            "item": im.group(1) if im else None,
                            "exception_quote": exception_quote,
                            "scope": "specific" if any(re.search(k, head) for k in own_kw) else "general",
                            "explanation": why})
                break
    # deduplicare: cel mai sever per tema
    order = ["exception_restores", "possible", "possible_material", "likely", "clearly"]
    best: dict[str, dict] = {}
    for e in out:
        k = e["theme"]
        if k not in best or order.index(e["applies"]) > order.index(best[k]["applies"]):
            best[k] = e
    return list(best.values())


def _theme_applies(theme: str, seg: str, facts: dict, component: dict) -> tuple[str | None, str]:
    v, st = _fact(facts, {"gradual": "sudden_or_gradual", "wear": "maintenance_wear", "unoccupied": "unoccupied_period_days",
                          "groundwater": "probable_cause", "no_forced_entry": "preventive_measures",
                          "construction_defect": "probable_cause", "negligence": "negligence"}[theme])
    if (facts.get({"gradual": "sudden_or_gradual", "wear": "maintenance_wear", "negligence": "negligence",
                   "no_forced_entry": "preventive_measures"}.get(theme, "_")) or {}).get("negative"):
        if theme in ("wear", "negligence"):
            return None, ""
    if theme == "gradual":
        if st == "provided" and v == "treptat":
            return "clearly", "Utilizatorul a indicat o dauna produsa treptat, iar clauza exclude astfel de daune."
        if st == "provided" and v == "brusc":
            return None, ""
        return "possible", "Nu este stabilit daca dauna s-a produs brusc sau treptat; excluderea s-ar aplica daunelor treptate."
    if theme == "wear":
        if st == "provided":
            return "likely", "Utilizatorul a mentionat uzura/coroziune/lipsa intretinerii, vizate de excludere."
        return "possible", "Starea de intretinere a instalatiei nu este cunoscuta; excluderea pentru uzura/intretinere poate deveni relevanta."
    if theme == "unoccupied":
        m = re.search(r"(\d+)\s*(de\s*)?(zile|zi)", seg) or re.search(r"(\d+)\s*(de\s*)?(saptamani|luni)", seg)
        threshold = None
        if m:
            threshold = int(m.group(1)) * (7 if "sapt" in m.group(3) else 30 if "lun" in m.group(3) else 1)
        if isinstance(v, (int, float)):
            if v == 0:
                return None, ""
            if threshold is not None:
                if v > threshold:
                    return "clearly", f"Locuinta a fost nelocuita {int(v)} zile, peste pragul de {threshold} zile din clauza."
                return None, ""
            return "likely", "Locuinta a fost nelocuita; clauza leaga excluderea de neocupare (prag nespecificat in fragment)."
        if st == "unknown":
            return "possible", "Nu se stie daca locuinta era nelocuita si pentru cat timp" + (f" (prag in clauza: {threshold} zile)." if threshold else ".")
        return "possible", "Durata exacta a perioadei de neocupare nu este stabilita."
    if theme == "groundwater":
        if v and re.search(r"freatic|refular", normalize(str(v))):
            return "clearly", "Cauza indicata (ape freatice/refulare) este exclusa."
        return None, ""
    if theme == "no_forced_entry":
        if v and re.search(r"efracti|fortat|spart", normalize(str(v))):
            return None, ""
        return "possible", "Nu este stabilit daca exista urme de efractie / daca accesul era asigurat conform cerintelor."
    if theme == "construction_defect":
        if v and re.search(r"defect|vici|executie|montaj", normalize(str(v))):
            return "likely", "Cauza indicata ar putea constitui un defect de executie/constructie exclus."
        return None, ""
    if theme == "negligence":
        if st == "provided":
            return "possible", "Utilizatorul a mentionat o posibila neglijenta; efectul depinde de formularea exacta (ex. culpa grava)."
        return None, ""
    return None, ""


def _limits_for(component: dict, cov: dict, cands: dict[str, dict]) -> list[dict]:
    if cov["level"] in ("none_found", "optional_not_selected"):
        return []
    p = cov.get("peril")
    groups = PERIL_KW.get(p or "", [])
    out = []
    for c in cands.values():
        if c["kind"] not in ("limit", "deductible", "schedule", "coverage", "endorsement_change") and c["doc_type"] not in ("offer", "schedule"):
            continue
        for seg in _segments(c["text"]):
            s = normalize(seg)
            if not re.search(_AMOUNT_RE, s):
                continue
            is_rc = bool(re.search(r"raspunder\w* civil", s))
            generic = re.search(r"orice eveniment|fiecare eveniment|toate riscurile", s)
            if component["category"] == "third_party":
                related = is_rc or generic
            else:
                related = (not is_rc) and ((groups and re.search(groups[0], s)) or generic)
            if not related:
                continue
            if re.search(r"fransiz|franciz|deductibil", s):
                impact = "deductible_only"
            elif re.search(r"limit|sublimit|maxim|cel mult|nu (va )?depas", s):
                impact = "may_reduce"
            else:
                continue
            amount = re.search(_AMOUNT_RE, s).group(0)
            out.append({"clause_id": c["id"], "quote": seg[:400], "impact": impact, "amount_text": amount,
                        "explanation": ("Fransiza aplicabila: " if impact == "deductible_only" else "Limita/sublimita care poate reduce despagubirea: ") + amount})
    # cel mult 4, prioritate sublimitelor
    out.sort(key=lambda x: 0 if x["impact"] == "may_reduce" else 1)
    seen, res = set(), []
    for o in out:
        if o["quote"] in seen:
            continue
        seen.add(o["quote"])
        res.append(o)
    return res[:4]


def _conditions_for(component: dict, perils: list[str], cands: dict[str, dict], facts: dict) -> list[dict]:
    out = []
    days, dst = _fact(facts, "unoccupied_period_days")
    pol, pst = _fact(facts, "police_report")
    for c in cands.values():
        if c["kind"] not in ("obligation", "claims"):
            continue
        for seg in _segments(c["text"]):
            s = normalize(seg)
            if re.search(r"nelocuit|neocupat|absen", s) and re.search(r"opri|inchid|goli|robinet|apa", s) and \
                    ({"water_damage", "roof_infiltration"} & set(perils)):
                if isinstance(days, (int, float)) and days == 0:
                    continue
                shut, shut_st = _fact(facts, "water_shutoff")
                if shut_st == "provided" and shut == "da":
                    status, expl = "met", "Obligatia de a opri apa pe durata neocuparii pare respectata (confirmat de utilizator)."
                elif shut_st == "provided" and shut == "nu":
                    status, expl = "unmet_likely", "Utilizatorul a indicat ca apa nu a fost oprita, desi locuinta era nelocuita."
                else:
                    status = "unmet_risk" if (isinstance(days, (int, float)) and days > 0) or dst == "unknown" else "unknown"
                    expl = "Obligatie de a opri/goli instalatiile pe durata neocuparii; nu se stie daca a fost respectata."
                out.append({"clause_id": c["id"], "quote": seg[:400], "status": status, "explanation": expl,
                            "facts": ["unoccupied_period_days", "water_shutoff"]})
            elif re.search(r"(aviz|anunt|notific|instiint)\w*", s) and re.search(r"\d+\s*(de\s*)?(ore|zile)|termen", s):
                out.append({"clause_id": c["id"], "quote": seg[:400], "status": "manageable",
                            "explanation": "Termen de notificare a daunei - conditie procedurala care trebuie respectata."})
            elif re.search(r"politi|autoritat", s) and ({"theft", "vandalism"} & set(perils)):
                st = "met" if pst == "provided" and pol and "sesizat" in normalize(str(pol)) else "unknown"
                out.append({"clause_id": c["id"], "quote": seg[:400], "status": st, "facts": ["police_report"],
                            "explanation": "Sesizarea autoritatilor este conditie pentru despagubire."})
    seen, res = set(), []
    for o in out:
        key = (o["status"], o["explanation"])
        if key in seen:
            continue
        seen.add(key)
        res.append(o)
    return res[:4]


def _conflicts(product_docs: list[dict], perils: list[str], cands: dict[str, dict], comps: list[dict]) -> list[dict]:
    out = []
    has_contract = any(d["hierarchy_level"] == 1 for d in product_docs)
    for p in perils:
        groups = PERIL_KW.get(p)
        if not groups:
            continue
        ipid_cov = [c for c in cands.values() if c["doc_type"] == "ipid" and c["kind"] != "exclusion" and _matches_all(c["text"], groups)]
        ipid_exc = [c for c in cands.values() if c["doc_type"] == "ipid" and c["kind"] == "exclusion" and _matches_all(c["text"], groups)]
        cont_cov = [c for c in cands.values() if c["hierarchy_level"] == 1 and c["kind"] in ("coverage", "endorsement_change") and _matches_all(c["text"], groups)]
        cont_exc = [c for c in cands.values() if c["hierarchy_level"] == 1 and c["kind"] == "exclusion" and _matches_all(c["text"], groups)]
        if has_contract and ipid_cov and not cont_cov and not cont_exc:
            a = ipid_cov[0]
            out.append({"clause_a": a["id"], "quote_a": _best_quote(a["text"], groups), "clause_b": None, "quote_b": None,
                        "explanation": "IPID-ul mentioneaza acoperirea, dar conditiile contractuale analizate nu contin o prevedere "
                                       "corespunzatoare. IPID-ul nu prevaleaza asupra contractului; necesita verificare umana."})
        if ipid_exc and cont_cov:
            a, b = ipid_exc[0], cont_cov[0]
            out.append({"clause_a": a["id"], "quote_a": _best_quote(a["text"], groups), "clause_b": b["id"], "quote_b": _best_quote(b["text"], groups),
                        "explanation": "IPID-ul prezinta riscul ca exclus, iar conditiile contractuale il acopera. Conflict de documente; "
                                       "contractul este baza principala, dar diferenta trebuie clarificata cu asiguratorul."})
        if ipid_cov and cont_exc and not cont_cov:
            a, b = ipid_cov[0], cont_exc[0]
            out.append({"clause_a": a["id"], "quote_a": _best_quote(a["text"], groups), "clause_b": b["id"], "quote_b": _best_quote(b["text"], groups),
                        "explanation": "IPID-ul indica acoperirea, iar conditiile contractuale contin o excludere pentru acelasi risc. "
                                       "Necesita analiza juridica/umana."})
    return out


def heuristic_assessment(product_docs, perils, components, facts, cands) -> dict:
    offers = [c for c in cands.values() if c["doc_type"] in ("offer", "schedule")]
    comps = []
    for comp in components:
        cov = _coverage_for(comp, perils, cands, offers)
        comps.append({
            "component": comp["component"], "category": comp["category"],
            "coverage_level": cov["level"], "coverage_findings": cov["findings"],
            "exclusions": _exclusions_for(comp, perils, cands, facts) if cov["level"] not in ("none_found",) else [],
            "limits": _limits_for(comp, cov, cands),
            "conditions": _conditions_for(comp, perils, cands, facts),
        })
    theme_fact = {t[0]: t[2] for t in EXCLUSION_THEMES}
    relevant = {"probable_cause", "occurrence_date"}
    for c in comps:
        for x in c["exclusions"]:
            relevant.add(theme_fact.get(x["theme"], ""))
            if x["theme"] == "unoccupied" and x.get("exception_quote"):
                relevant.add("water_shutoff")
        for x in c["conditions"]:
            relevant.update(x.get("facts", []))
    return {"components": comps, "conflicts": _conflicts(product_docs, perils, cands, comps),
            "clarity_level": None, "summary": None, "relevant_facts": sorted(relevant - {""})}


# ============================================================================ analizor LLM
_LLM_SYSTEM = """Esti specialist in interpretarea conditiilor de asigurare a locuintelor din Romania.
Primesti faptele unui incident (cu statutul lor: provided/inferred/unknown) si clauze extrase din documentele
APLICABILE ale UNUI SINGUR produs. Evaluezi, per componenta a pierderii, baza contractuala pentru despagubire.

Reguli obligatorii:
1. Foloseste EXCLUSIV clauzele furnizate. Nu folosi cunostinte generale despre produs sau piata.
2. Orice constatare trebuie sa aiba clause_id dintr-o clauza furnizata si un citat COPIAT LITERAL din acea clauza
   (fara parafrazare, fara '...'). Daca nu exista text-suport, foloseste nivelul 'none_found' / nu raporta constatarea.
3. Ierarhia documentelor: nivel 1 (conditii, conditii speciale, acte aditionale, clauze) > 2 (polita/oferta) >
   3 (IPID) > 4 (brosura). IPID/brosura nu pot suprascrie contractul. Semnaleaza contradictiile in 'conflicts'.
4. Excluderi: interpreteaza in context; nu aplica o excludere doar pe baza unui cuvant comun. Distinge excluderile
   generale de cele specifice altor garantii (care nu se aplica). Identifica exceptiile de la excluderi.
   'applies': clearly (faptele stabilite o declanseaza), likely, possible (depinde de fapte necunoscute),
   exception_restores (o exceptie restabileste acoperirea). Nu raporta excluderi irelevante.
5. Faptele 'inferred' sau 'unknown' NU sunt fapte stabilite: nu pot face o excludere 'clearly'.
6. Acoperire: express (risc numit expres), general (formulare generala/all-risk), optional_unconfirmed (acoperire
   optionala fara confirmare in oferta/polita), optional_not_selected (oferta arata ca nu a fost aleasa), ambiguous,
   none_found. Daunele la terti se analizeaza DOAR prin garantia de raspundere civila; nu presupune ca o garantie
   acopera toate pierderile din acelasi eveniment.
7. Informatia lipsa NU este interpretata favorabil. Nu inventa sume, fransize sau prevederi; scrie
   "Nespecificat in documentele analizate".
8. Nu atribui scoruri numerice si nu estima probabilitati de plata. Raspunde in romana."""


def _llm_schema() -> dict:
    finding = {"type": "object", "properties": {"clause_id": {"type": "string"}, "quote": {"type": "string"},
                                                "explanation": {"type": "string"}}, "required": ["clause_id", "quote", "explanation"]}
    return {
        "type": "object",
        "properties": {
            "components": {"type": "array", "items": {"type": "object", "properties": {
                "component": {"type": "string"},
                "category": {"type": "string", "enum": ["building", "contents", "third_party", "expenses", "other"]},
                "coverage_level": {"type": "string", "enum": ["express", "general", "optional_unconfirmed", "optional_not_selected", "ambiguous", "none_found"]},
                "coverage_findings": {"type": "array", "items": finding},
                "exclusions": {"type": "array", "items": {"type": "object", "properties": {
                    "clause_id": {"type": "string"}, "quote": {"type": "string"},
                    "applies": {"type": "string", "enum": ["clearly", "likely", "possible_material", "possible", "exception_restores"],
                                "description": "possible_material = faptele stabilite declanseaza excluderea, dar o exceptie neconfirmata ar putea restabili acoperirea"},
                    "item": {"type": ["string", "null"], "description": "litera sub-punctului, daca exista (ex. 'a')"},
                    "scope": {"type": "string", "enum": ["general", "specific"]},
                    "exception_quote": {"type": ["string", "null"]}, "explanation": {"type": "string"}},
                    "required": ["clause_id", "quote", "applies", "explanation"]}},
                "limits": {"type": "array", "items": {"type": "object", "properties": {
                    "clause_id": {"type": "string"}, "quote": {"type": "string"},
                    "impact": {"type": "string", "enum": ["no_material_limit", "deductible_only", "may_reduce", "severe"]},
                    "amount_text": {"type": ["string", "null"]}, "explanation": {"type": "string"}},
                    "required": ["clause_id", "quote", "impact", "explanation"]}},
                "conditions": {"type": "array", "items": {"type": "object", "properties": {
                    "clause_id": {"type": "string"}, "quote": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "manageable", "unknown", "unmet_risk", "unmet_likely"]},
                    "explanation": {"type": "string"}}, "required": ["clause_id", "quote", "status", "explanation"]}},
            }, "required": ["component", "category", "coverage_level", "coverage_findings", "exclusions", "limits", "conditions"]}},
            "conflicts": {"type": "array", "items": {"type": "object", "properties": {
                "clause_a": {"type": "string"}, "quote_a": {"type": "string"}, "clause_b": {"type": ["string", "null"]},
                "quote_b": {"type": ["string", "null"]}, "explanation": {"type": "string"}}, "required": ["clause_a", "quote_a", "explanation"]}},
            "clarity_level": {"type": "string", "enum": ["clear_direct", "clear_with_definitions", "ambiguous"]},
            "summary": {"type": "string"},
            "reject_reasons": {"type": "array", "items": {"type": "string"}},
            "documents_to_obtain": {"type": "array", "items": {"type": "string"}},
            "clarify_before_purchase": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["components", "conflicts", "clarity_level", "summary"],
    }


def llm_assessment(product_key, perils, components, facts, cands) -> dict:
    import json
    from .llm import call_tool, wrap_untrusted
    ordered = sorted(cands.values(), key=lambda c: (c["hierarchy_level"] or 5, -c.get("retrieval", {}).get("score", 0)))[:45]
    blocks = []
    for c in ordered:
        blocks.append(wrap_untrusted("clause", c["text"][:3000], {
            "id": c["id"], "doc": c["filename"], "doc_type": c["doc_type"], "level": c["hierarchy_level"],
            "number": c.get("number") or "", "kind": c["kind"], "location": c["location_label"],
            "effective_date": c.get("effective_date") or "", "section": c.get("section_path") or ""}))
    facts_view = {k: v for k, v in facts.items()}
    user = (f"Produs analizat: {product_key}\nRiscuri detectate: {', '.join(perils)}\n"
            f"Componente ale pierderii: {json.dumps(components, ensure_ascii=False)}\n"
            f"Fapte (cu statut): {json.dumps(facts_view, ensure_ascii=False, default=str)}\n\n"
            "<document_data>\n" + "\n".join(blocks) + "\n</document_data>")
    return call_tool(_LLM_SYSTEM, user, "record_coverage_assessment", _llm_schema(),
                     "Inregistreaza evaluarea acoperirii pentru produs")


# ============================================================================ verificare + agregare
def _verify_assessment(raw: dict, product_key: str, cands: dict[str, dict]) -> dict:
    n_ver = n_unv = 0
    unverified = []
    ocr = False

    def ev(criterion, finding, cid, quote, interp, extra=None):
        nonlocal n_ver, n_unv, ocr
        ok, note = verify(cid, quote, cands)
        cl = cands.get(cid)
        rec = evidence_record(product_key, criterion, finding, cl, quote, interp, ok, note, extra)
        if ok:
            n_ver += 1
            if cl and cl.get("ocr_pages"):
                ocr = True
        else:
            n_unv += 1
            unverified.append(rec)
        return rec, ok

    comps = []
    for c in raw.get("components", []):
        cov_ev = []
        for f in c.get("coverage_findings", []):
            rec, ok = ev("Acoperire", c["component"], f.get("clause_id"), f.get("quote"), f.get("explanation", ""))
            if ok:
                cov_ev.append(rec)
        level = c.get("coverage_level", "none_found")
        if not cov_ev and level != "none_found":
            level = "none_found"  # fara citat verificat nu exista acoperire dovedita
        exs = []
        for x in c.get("exclusions", []):
            extra = {"applies": x.get("applies"), "exception_quote": x.get("exception_quote"), "scope": x.get("scope")}
            cl0 = cands.get(x.get("clause_id") or "")
            if x.get("item") and cl0 and cl0.get("number"):
                extra["clause_number"] = f"Art. {cl0['number']} lit. {x['item']})"
            rec, ok = ev("Excludere", c["component"], x.get("clause_id"), x.get("quote"), x.get("explanation", ""), extra)
            exs.append({"applies": x.get("applies"), "verified": ok, "evidence": rec})
        lims = []
        for x in c.get("limits", []):
            rec, ok = ev("Limita / fransiza", c["component"], x.get("clause_id"), x.get("quote"), x.get("explanation", ""),
                         {"impact": x.get("impact"), "amount_text": x.get("amount_text")})
            lims.append({"impact": x.get("impact"), "verified": ok, "evidence": rec, "amount_text": x.get("amount_text")})
        conds = []
        for x in c.get("conditions", []):
            rec, ok = ev("Conditie", c["component"], x.get("clause_id"), x.get("quote"), x.get("explanation", ""),
                         {"status": x.get("status")})
            conds.append({"status": x.get("status"), "verified": ok, "evidence": rec})
        comps.append({"component": c["component"], "category": c.get("category", "other"), "coverage_level": level,
                      "coverage_evidence": cov_ev, "exclusions": exs, "limits": lims, "conditions": conds})
    conflicts = []
    for x in raw.get("conflicts", []):
        ra, oka = ev("Conflict", "Document A", x.get("clause_a"), x.get("quote_a"), x.get("explanation", ""))
        rb = None
        if x.get("clause_b"):
            rb, _ = ev("Conflict", "Document B", x.get("clause_b"), x.get("quote_b"), x.get("explanation", ""))
        if oka:
            conflicts.append({"explanation": x.get("explanation"), "evidence": ra, "evidence_b": rb})
    return {"components": comps, "conflicts": conflicts, "n_verified": n_ver, "n_unverified": n_unv,
            "unverified": unverified, "ocr_in_evidence": ocr,
            "clarity_level": raw.get("clarity_level"), "summary": raw.get("summary"),
            "reject_reasons_llm": raw.get("reject_reasons") or [], "documents_to_obtain_llm": raw.get("documents_to_obtain") or [],
            "clarify_llm": raw.get("clarify_before_purchase") or []}


def _derive_clarity(a: dict) -> str:
    if a.get("clarity_level"):
        return a["clarity_level"]
    prim = next((c for c in a["components"] if c["category"] != "third_party"), a["components"][0] if a["components"] else None)
    if not prim:
        return "not_found"
    lvl = prim["coverage_level"]
    if lvl == "express" and all((e.get("hierarchy_level") or 5) <= 2 for e in prim["coverage_evidence"]):
        return "clear_direct"
    if lvl in ("express", "general"):
        return "clear_with_definitions"
    return "ambiguous"


_DOCS_BY_PERIL = {
    "water_damage": "Constatarea cauzei (raport instalator / administratia blocului), fotografii, devize de reparatie",
    "roof_infiltration": "Constatare tehnica a acoperisului, date meteo pentru ziua evenimentului, fotografii",
    "flood": "Dovada fenomenului (comunicari ISU/ANM), fotografii, inventarul bunurilor afectate",
    "fire": "Raportul ISU / pompieri, fotografii, inventarul bunurilor",
    "electrical": "Constatare electrician / service, dovada achizitiei aparatelor, eventual confirmare de la distribuitorul de energie",
    "theft": "Dovada sesizarii la politie, inventarul bunurilor sustrase, dovezi de proprietate/valoare",
    "vandalism": "Dovada sesizarii la politie, fotografii",
    "glass_breakage": "Fotografii, deviz de inlocuire",
    "accidental_damage": "Descrierea circumstantelor, fotografii, devize",
    "natural_hazards": "Confirmarea fenomenului meteo (ANM), fotografii, constatare tehnica",
    "civil_liability": "Constatare comuna cu tertul pagubit, pretentia scrisa a tertului, devize",
}


def analyze(incident_text: str, extraction: dict, answers: dict | None, user: dict | None) -> dict:
    facts = apply_answers(extraction["facts"], answers or {})
    perils = facts.get("incident_type", {}).get("value") or []
    components = extraction.get("loss_components") or []
    from .facts import derive_components
    if answers:  # raspunsurile pot adauga componente (ex. daune la terti)
        components = _merge_components(components, derive_components(facts))
    ref_date = facts.get("occurrence_date", {}).get("value") if facts.get("occurrence_date", {}).get("status") == "provided" else None
    mversion, cfg = current_methodology()
    use_llm = settings.llm_enabled
    mode = "llm" if use_llm else "heuristic"
    open_questions = follow_up_questions(facts, max_q=10)
    unknown_material = [q["question"] for q in open_questions]

    results = []
    for pkey, docs in versions.products().items():
        applicable, limitations = versions.resolve_applicable(docs, ref_date)
        if not any(d["doc_type"] not in ("offer",) for d in applicable):
            continue
        cands = gather_candidates(applicable, perils, components)
        if not cands:
            continue
        product_mode = mode
        llm_error = None
        if use_llm:
            try:
                raw = llm_assessment(pkey, perils, components, facts, cands)
            except Exception as e:
                llm_error = f"{type(e).__name__}: {e}"
                product_mode = "heuristic (fallback)"
                raw = heuristic_assessment(applicable, perils, components, facts, cands)
        else:
            raw = heuristic_assessment(applicable, perils, components, facts, cands)
        a = _verify_assessment(raw, pkey, cands)
        a["clarity_level"] = _derive_clarity(a)
        a["version_undetermined"] = any("NU poate fi stabilita" in l for l in limitations)
        flagged = {d["id"] for d in applicable if d.get("injection_flags") not in (None, "[]", "")}
        a["injection_flags"] = [d["filename"] for d in applicable if d["id"] in flagged]
        a["injection_in_evidence"] = any(e.get("document_id") in flagged for c in a["components"] for e in c["coverage_evidence"])
        if flagged:
            limitations.append("Documente cu text suspect de instructiuni catre AI (tratat strict ca date, semnalat pentru revizuire): "
                               + ", ".join(a["injection_flags"]))
        rel = raw.get("relevant_facts")
        a["unknown_material_facts"] = [q["question"] for q in open_questions if rel is None or q["fact"] in rel]
        sc = score_product(a, facts, cfg)
        insurer = docs[0]["insurer"] or NS
        product = docs[0]["product"] or NS
        results.append({
            "product_key": pkey, "insurer": insurer, "product": product, "product_label": f"{insurer} - {product}",
            "documents": [{"id": d["id"], "filename": d["filename"], "doc_type": d["doc_type"], "effective_date": d["effective_date"],
                           "version": d["version_label"], "hierarchy_level": d["hierarchy_level"]} for d in applicable],
            "version_limitations": limitations, "assessment": a, "score": sc, "mode": product_mode, "llm_error": llm_error,
            "details": _details(a, sc, perils, facts, open_questions),
        })
    verdict = rank(results, cfg)
    for r in results:
        r["table"] = _table_row(r)
    analysis_id = uuid.uuid4().hex[:12]
    out = {
        "id": analysis_id, "created_at": db.now(), "mode": mode, "retrieval_backend": index.backend,
        "methodology_version": mversion, "weights": cfg["weights"], "facts": facts, "perils": perils,
        "loss_components": components, "reference_date": ref_date, "open_questions": open_questions,
        "products": results, "verdict": verdict, "radar": _radar(results, cfg), "disclaimer": DISCLAIMER,
        "outcome_labels": OUTCOMES, "dimension_labels": DIM_LABELS,
    }
    with db.tx() as c:
        c.execute("INSERT INTO analyses(id, created_at, username, incident_enc, facts_json, answers_json, result_json, methodology_version, mode) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (analysis_id, out["created_at"], (user or {}).get("username", "system"), security.encrypt_text(incident_text),
                   db.dumps(facts), security.encrypt_text(db.dumps(answers or {})),
                   security.encrypt_text(db.dumps(out)), mversion, mode))
    security.audit(user, "analysis.run", analysis_id, {"produse": len(results), "mod": mode, "metodologie": mversion,
                                                        "verdict": verdict["type"]})
    return out


def _merge_components(a: list[dict], b: list[dict]) -> list[dict]:
    cats = {c["category"] for c in a}
    return a + [c for c in b if c["category"] not in cats]


def _table_row(r: dict) -> dict:
    a, sc = r["assessment"], r["score"]
    prim = next((c for c in a["components"] if c["category"] != "third_party"), a["components"][0] if a["components"] else None)
    sup = (prim or {}).get("coverage_evidence") or []
    sev = ["possible", "possible_material", "likely", "clearly"]
    exs = [x for c in a["components"] for x in c["exclusions"] if x["verified"] and x["applies"] in sev]
    exs.sort(key=lambda x: sev.index(x["applies"]), reverse=True)
    lims = [l for c in a["components"] for l in c["limits"] if l["verified"]]
    return {
        "rank": r["rank"], "insurer": r["insurer"], "product": r["product"], "outcome": sc["outcome_label"],
        "score": sc["overall"], "evidence_confidence": sc["evidence_confidence"], "uncertainty": sc["uncertainty"],
        "main_clause": (f"{sup[0]['clause_number']} ({sup[0]['location']})" if sup else NS),
        "main_exclusion": (f"{exs[0]['evidence']['clause_number']}: {exs[0]['evidence']['interpretation'][:140]}" if exs else "Nicio excludere materiala identificata in documentele analizate"),
        "deductible_or_sublimit": ("; ".join(dict.fromkeys(f"{l.get('amount_text') or ''} ({'fransiza' if l['impact']=='deductible_only' else 'limita'})" for l in lims)) if lims else NS),
        "missing_info": [q for q in a.get("unknown_material_facts", [])][:3],
        "recommendation": r["recommendation"],
    }


def _details(a: dict, sc: dict, perils: list[str], facts: dict, open_questions: list[dict]) -> dict:
    comps = a["components"]
    covered_parts, not_covered = [], []
    for c in comps:
        if c.get("component_score", 0) >= 50 or c["coverage_level"] in ("express", "general"):
            covered_parts.append(c["component"])
        else:
            not_covered.append(c["component"])
    exclusions = [x["evidence"] for c in comps for x in c["exclusions"] if x["verified"]]
    limits = [l["evidence"] for c in comps for l in c["limits"] if l["verified"]]
    reject = list(a.get("reject_reasons_llm") or [])
    for x in exclusions:
        if x.get("applies") in ("clearly", "likely", "possible"):
            reject.append(f"{x['clause_number']}: {x['interpretation']}")
    for c in comps:
        for x in c["conditions"]:
            if x["verified"] and x["status"] in ("unknown", "unmet_risk", "unmet_likely"):
                reject.append(f"Nerespectarea conditiei {x['evidence']['clause_number']}: {x['evidence']['interpretation']}")
    docs = list(a.get("documents_to_obtain_llm") or [])
    for p in perils:
        if p in _DOCS_BY_PERIL:
            docs.append(_DOCS_BY_PERIL[p] + " [recomandare generala a aplicatiei, nu cerinta extrasa din contract]")
    if any(c["category"] == "third_party" for c in comps) and "civil_liability" not in perils:
        docs.append(_DOCS_BY_PERIL["civil_liability"] + " [recomandare generala a aplicatiei]")
    clarify = list(a.get("clarify_llm") or [])
    for c in comps:
        if c["coverage_level"] in ("optional_unconfirmed", "ambiguous", "none_found"):
            clarify.append(f"Este acoperita componenta „{c['component']}”? (nivel identificat: {c['coverage_level']})")
        for x in c["exclusions"]:
            if x["verified"] and x["applies"] in ("possible", "possible_material"):
                clarify.append(f"Cum se aplica excluderea {x['evidence']['clause_number']} in situatia descrisa?")
    if not limits:
        clarify.append("Care sunt sumele asigurate, sublimitele si fransizele aplicabile acestui tip de dauna?")
    return {
        "supporting_clauses": [e for c in comps for e in c["coverage_evidence"]],
        "covered_components": covered_parts, "uncovered_components": not_covered,
        "exclusions": exclusions, "unknown_facts": a.get("unknown_material_facts") or [],
        "limits": limits, "reject_reasons": list(dict.fromkeys(reject))[:10],
        "documents_to_obtain": list(dict.fromkeys(docs))[:8], "clarify_before_purchase": list(dict.fromkeys(clarify))[:8],
        "summary": a.get("summary"), "unverified": a.get("unverified", []), "conflicts": a.get("conflicts", []),
        "property_vs_liability": [{"component": c["component"], "category": c["category"], "coverage_level": c["coverage_level"]} for c in comps],
    }


def _radar(results: list[dict], cfg: dict) -> dict:
    dims = cfg["radar_dimensions"]
    top = [r for r in results][:4]
    return {"labels": [DIM_LABELS[d] for d in dims], "keys": dims,
            "series": [{"label": r["product_label"], "values": [r["score"]["dimensions"][d]["score"] for d in dims]} for r in top],
            "note": "Graficul compara produsele DOAR pentru incidentul introdus. Nu este un clasament general al produselor. "
                    "Scala 0-100 per dimensiune, conform rubricilor din metodologie."}
