"""Identificare euristica a metadatelor documentului.

Toate valorile extrase sunt insotite de dovada (fragmentul de text din care au
rezultat). Unde nu exista dovada, valoarea ramane None si UI-ul o afiseaza ca
"Nespecificat in documentele analizate". Un utilizator cu rol de reviewer poate
corecta manual metadatele; corectia este auditata si marcata ca suprascriere.
"""
from __future__ import annotations

import re
from datetime import date

from .config import load_json_config
from .textnorm import normalize

DOC_TYPES = {
    "policy_conditions": ("Conditii de asigurare", 1),
    "special_conditions": ("Conditii speciale / particulare", 1),
    "endorsement": ("Act aditional / amendament", 1),
    "optional_coverage": ("Clauza / acoperire optionala", 1),
    "schedule": ("Polita / specificatie / anexa", 2),
    "offer": ("Oferta / cotatie", 2),
    "ipid": ("Document de informare (IPID/PID)", 3),
    "brochure": ("Brosura / material de prezentare", 4),
    "unknown": ("Tip nedeterminat", 5),
}

# (tip, regex pe text normalizat, pondere). Titlul/numele fisierului cantaresc mai mult.
_TYPE_RULES = [
    ("ipid", r"document de informare (privind|despre) produsul de asigurare|\bipid\b|\bpid\b", 5),
    ("endorsement", r"\bact(ul)? aditional\b|\baddendum\b|\bamendament", 5),
    ("special_conditions", r"conditii(le)? (speciale|particulare)", 4),
    ("optional_coverage", r"clauz[ae] (suplimentar|optional|aditional)\w*|acoperir\w* optional\w*", 3),
    ("offer", r"\boferta\b|\bcotati[ea]\b|prima (totala|anuala) de (plata|asigurare)", 3),
    ("schedule", r"\bspecificati[ea]\b|\banexa\b|polita (de asigurare )?(nr|seria)", 2),
    ("policy_conditions", r"conditii(le)? (generale )?de asigurare|conditii(le)? contractuale|conditii generale", 3),
    ("brochure", r"\bbrosur\w*|\bdescoper\w+ beneficii|de ce sa alegi|\bpliant\b", 3),
]

_MONTHS = {"ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5, "iunie": 6, "iulie": 7,
           "august": 8, "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12}

_DATE = r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})|(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})|(" + "|".join(_MONTHS) + r")\s+(\d{4})"
_EFFECTIVE_RE = re.compile(
    r"(valabil\w*|in vigoare|aplicabil\w*|intra in vigoare|editia|versiunea[^,.;]{0,15}|actualizat\w*|data intrarii in vigoare)"
    r"[^.;\n]{0,40}?(" + _DATE + r")")
_VERSION_RE = re.compile(r"\b(versiunea|editia|cod(?:ul)? (?:document|produs|conditii)|ver\.|rev\.)\s*(nr\.?\s*)?[:\-]?\s*([a-z0-9][a-z0-9./_-]{0,20})")
_PRODUCT_RE = re.compile(r"(?:produs(?:ul)?|program(?:ul)?|asigurare[a]?|polita)\s*(?:de asigurare)?\s*[:\-]?\s*[\"„“]([^\"”„“]{3,60})[\"”“]", re.I)


_QUOTED_RE = re.compile(r"[\"„“]([A-Z][^\"”„“\n]{2,50})[\"”“]")


def _parse_date(m: re.Match) -> date | None:
    g = m.groups()
    try:
        if g[0]:
            return date(int(g[2]), int(g[1]), int(g[0]))
        if g[3]:
            return date(int(g[5]), _MONTHS[g[4]], int(g[3]))
        if g[6]:
            return date(int(g[7]), _MONTHS[g[6]], 1)
    except ValueError:
        return None
    return None


def detect_insurer(filename: str, text: str) -> tuple[str | None, str | None]:
    cfg = load_json_config("insurers.json")["insurers"]
    fn = normalize(filename)
    head = normalize(text[:1500])
    body = normalize(text[:8000])
    best, best_score, ev = None, 0.0, None
    for name, aliases in cfg.items():
        score = 0.0
        evidence = None
        for a in aliases:
            a_n = normalize(a)
            pat = r"(?<![a-z])" + re.escape(a_n) + r"(?![a-z])"
            n_fn = len(re.findall(pat, fn))
            n_head = len(re.findall(pat, head))
            n_body = len(re.findall(pat, body))
            s = 3 * n_fn + 2 * n_head + n_body
            if name.startswith("PAID") and n_fn == 0:
                s *= 0.2  # PAID este mentionat aproape in orice polita de locuinta din Romania
            if s and evidence is None:
                m = re.search(pat, body) or re.search(pat, fn)
                if m:
                    src = body if re.search(pat, body) else fn
                    evidence = src[max(0, m.start() - 50): m.end() + 50]
            score += s
        if score > best_score:
            best, best_score, ev = name, score, evidence
    return best, ev


def detect_doc_type(filename: str, text: str) -> tuple[str, dict]:
    fn = normalize(filename)
    title = normalize(text[:300])
    head = normalize(text[300:2500])
    scores: dict[str, float] = {}
    for t, pat, w in _TYPE_RULES:
        # numele fisierului si titlul (primele ~300 caractere) primeaza asupra mentiunilor din corp
        s = 4 * len(re.findall(pat, fn)) + 4 * len(re.findall(pat, title)) + len(re.findall(pat, head))
        if s:
            scores[t] = scores.get(t, 0) + s * w
    if not scores:
        return "unknown", {}
    # Un IPID mentioneaza des "conditii de asigurare" -> titlul IPID are prioritate
    best = max(scores, key=scores.get)
    return best, scores


def detect_effective_date(text: str) -> tuple[str | None, str | None]:
    t = normalize(text[:6000])
    for m in _EFFECTIVE_RE.finditer(t):
        dm = re.search(_DATE, m.group(0))
        if dm:
            d = _parse_date(dm)
            if d and 1990 < d.year < 2100:
                return d.isoformat(), t[max(0, m.start() - 20): m.end() + 10]
    return None, None


def detect_version(text: str) -> tuple[str | None, str | None]:
    t = normalize(text[:4000])
    m = _VERSION_RE.search(t)
    if m:
        val = m.group(3).strip("./-")
        if re.search(r"\d", val) or m.group(1).startswith("cod"):
            return val.upper(), t[max(0, m.start() - 10): m.end() + 10]
    return None, None


def detect_product(filename: str, text: str, insurer: str | None) -> tuple[str | None, str | None]:
    m = _PRODUCT_RE.search(text[:5000]) or _QUOTED_RE.search(text[:600])
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        return name, text[max(0, m.start() - 10): m.end() + 10]
    # fallback: numele fisierului fara cuvinte de tip document
    stem = re.sub(r"\.(pdf|docx)$", "", filename, flags=re.I)
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"(?i)\b(conditii( generale| speciale)?|ipid|pid|oferta|act aditional|brosura|anexa|clauza|"
                  r"v\d+|versiunea|editia|\d{4}|\d{1,2}\s?\d{1,2}\s?\d{4})\b", " ", stem)
    if insurer:
        for a in load_json_config("insurers.json")["insurers"].get(insurer, []) + insurer.split():
            a = a.strip("()")
            if len(a) > 2:
                stem = re.sub(r"(?i)\b" + re.escape(a) + r"\b", " ", stem)
    stem = re.sub(r"(?i)\b(nr|scanat|final|draft|copy|copie|\d{1,2})\b", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return (stem or None), ("dedus din numele fisierului" if stem else None)


def extract_metadata(filename: str, text: str) -> dict:
    insurer, ins_ev = detect_insurer(filename, text)
    doc_type, type_scores = detect_doc_type(filename, text)
    eff, eff_ev = detect_effective_date(text)
    ver, ver_ev = detect_version(text)
    product, prod_ev = detect_product(filename, text, insurer)
    return {
        "insurer": insurer, "product": product, "doc_type": doc_type,
        "version_label": ver, "effective_date": eff,
        "hierarchy_level": DOC_TYPES[doc_type][1],
        "evidence": {"insurer": ins_ev, "product": prod_ev, "effective_date": eff_ev,
                     "version": ver_ev, "doc_type_scores": type_scores},
    }
