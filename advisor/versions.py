"""Managementul versiunilor: duplicate, versiuni conflictuale, acte aditionale si
determinarea versiunii aplicabile pentru o data de referinta.

Regula de baza: documentul cel mai recent INCARCAT nu este presupus aplicabil.
Aplicabilitatea se determina din data de intrare in vigoare extrasa din text
(sau corectata de un reviewer). Cand aceasta nu poate fi stabilita, limitarea
este semnalata explicit in analiza.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import db
from .textnorm import normalize


def _shingles(text: str, k: int = 6) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", normalize(text))
    return {" ".join(toks[i:i + k]) for i in range(max(0, len(toks) - k + 1))}


def doc_text(doc_id: str) -> str:
    return "\n".join(r["text"] for r in db.q("SELECT text FROM clauses WHERE document_id=? ORDER BY ordinal", (doc_id,)))


def product_key(insurer: str | None, product: str | None) -> str:
    return f"{insurer or 'Asigurator nedeterminat'} :: {product or 'Produs nedeterminat'}"


def active_documents() -> list[dict]:
    return db.q("SELECT * FROM documents WHERE status='ok' AND deleted_at IS NULL")


def rebuild_relations() -> list[dict]:
    docs = active_documents()
    rels: list[tuple[str, str, str, dict]] = []
    by_hash = defaultdict(list)
    for d in docs:
        by_hash[d["content_hash"]].append(d)
    for h, ds in by_hash.items():
        for other in ds[1:]:
            rels.append((ds[0]["id"], other["id"], "duplicate", {"motiv": "continut identic (hash)"}))

    groups = defaultdict(list)
    for d in docs:
        groups[(d["insurer"], normalize(d["product"] or ""), d["doc_type"])].append(d)
    shingle_cache: dict[str, set] = {}
    for (ins, prod, dtype), ds in groups.items():
        uniq = {d["content_hash"]: d for d in ds}.values()
        uniq = list(uniq)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                sa = shingle_cache.setdefault(a["id"], _shingles(doc_text(a["id"])))
                sb = shingle_cache.setdefault(b["id"], _shingles(doc_text(b["id"])))
                jac = len(sa & sb) / max(1, len(sa | sb))
                detail = {"similaritate": round(jac, 3), "data_a": a["effective_date"], "data_b": b["effective_date"],
                          "versiune_a": a["version_label"], "versiune_b": b["version_label"],
                          "clauze_diferite": diff_clause_numbers(a["id"], b["id"])}
                if dtype in ("offer", "schedule"):
                    continue  # oferte multiple pentru acelasi produs sunt legitime
                if jac > 0.97:
                    rels.append((a["id"], b["id"], "near_duplicate", detail))
                else:
                    rels.append((a["id"], b["id"], "version_conflict", detail))

    for d in docs:
        if d["doc_type"] != "endorsement":
            continue
        refs = sorted(set(re.findall(r"art(?:icolul|\.)?\s*(\d+(?:\.\d+)*)", normalize(doc_text(d["id"])))))
        for base in docs:
            if base["id"] != d["id"] and base["insurer"] == d["insurer"] and normalize(base["product"] or "") == normalize(d["product"] or "") \
                    and base["doc_type"] in ("policy_conditions", "special_conditions"):
                rels.append((d["id"], base["id"], "amends", {"articole_referite": refs}))

    with db.tx() as c:
        c.execute("DELETE FROM doc_relations")
        c.executemany("INSERT INTO doc_relations(doc_a, doc_b, relation, detail_json) VALUES (?,?,?,?)",
                      [(a, b, r, db.dumps(dt)) for a, b, r, dt in rels])
    return [{"doc_a": a, "doc_b": b, "relation": r, "detail": dt} for a, b, r, dt in rels]


def diff_clause_numbers(doc_a: str, doc_b: str) -> list[str]:
    ca = {r["number"]: normalize(r["text"]) for r in db.q(
        "SELECT number, text FROM clauses WHERE document_id=? AND number IS NOT NULL", (doc_a,))}
    cb = {r["number"]: normalize(r["text"]) for r in db.q(
        "SELECT number, text FROM clauses WHERE document_id=? AND number IS NOT NULL", (doc_b,))}
    out = [n for n in sorted(set(ca) | set(cb)) if ca.get(n) != cb.get(n)]
    return out[:40]


def resolve_applicable(docs: list[dict], ref_date: str | None) -> tuple[list[dict], list[str]]:
    """Alege documentele aplicabile pentru un produs la data de referinta.

    Intoarce (documente_aplicabile, limitari). Pentru fiecare tip de document:
    - duplicatele exacte sunt colapsate;
    - daca exista o singura versiune, ea este folosita (cu avertisment daca nu are data);
    - daca exista mai multe versiuni cu date, se alege cea mai recenta cu data <= referinta;
    - daca exista versiuni fara data, nu se poate stabili aplicabilitatea -> toate sunt
      pastrate si limitarea este semnalata.
    Ofertele si actele aditionale nu sunt colapsate (pot coexista legitim).
    """
    limitations: list[str] = []
    by_type = defaultdict(list)
    seen_hash = set()
    for d in docs:
        if d["content_hash"] in seen_hash:
            continue
        seen_hash.add(d["content_hash"])
        by_type[d["doc_type"]].append(d)
    chosen: list[dict] = []
    for dtype, ds in by_type.items():
        if dtype in ("offer", "endorsement", "optional_coverage", "schedule") or len(ds) == 1:
            for d in ds:
                if dtype not in ("offer", "brochure") and not d["effective_date"]:
                    limitations.append(f"Data de intrare in vigoare nu a putut fi stabilita pentru „{d['filename']}”.")
                if ref_date and d["effective_date"] and d["effective_date"] > ref_date and dtype != "offer":
                    limitations.append(f"„{d['filename']}” intra in vigoare la {d['effective_date']}, dupa data de referinta {ref_date}.")
            chosen.extend(ds)
            continue
        dated = [d for d in ds if d["effective_date"]]
        undated = [d for d in ds if not d["effective_date"]]
        if undated:
            names = ", ".join(f"„{d['filename']}”" for d in ds)
            limitations.append(
                f"Exista {len(ds)} versiuni de tip „{dtype}” ({names}), dintre care cel putin una fara data de "
                "intrare in vigoare. Versiunea aplicabila NU poate fi stabilita; analiza le include pe toate si "
                "necesita verificare umana.")
            chosen.extend(ds)
            continue
        ref = ref_date or "9999-12-31"
        eligible = sorted([d for d in dated if d["effective_date"] <= ref], key=lambda d: d["effective_date"])
        if eligible:
            pick = eligible[-1]
            others = [d for d in ds if d is not pick]
            chosen.append(pick)
            limitations.append(
                f"Pentru tipul „{dtype}” s-a folosit „{pick['filename']}” (in vigoare din {pick['effective_date']}); "
                f"excluse ca neaplicabile la data {ref_date or 'curenta'}: " + ", ".join(f"„{o['filename']}”" for o in others) +
                ". Aplicabilitatea depinde si de data emiterii politei, care trebuie confirmata.")
        else:
            chosen.extend(ds)
            limitations.append(f"Nicio versiune de tip „{dtype}” nu este in vigoare la data {ref_date}; toate sunt incluse cu rezerva.")
    return chosen, limitations


def products() -> dict[str, list[dict]]:
    """Grupeaza documentele pe produs (asigurator + denumire normalizata, fara diacritice/majuscule)."""
    groups = defaultdict(list)
    for d in active_documents():
        groups[(d["insurer"], normalize(d["product"] or ""))].append(d)
    out = {}
    for (_ins, _p), ds in groups.items():
        best = min(ds, key=lambda d: d["hierarchy_level"] or 5)  # eticheta din documentul contractual
        out[product_key(best["insurer"], best["product"])] = ds
    return out
