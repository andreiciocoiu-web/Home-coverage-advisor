"""Managerul de evidente: verificarea literala a citatelor si construirea lantului
Produs -> Criteriu -> Constatare -> Document -> Clauza -> Citat -> Pagina ->
Interpretare -> Incredere.

O constatare este 'verificata' doar daca:
1. clauza citata apartine documentelor aplicabile ale ACELUIASI produs;
2. citatul apare literal in textul clauzei (modulo spatii/diacritice/ghilimele).
Constatarile neverificate nu sustin scorul si sunt afisate ca atare.
"""
from __future__ import annotations

from .metadata import DOC_TYPES
from .textnorm import contains_quote


def verify(clause_id: str | None, quote: str | None, allowed: dict[str, dict]) -> tuple[bool, str]:
    if not clause_id:
        return False, "Lipseste referinta la clauza"
    cl = allowed.get(clause_id)
    if cl is None:
        return False, "Clauza citata nu apartine documentelor aplicabile ale produsului"
    if not quote or not quote.strip():
        return False, "Lipseste citatul"
    if not contains_quote(cl["text"], quote):
        return False, "Citatul nu a fost gasit literal in clauza (posibila parafrazare sau eroare)"
    return True, "Citat verificat literal in clauza"


def evidence_record(product_key: str, criterion: str, finding: str, clause: dict | None, quote: str | None,
                    interpretation: str, verified: bool, verification_note: str, extra: dict | None = None) -> dict:
    rec = {
        "product": product_key, "criterion": criterion, "finding": finding,
        "quote": quote, "interpretation": interpretation, "verified": verified,
        "verification_note": verification_note, "text_type": "contractual" if verified else "neverificat",
    }
    if clause:
        rec.update({
            "clause_id": clause["id"], "clause_number": clause.get("number") or "Nespecificat in documentele analizate",
            "clause_title": clause.get("title"), "clause_kind": clause.get("kind"),
            "location": clause.get("location_label"), "page": clause.get("page_start"),
            "document_id": clause.get("document_id"), "document": clause.get("filename"),
            "doc_type": DOC_TYPES.get(clause.get("doc_type") or "unknown", ("?", 5))[0],
            "hierarchy_level": clause.get("hierarchy_level"),
            "insurer": clause.get("insurer"), "effective_date": clause.get("effective_date"),
            "version": clause.get("version_label"),
        })
    if extra:
        rec.update(extra)
    return rec
