"""Validare fisiere, extragere text/tabele din PDF si DOCX, OCR pentru pagini scanate.

Rezultatul este o lista ordonata de blocuri care pastreaza legatura cu locatia
originala (pagina PDF sau sectiunea/paragraful DOCX).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from .config import settings


@dataclass
class Block:
    text: str
    page: int | None  # numar de pagina PDF (1-based); None pentru DOCX
    heading_level: int = 0  # 0 = text normal; 1..3 = titlu (din stil DOCX sau euristic)
    is_table: bool = False
    para_index: int = 0  # index paragraf (DOCX) - locatie cand nu exista pagini
    ocr: bool = False


@dataclass
class ParsedDoc:
    blocks: list[Block] = field(default_factory=list)
    page_count: int = 0
    ocr_pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class ValidationError(ValueError):
    pass


def validate(data: bytes, mime: str, filename: str) -> str:
    """Verifica tipul real al fisierului (semnatura), nu doar extensia/MIME declarat."""
    if len(data) > settings.max_file_mb * 1024 * 1024:
        raise ValidationError(f"Fisier prea mare (> {settings.max_file_mb} MB)")
    if len(data) < 100:
        raise ValidationError("Fisier gol sau trunchiat")
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if "word/document.xml" in names:
                    if any(n.startswith("word/vbaProject") for n in names):
                        raise ValidationError("DOCX cu macro-uri (vbaProject) respins")
                    return "docx"
        except zipfile.BadZipFile as e:
            raise ValidationError(f"Arhiva DOCX corupta: {e}") from e
    raise ValidationError(f"Tip de fisier nesuportat sau semnatura invalida ({mime}, {filename})")


# --------------------------------------------------------------------------- PDF
def _ocr_page(page) -> str:
    import pytesseract
    img = page.to_image(resolution=300).original
    langs = settings.ocr_langs
    try:
        return pytesseract.image_to_string(img, lang=langs)
    except pytesseract.TesseractError:
        # pachetul de limba romana poate lipsi; fallback explicit, semnalat in avertismente
        return pytesseract.image_to_string(img, lang="eng")


_HEADING_RE = re.compile(
    r"^\s*(CAPITOLUL|CAP\.|SEC[TȚ]IUNEA|PARTEA|ANEXA|CLAUZA|DEFINI[TȚ]II|EXCLUDERI|ARTICOLUL|ART\.)\b", re.I)


def _guess_heading_level(line: str) -> int:
    s = line.strip()
    if not s or len(s) > 140:
        return 0
    if _HEADING_RE.match(s):
        return 1 if re.match(r"^\s*(CAPITOLUL|CAP\.|SEC|PARTEA|ANEXA)", s, re.I) else 2
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(s) < 100:
        return 1
    return 0


def parse_pdf(data: bytes) -> ParsedDoc:
    import pdfplumber
    doc = ParsedDoc()
    ocr_warned = False
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        doc.page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            used_ocr = False
            if len(text.strip()) < settings.ocr_min_chars_per_page:
                try:
                    ocr_text = _ocr_page(page)
                    if len(ocr_text.strip()) > len(text.strip()):
                        text, used_ocr = ocr_text, True
                        doc.ocr_pages += 1
                except Exception as e:  # tesseract absent etc.
                    if not ocr_warned:
                        doc.warnings.append(f"OCR indisponibil sau esuat: {e}")
                        ocr_warned = True
            for line in text.splitlines():
                if line.strip():
                    doc.blocks.append(Block(line.rstrip(), i, _guess_heading_level(line), ocr=used_ocr))
            if not used_ocr:
                try:
                    for table in page.extract_tables() or []:
                        rows = [" | ".join((c or "").replace("\n", " ").strip() for c in row) for row in table if row]
                        rows = [r for r in rows if r.strip(" |")]
                        if rows:
                            doc.blocks.append(Block("\n".join(rows), i, 0, is_table=True))
                except Exception as e:
                    doc.warnings.append(f"Extragere tabel esuata la pagina {i}: {e}")
    if doc.ocr_pages:
        doc.warnings.append(f"{doc.ocr_pages} pagini procesate prin OCR - citatele din aceste pagini "
                            "pot contine erori de recunoastere si trebuie verificate pe original.")
    return doc


# --------------------------------------------------------------------------- DOCX
def parse_docx(data: bytes) -> ParsedDoc:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    doc = ParsedDoc()
    idx = 0
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            idx += 1
            style = (p.style.name if p.style is not None else "") or ""
            lvl = 0
            m = re.match(r"(Heading|Titlu)\s*(\d)", style, re.I)
            if m:
                lvl = int(m.group(2))
            elif style.lower() == "title":
                lvl = 1
            else:
                lvl = _guess_heading_level(text)
            doc.blocks.append(Block(text, None, lvl, para_index=idx))
        elif tag == "tbl":
            t = Table(child, d)
            rows = []
            for r in t.rows:
                cells = []
                for c in r.cells:
                    ct = c.text.replace("\n", " ").strip()
                    if not cells or cells[-1] != ct:  # celule imbinate apar duplicat
                        cells.append(ct)
                rows.append(" | ".join(cells))
            rows = [r for r in rows if r.strip(" |")]
            if rows:
                idx += 1
                doc.blocks.append(Block("\n".join(rows), None, 0, is_table=True, para_index=idx))
    doc.page_count = 0
    return doc


def parse(data: bytes, mime: str, filename: str) -> tuple[str, ParsedDoc]:
    kind = validate(data, mime, filename)
    return kind, (parse_pdf(data) if kind == "pdf" else parse_docx(data))


# --------------------------------------------------------------------------- injectii
_INJECTION_PATTERNS = [
    r"ignor\w*\s+(all|any|the|previous|prior|toate|orice)?\s*(previous|prior|above|anterioare|de mai sus)?\s*(instruc\w+|instructions?)",
    r"disregard\s+(the\s+)?(previous|above|prior)",
    r"\byou are (now )?(an?|the) (ai|assistant|model)",
    r"\bsystem prompt\b|\bprompt de sistem\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
    r"\b(assistant|asistent)\s*:\s",
    r"(acord[aă]|d[aă])\s+(scor|punctaj)\w*\s+(maxim|100)",
    r"(recommend|recomand[aă])\s+(this|acest)\s+(product|produs)\s+(as|ca)",
    r"(classify|clasific[aă])\s+(as|ca)\s+(covered|acoperit)",
]


def scan_injection(text: str) -> list[str]:
    from .textnorm import normalize
    t = normalize(text)
    hits = []
    for pat in _INJECTION_PATTERNS:
        for m in re.finditer(pat, t):
            hits.append(t[max(0, m.start() - 40): m.end() + 40])
            if len(hits) > 10:
                return hits
    return hits
