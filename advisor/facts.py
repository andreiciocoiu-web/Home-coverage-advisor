"""Extragerea faptelor din descrierea incidentului si generarea intrebarilor
de clarificare.

Fiecare fapt are un statut:
- provided : spus explicit de utilizator (cu fragment din textul lui ca dovada);
- inferred : dedus din descriere (nu este tratat ca fapt stabilit);
- unknown  : necunoscut.
Un fapt marcat 'provided' fara fragment verificabil in textul utilizatorului este
retrogradat automat la 'inferred'.
"""
from __future__ import annotations

import re

from .config import load_json_config
from .textnorm import contains_quote, normalize

FIELDS = list(load_json_config("perils.json")["fact_fields"].keys())

_EN2RO = {
    "burst": "spart", "pipe": "teava", "water": "apa", "bathroom": "baie", "parquet": "parchet",
    "furniture": "mobila", "ceiling": "tavan", "apartment below": "apartamentul de dedesubt",
    "unoccupied": "nelocuit", "weeks": "saptamani", "week": "saptamana", "days": "zile", "months": "luni",
    "three": "trei", "two": "doua", "four": "patru", "fire": "incendiu", "theft": "furt", "flood": "inundatie",
    "roof": "acoperis", "neighbor": "vecin", "neighbour": "vecin", "apartment": "apartament", "house": "casa",
    "short circuit": "scurtcircuit", "power surge": "supratensiune", "storm": "furtuna", "broken window": "geam spart",
    "leak": "scurgere", "gradual": "treptat", "suddenly": "brusc", "police": "politie", "rust": "rugina",
}
_NUM_WORDS = {"o": 1, "un": 1, "una": 1, "doua": 2, "doi": 2, "trei": 3, "patru": 4, "cinci": 5, "sase": 6,
              "sapte": 7, "opt": 8, "noua": 9, "zece": 10}
_UNIT_DAYS = {"zi": 1, "zile": 1, "saptamana": 7, "saptamani": 7, "luna": 30, "luni": 30}


def _pre(text: str) -> str:
    t = normalize(text)
    for en, ro in _EN2RO.items():
        t = re.sub(rf"\b{re.escape(en)}\b", ro, t)
    return t


def _snippet(original: str, pattern: str) -> str | None:
    """Gaseste fragmentul ORIGINAL care corespunde unui pattern pe textul normalizat."""
    norm = normalize(original)
    m = re.search(pattern, norm)
    if not m:
        return None
    # fragmentul normalizat are aceeasi lungime ca originalul doar aproximativ; luam propozitia
    sentences = re.split(r"(?<=[.!?])\s+", original)
    for s in sentences:
        if re.search(pattern, normalize(s)):
            return s.strip()[:300]
    return original[:300]


def detect_perils(text: str) -> list[dict]:
    t = _pre(text)
    out = []
    for key, p in load_json_config("perils.json")["perils"].items():
        hits = [trg for trg in p["triggers"] if re.search(r"(?<![a-z])" + re.escape(normalize(trg)), t)]
        if hits:
            out.append({"peril": key, "label": p["label"], "triggers": hits})
    return out


def heuristic_facts(text: str) -> dict:
    t = _pre(text)
    f: dict[str, dict] = {k: {"value": None, "status": "unknown", "evidence": None} for k in FIELDS}

    def setf(k, value, status, pattern=None, evidence=None):
        ev = evidence or (_snippet(text, pattern) if pattern else None)
        f[k] = {"value": value, "status": status, "evidence": ev}

    perils = detect_perils(text)
    if perils:
        setf("incident_type", [p["peril"] for p in perils], "inferred",
             evidence="Detectat din cuvintele: " + ", ".join(sorted({h for p in perils for h in p["triggers"]})))
    m = re.search(r"\b(apartament|garsonier\w*|casa|vila|locuint\w* individual\w*|duplex)\b", t)
    if m:
        setf("property_type", m.group(1), "provided", re.escape(m.group(1)) if m.group(1) in normalize(text) else None)
        if f["property_type"]["evidence"] is None:
            f["property_type"]["status"] = "inferred"
    m = re.search(r"\b(?:in|din)\s+(bucuresti|cluj[- ]napoca|cluj|iasi|timisoara|constanta|brasov|craiova|galati|ploiesti|oradea|sibiu|[a-z]+ sector \d)\b", t)
    if m:
        setf("location", m.group(1).title(), "provided", re.escape(m.group(1)))
    m = re.search(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b", t)
    if m:
        d, mo, y = re.split(r"[./-]", m.group(1))
        setf("occurrence_date", f"{y}-{int(mo):02d}-{int(d):02d}", "provided", re.escape(m.group(1)))
    # daune directe vs terti
    items = ["parchet", "mobil", "tavan", "pereti", "perete", "usi", "usa", "gresie", "faianta", "electrocasnic",
             "aparat", "televizor", "laptop", "haine", "covor", "bucatari", "instalati", "acoperis", "geam", "bijuteri"]
    third_ctx = r"(vecin\w*|dedesubt|de jos|tert\w*|alt apartament)"
    direct, third = [], []
    for sent in re.split(r"(?<=[.!?;])\s+|,\s*(?=si\b)", t):
        found = [i for i in items if re.search(rf"\b{i}", sent)]
        if not found:
            continue
        ctx = re.search(third_ctx, sent)
        if ctx:
            # in aceeasi propozitie pot aparea si bunuri proprii: un bun este atribuit tertului doar daca
            # apare imediat inaintea contextului ("tavanul apartamentului de dedesubt") sau dupa el
            pos = ctx.start()
            for i in found:
                ipos = re.search(rf"\b{i}", sent).start()
                between = sent[ipos:pos]
                # bunul apartine tertului daca este legat direct de contextul tert (fara enumerare intre ele)
                is_third = ipos > pos or (0 <= pos - ipos <= 45 and not re.search(r",|\bsi\b", between))
                (third if is_third else direct).append(i)
        else:
            direct.extend(found)
    if direct:
        setf("direct_damage", sorted(set(direct)), "provided", "|".join(sorted(set(direct))))
    if third or re.search(third_ctx, t):
        setf("third_party_damage", sorted(set(third)) or "da (bunuri ale tertilor afectate)", "provided", third_ctx)
        setf("civil_liability", "posibila (daune cauzate tertilor)", "inferred",
             evidence="Dedus din mentionarea daunelor la terti; raspunderea depinde de cauza si de lege.")
    if re.search(r"\b(treptat|in timp\b|de (cateva )?(luni|saptamani)|de mult timp|de mult\b(?! ori)|pete\b|umezeal|scurger\w* lent\w*|mucegai|picura)", t):
        setf("sudden_or_gradual", "treptat", "provided", r"treptat|in timp|pete|umezeal|scurgere lenta|mucegai")
    elif re.search(r"\b(brusc|dintr-o data|subit)", t):
        setf("sudden_or_gradual", "brusc", "provided", r"brusc|dintr-o data|subit")
    elif re.search(r"\b(spart|a cedat|fisurat|s-a rupt|explodat)", t):
        setf("sudden_or_gradual", "probabil brusc", "inferred",
             evidence="Dedus din descrierea unei spargeri; caracterul brusc nu a fost confirmat explicit.")
    m = re.search(r"(nelocuit\w*|neocupat\w*|plecat\w*|nu a locuit nimeni|nu (a )?fost nimeni|goala?)[^.]{0,60}?\b(\d+|o|un|una|doua|doi|trei|patru|cinci|sase|sapte|opt|noua|zece)\s+(?:de\s+)?(zile|zi|saptamani|saptamana|luni|luna)\b", t)
    if not m:
        m2 = re.search(r"\b(\d+|o|un|una|doua|doi|trei|patru|cinci|sase|sapte|opt|noua|zece)\s+(?:de\s+)?(zile|zi|saptamani|saptamana|luni|luna)\b[^.]{0,40}(nelocuit|neocupat|plecat|goala?)", t)
        if m2:
            n, unit = m2.group(1), m2.group(2)
            days = (int(n) if n.isdigit() else _NUM_WORDS[n]) * _UNIT_DAYS[unit]
            setf("unoccupied_period_days", days, "provided", re.escape(m2.group(0)[:40]))
    if m:
        n, unit = m.group(3), m.group(4)
        days = (int(n) if n.isdigit() else _NUM_WORDS[n]) * _UNIT_DAYS[unit]
        setf("unoccupied_period_days", days, "provided", r"nelocuit|neocupat|plecat|nu a locuit|goal")
    elif f["unoccupied_period_days"]["status"] == "unknown" and re.search(r"\b(locuiam|locuim|locuiesc|eram acasa|era locuit\w*|locuita permanent|locuim permanent)\b", t) \
            and not re.search(r"\b(nelocuit|neocupat|plecat)", t):
        setf("unoccupied_period_days", 0, "provided", r"locuiam|locuim|locuiesc|eram acasa|era locuit")
    elif f["unoccupied_period_days"]["status"] == "unknown" and re.search(r"\b(nelocuit|neocupat)", t):
        setf("unoccupied_period_days", "nelocuita (durata necunoscuta)", "provided", r"nelocuit|neocupat")
    if re.search(r"(oprit|inchis|golit)\w*[^.]{0,30}(apa|robinet|instalati)", t):
        setf("water_shutoff", "da", "provided", r"(oprit|inchis|golit)")
    elif re.search(r"(nu am|nu a fost)\s+(oprit|inchis|golit)", t):
        setf("water_shutoff", "nu", "provided", r"nu am|nu a fost")
    if re.search(r"\b(uzur|coroziun|rugin|vech\w* de|nerevizuit|neintretinut)", t):
        setf("maintenance_wear", "semne de uzura/coroziune mentionate", "provided", r"uzur|coroziun|rugin|nerevizuit|neintretinut")
    if re.search(r"\b(am uitat|lasat\w* deschis|neglij|nu am inchis|deschis\w* fereastra)", t):
        setf("negligence", "posibila neglijenta mentionata", "provided", r"am uitat|lasat\w* deschis|neglij|nu am inchis")
    if re.search(r"\b(alarm|usa metalica|incuiat|yala|grilaj)", t):
        setf("preventive_measures", "masuri mentionate", "provided", r"alarm|usa metalica|incuiat|yala|grilaj")
    if re.search(r"\bpoliti", t):
        setf("police_report", "politia a fost sesizata" if re.search(r"(sesiz|anunt|chemat|reclamat)\w*[^.]{0,20}politi|politi\w*[^.]{0,20}(sesiz|anunt|venit)", t) else "mentionata",
             "provided", r"politi")
    if re.search(r"(raspundere civila|optional|clauza suplimentar|am (inclus|ales|bifat))", t):
        setf("policy_options", "optiuni mentionate de utilizator", "provided", r"raspundere civila|optional|clauza suplimentar|am (inclus|ales|bifat)")
    if re.search(r"(fransiz|franciz|deductibil|limita)", t):
        setf("limits_deductibles", "mentionate de utilizator", "provided", r"fransiz|franciz|deductibil|limita")
    m = re.search(r"(?:cauz\w*|din cauza|provocat\w* de|deoarece|pentru ca)\s+([^.]{5,120})", t)
    if m:
        setf("probable_cause", m.group(1).strip(), "provided", re.escape(m.group(0)[:30]))
    elif perils:
        setf("probable_cause", perils[0]["label"], "inferred",
             evidence="Dedus din tipul incidentului; cauza tehnica nu a fost stabilita.")
    f["location"] = f["location"] if f["location"]["value"] else f["location"]
    return f


FACT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "object",
            "properties": {k: {
                "type": "object",
                "properties": {
                    "value": {"type": ["string", "number", "array", "null"]},
                    "status": {"type": "string", "enum": ["provided", "inferred", "unknown"]},
                    "evidence": {"type": ["string", "null"],
                                 "description": "Pentru 'provided': fragment copiat EXACT din textul utilizatorului. Pentru 'inferred': rationamentul."},
                }, "required": ["value", "status", "evidence"]} for k in FIELDS},
        },
        "perils": {"type": "array", "items": {"type": "string"},
                   "description": "Chei din taxonomia de riscuri relevante pentru incident"},
        "loss_components": {"type": "array", "items": {"type": "object", "properties": {
            "component": {"type": "string"}, "category": {"type": "string", "enum": ["building", "contents", "third_party", "expenses", "other"]},
            "evidence": {"type": "string"}}, "required": ["component", "category"]}},
    },
    "required": ["facts", "perils", "loss_components"],
}

FACT_SYSTEM = """Esti analist de daune pentru asigurari de locuinte in Romania. Extragi FAPTELE dintr-o descriere de incident,
fara a decide acoperirea. Reguli stricte:
- 'provided' doar daca utilizatorul a spus explicit; 'evidence' = fragment copiat exact din textul lui.
- 'inferred' pentru deductii (explica in 'evidence'); nu transforma niciodata o presupunere in fapt.
- 'unknown' daca nu rezulta din text. Nu inventa date, sume, cauze.
- 'perils': alege doar din cheile: {perils}.
- 'loss_components': separa daunele la bunurile proprii (cladire / bunuri mobile) de daunele la terti.
Raspunde in limba romana."""


def llm_facts(text: str) -> dict:
    from .llm import call_tool, wrap_untrusted
    perils = list(load_json_config("perils.json")["perils"].keys())
    out = call_tool(FACT_SYSTEM.format(perils=", ".join(perils)), wrap_untrusted("user_incident", text),
                    "record_incident_facts", FACT_TOOL_SCHEMA, "Inregistreaza faptele incidentului", max_tokens=3000)
    facts = out.get("facts", {})
    for k in FIELDS:
        facts.setdefault(k, {"value": None, "status": "unknown", "evidence": None})
        fv = facts[k]
        if fv.get("status") == "provided" and not (fv.get("evidence") and contains_quote(text, fv["evidence"])):
            fv["status"] = "inferred"
            fv["evidence"] = f"[retrogradat: fragmentul nu a fost gasit literal in descriere] {fv.get('evidence') or ''}"
    facts["incident_type"] = {"value": [p for p in out.get("perils", []) if p in perils],
                              "status": "inferred", "evidence": "Clasificare a descrierii"}
    return {"facts": facts, "loss_components": out.get("loss_components", [])}


def derive_components(facts: dict) -> list[dict]:
    comps = []
    dd = facts.get("direct_damage", {}).get("value")
    building_words = ("parchet", "tavan", "pereti", "perete", "usi", "usa", "gresie", "faianta", "instalati", "acoperis", "geam")
    if dd:
        items = dd if isinstance(dd, list) else [str(dd)]
        b = [i for i in items if any(w in normalize(i) for w in building_words)]
        c = [i for i in items if i not in b]
        if b:
            comps.append({"component": "Elemente ale cladirii: " + ", ".join(b), "category": "building"})
        if c:
            comps.append({"component": "Bunuri mobile: " + ", ".join(c), "category": "contents"})
    if facts.get("third_party_damage", {}).get("status") in ("provided", "inferred") and facts["third_party_damage"]["value"]:
        v = facts["third_party_damage"]["value"]
        comps.append({"component": "Daune la terti: " + (", ".join(v) if isinstance(v, list) else str(v)), "category": "third_party"})
    if not comps:
        comps.append({"component": "Dauna la bunul asigurat (detalii necunoscute)", "category": "other"})
    return comps


def extract(text: str, use_llm: bool) -> dict:
    mode = "heuristic"
    result = None
    if use_llm:
        try:
            result = llm_facts(text)
            mode = "llm"
        except Exception as e:  # cade pe euristica, cu semnalare
            result = None
            mode = f"heuristic (LLM indisponibil: {type(e).__name__})"
    if result is None:
        facts = heuristic_facts(text)
        result = {"facts": facts, "loss_components": derive_components(facts)}
    else:
        # completam riscurile detectate lexical, ca plasa de siguranta pentru retrieval
        lex = [p["peril"] for p in detect_perils(text)]
        cur = result["facts"]["incident_type"]["value"] or []
        result["facts"]["incident_type"]["value"] = list(dict.fromkeys(cur + lex))
        if not result.get("loss_components"):
            result["loss_components"] = derive_components(result["facts"])
    result["mode"] = mode
    return result


def follow_up_questions(facts: dict, max_q: int = 5) -> list[dict]:
    """Intrebari doar pentru faptele materiale pentru riscurile detectate."""
    cfg = load_json_config("perils.json")
    perils = facts.get("incident_type", {}).get("value") or []
    needed: dict[str, int] = {}
    for p in perils:
        for i, fk in enumerate(cfg["perils"].get(p, {}).get("material_facts", [])):
            needed[fk] = min(needed.get(fk, 99), i)
    if not perils:
        needed = {"probable_cause": 0, "property_type": 1}
    # 'inferred' nu este fapt stabilit -> intrebam si pentru acestea, daca sunt materiale
    qs = []
    for fk, prio in sorted(needed.items(), key=lambda t: t[1]):
        st = facts.get(fk, {}).get("status", "unknown")
        val = facts.get(fk, {}).get("value")
        if st == "provided" and not (isinstance(val, str) and "necunoscut" in val):
            continue
        meta = cfg["fact_fields"].get(fk, {})
        if "question" in meta:
            qs.append({"fact": fk, "question": meta["question"], "why": meta["why"], "current_status": st,
                       "current_value": val})
    days = facts.get("unoccupied_period_days", {}).get("value")
    if "water_damage" in perils and isinstance(days, (int, float)) and days > 0 \
            and facts.get("water_shutoff", {}).get("status") != "provided":
        meta = cfg["fact_fields"]["water_shutoff"]
        qs.insert(min(1, len(qs)), {"fact": "water_shutoff", "question": meta["question"], "why": meta["why"],
                                     "current_status": facts.get("water_shutoff", {}).get("status", "unknown"), "current_value": None})
    if facts.get("occurrence_date", {}).get("status") == "unknown" and len(qs) < max_q:
        meta = cfg["fact_fields"]["occurrence_date"]
        qs.append({"fact": "occurrence_date", "question": meta["question"], "why": meta["why"], "current_status": "unknown", "current_value": None})
    return qs[:max_q]


def apply_answers(facts: dict, answers: dict[str, str]) -> dict:
    """Raspunsurile utilizatorului devin fapte 'provided' (sursa: raspuns)."""
    from copy import deepcopy
    f = deepcopy(facts)
    for k, ans in (answers or {}).items():
        if not ans or not str(ans).strip() or k not in f:
            continue
        val: object = ans.strip()
        if k == "unoccupied_period_days":
            t = _pre(ans)
            m = re.search(r"\b(\d+|o|un|una|doua|doi|trei|patru|cinci|sase|sapte|opt|noua|zece)\s+(?:de\s+)?(zile|zi|saptamani|saptamana|luni|luna)\b", t)
            if m:
                n = m.group(1)
                val = (int(n) if n.isdigit() else _NUM_WORDS[n]) * _UNIT_DAYS[m.group(2)]
            elif re.search(r"\b(nu|locuita|ocupata)\b", t) and not re.search(r"nelocuit", t):
                val = 0
        if k == "occurrence_date":
            m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", ans)
            if m:
                val = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        if k == "water_shutoff":
            t = _pre(ans)
            val = "nu" if re.search(r"\bnu\b", t) else ("da" if re.search(r"\b(da|oprit|inchis|golit)", t) else ans.strip())
        if k == "sudden_or_gradual":
            t = _pre(ans)
            val = "treptat" if re.search(r"treptat|in timp|lent|pete|umezeal", t) else ("brusc" if re.search(r"brusc|dintr-o data|subit|spart", t) else ans.strip())
        neg = bool(re.search(r"^\s*nu\b|\bnu (exista|a fost|am|era|sunt)\b|\bfara\b|\bnoua?\b|\brecent\w*\b|\brevizuit\w*\b|\binlocuit\w*\b", _pre(ans)))
        f[k] = {"value": val, "status": "provided", "evidence": f"Raspuns utilizator: {ans.strip()[:300]}",
                "negative": neg and k in ("maintenance_wear", "negligence", "third_party_damage", "preventive_measures", "police_report")}
    return f
