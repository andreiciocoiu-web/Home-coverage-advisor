"""Segmentarea documentelor in clauze, cu pastrarea numarului clauzei,
a caii de sectiune si a locatiei in documentul original."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .parsing import Block
from .textnorm import normalize

MAX_CLAUSE_CHARS = 2500

_ART_RE = re.compile(r"^\s*(art(?:icolul|\.)?)\s*(\d+(?:\.\d+)*)\s*[.:\-)]?\s*(.*)$", re.I)
_NUM_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]\s+([A-Za-zĂÂÎȘȚăâîșț„\"].*)$")
_SECTION_RE = re.compile(r"^\s*(capitolul|cap\.|sec[tț]iunea|partea|anexa|clauza)\s+([ivxlc]+|\d+|[a-z])\b[\s.:\-]*(.*)$", re.I)


@dataclass
class Clause:
    ordinal: int
    number: str | None
    title: str
    section_path: str
    kind: str
    text: str
    page_start: int | None
    page_end: int | None
    location_label: str


KIND_LABELS = {
    "coverage": "Acoperire / risc asigurat",
    "exclusion": "Excludere",
    "definition": "Definitie",
    "limit": "Limita / suma asigurata",
    "deductible": "Fransiza",
    "obligation": "Obligatie / conditie",
    "claims": "Procedura de dauna",
    "schedule": "Parametri oferta / polita",
    "endorsement_change": "Modificare prin act aditional",
    "other": "Altele",
}


def classify_kind(section_path: str, title: str, text: str, is_table: bool = False) -> str:
    sec = normalize(section_path)
    tit = normalize(title)
    body = normalize(text[:600])
    first = body[:220]
    if re.search(r"se modifica|se inlocuieste|se completeaza|se abroga|va avea urmatorul cuprins", first):
        return "endorsement_change"
    if is_table and re.search(r"suma asigurat|prima|fransiz|inclus|optional|da\b|nu\b", body):
        return "schedule"
    if re.search(r"nu se acorda despagubir|nu (acorda|despagubeste|plateste|acopera)|sunt excluse|nu sunt (acoperite|asigurate)|excluder", tit + " " + first):
        return "exclusion"
    if "exclu" in tit:
        return "exclusion"
    if re.search(r"fransiz|franciz|deductibil", tit + " " + first):
        return "deductible"
    if re.search(r"definit", tit) or re.search(r"^[\"„]?[a-z ]{2,40}[\"”]?\s*(-|:|inseamna|reprezinta)\s", first) and "definit" in sec:
        return "definition"
    if re.search(r"asiguratorul (acorda|despagubeste|plateste|acopera)|sunt (asigurate|acoperite)|(este|sunt) inclus\w* automat|se acorda despagubir", first[:200]):
        return "coverage"
    if re.search(r"(limit|sublimit|suma asigurat)", tit + " " + first[:120]):
        return "limit"
    if re.search(r"obligati|masuri de (prevenire|siguranta)|neocupat|nelocuit", tit + " " + first[:160]):
        return "obligation"
    if re.search(r"constatare|avizare|notificare|instrumentare|plata despagubir|procedura", tit):
        return "claims"
    if re.search(r"riscuri(le)? asigurate|acoperir|garanti|se acorda despagubir|asiguratorul (acorda|despagubeste|plateste|acopera)|sunt asigurate|sunt acoperite|raspunderea civila", tit + " " + first):
        return "coverage"
    # mostenire din sectiune
    if "exclu" in sec:
        return "exclusion"
    if "definit" in sec:
        return "definition"
    if re.search(r"fransiz", sec):
        return "deductible"
    if re.search(r"obligati", sec):
        return "obligation"
    if re.search(r"riscuri|acoperir|garanti|raspunderea civila", sec):
        return "coverage"
    if re.search(r"dauna|despagubir", sec):
        return "claims"
    return "other"


def _location(page_start, page_end, para_start, section_path) -> str:
    if page_start:
        loc = f"p. {page_start}" if page_start == page_end or not page_end else f"p. {page_start}-{page_end}"
    else:
        loc = f"paragraful {para_start}"
    return f"{loc}" + (f" | {section_path}" if section_path else "")


def extract_clauses(blocks: list[Block]) -> list[Clause]:
    clauses: list[Clause] = []
    sections: list[str] = []  # stiva de titluri
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur and cur["lines"]:
            text = "\n".join(cur["lines"]).strip()
            parts = [text]
            if len(text) > MAX_CLAUSE_CHARS:
                parts, buf = [], ""
                for ln in cur["lines"]:
                    if len(buf) + len(ln) > MAX_CLAUSE_CHARS and buf:
                        parts.append(buf.strip())
                        buf = ""
                    buf += ln + "\n"
                if buf.strip():
                    parts.append(buf.strip())
            for i, ptxt in enumerate(parts):
                title = cur["title"] + (f" (partea {i + 1})" if len(parts) > 1 else "")
                clauses.append(Clause(
                    ordinal=len(clauses), number=cur["number"], title=title,
                    section_path=cur["section"],
                    kind=classify_kind(cur["section"], cur["title"], ptxt, cur["is_table"]),
                    text=ptxt, page_start=cur["p0"], page_end=cur["p1"],
                    location_label=_location(cur["p0"], cur["p1"], cur["para0"], cur["section"]),
                ))
        cur = None

    def start(number, title, b: Block, is_table=False):
        nonlocal cur
        flush()
        cur = {"number": number, "title": title.strip()[:160], "section": " > ".join(sections[-3:]),
               "lines": [], "p0": b.page, "p1": b.page, "para0": b.para_index, "is_table": is_table}

    for b in blocks:
        line = b.text.strip()
        if not line:
            continue
        if b.is_table:
            start(None, "Tabel", b, is_table=True)
            cur["lines"].append(line)
            flush()
            continue
        sm = _SECTION_RE.match(line)
        am = _ART_RE.match(line)
        nm = _NUM_RE.match(line) if not am else None
        if sm or (b.heading_level and not am and not nm):
            # titlu de sectiune: actualizeaza stiva si porneste o clauza-titlu
            lvl = b.heading_level or 1
            label = line[:120]
            if lvl <= 1:
                sections[:] = [label]
            else:
                sections[:] = sections[: lvl - 1] + [label]
            start(sm.group(2) if sm else None, label, b)
            cur["lines"].append(line)
            continue
        if am:
            start(am.group(2), am.group(3) or f"Art. {am.group(2)}", b)
            cur["lines"].append(line)
            continue
        if nm:
            start(nm.group(1), nm.group(2), b)
            cur["lines"].append(line)
            continue
        if cur is None:
            start(None, "Preambul", b)
        cur["lines"].append(line)
        if b.page:
            cur["p1"] = b.page
    flush()
    # clauze-titlu goale (doar headingul) raman utile pentru context, dar nu pentru citare
    return clauses
