"""Normalizare text romanesc pentru cautare si verificarea citatelor.

Normalizarea este folosita DOAR pentru comparatie si indexare. Textul original
al documentelor nu este niciodata modificat; citatele afisate provin din textul
original, nu din forma normalizata.
"""
from __future__ import annotations

import re
import unicodedata

_DIACRITICS = str.maketrans({
    "ă": "a", "â": "a", "î": "i", "ș": "s", "ş": "s", "ț": "t", "ţ": "t",
    "Ă": "A", "Â": "A", "Î": "I", "Ș": "S", "Ş": "S", "Ț": "T", "Ţ": "T",
})

_QUOTES = str.maketrans({"„": '"', "”": '"', "“": '"', "«": '"', "»": '"', "’": "'", "‘": "'",
                         "–": "-", "—": "-", " ": " ", "­": ""})

# Cuvinte de legatura frecvente, eliminate din indexul lexical
STOPWORDS = set("""
a ai al ale am ar are as asa asupra au avea care ce cel cea cei cele cu cum
da dar de deci din dintre dupa e ea ei el este eu fi fie fost iar in inca
intre isi la le li lor lui mai mi ne nici nu o or ori pe pentru prin sa sau
se si sunt sub ta te un una unei unor unui va vor fata catre acest aceasta
acestei acestui acestor fiind poate pot daca decat cand unde
""".split())


def strip_diacritics(text: str) -> str:
    text = text.translate(_DIACRITICS)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Forma canonica pentru comparatie: fara diacritice, lowercase, spatii unice."""
    text = (text or "").translate(_QUOTES)
    text = strip_diacritics(text).lower()
    text = re.sub(r"-\s*\n\s*", "", text)  # despartire in silabe la capat de rand
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def light_stem(token: str) -> str:
    """Stemming usor pentru romana (trunchiere de sufixe flexionare frecvente).

    Nu este un stemmer lingvistic complet; scopul este doar cresterea recall-ului
    lexical (ex. "inundatie" / "inundatii" / "inundatiei").
    """
    for suf in ("urilor", "ilor", "elor", "ului", "iile", "ilor", "iei", "ele", "ile",
                "ul", "ii", "ei", "le", "ea", "ia", "a", "e", "i"):
        if len(token) - len(suf) >= 4 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def tokenize(text: str, stem: bool = True) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", normalize(text))
    out = [t for t in toks if t not in STOPWORDS and len(t) > 1]
    return [light_stem(t) for t in out] if stem else out


def contains_quote(haystack: str, quote: str) -> bool:
    """Verifica daca un citat apare literal (modulo spatii/diacritice/ghilimele)."""
    q = normalize(quote).strip(' ."\'')
    if len(q) < 8:
        return False
    return q in normalize(haystack)
