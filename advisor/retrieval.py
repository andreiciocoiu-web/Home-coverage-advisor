"""Cautare hibrida la nivel de clauza: BM25 (lexical) + vectorial + cautare exacta
dupa numarul clauzei, combinate prin Reciprocal Rank Fusion.

Componenta vectoriala:
- `st`: sentence-transformers multilingv (daca pachetul si modelul sunt disponibile);
- `tfidf`: TF-IDF pe n-grame de caractere (fallback fara dependinte grele).
Backend-ul folosit efectiv este raportat in fiecare analiza (transparenta).
"""
from __future__ import annotations

import re
import threading

import numpy as np
from rank_bm25 import BM25Okapi

from . import db
from .config import settings
from .textnorm import normalize, tokenize

RRF_K = 60


class HybridIndex:
    def __init__(self):
        self._lock = threading.RLock()
        self._built = False
        self.backend = "neconstruit"

    def invalidate(self):
        with self._lock:
            self._built = False

    def _build(self):
        rows = db.q("""SELECT c.*, d.insurer, d.product, d.doc_type, d.hierarchy_level, d.filename,
                              d.effective_date, d.version_label, d.ocr_pages
                       FROM clauses c JOIN documents d ON d.id=c.document_id
                       WHERE d.status='ok' AND d.deleted_at IS NULL""")
        self.rows = rows
        self.by_id = {r["id"]: i for i, r in enumerate(rows)}
        corpus = [tokenize(f"{r['title']} {r['section_path']} {r['text']}") for r in rows]
        self.bm25 = BM25Okapi(corpus) if rows else None
        self.dense = None
        texts = [normalize(f"{r['title']} {r['text']}") for r in rows]
        backend = settings.embedding_backend
        if rows and backend in ("auto", "st"):
            try:
                from sentence_transformers import SentenceTransformer  # optional
                self._st = SentenceTransformer(settings.st_model)
                self.dense = self._st.encode(texts, normalize_embeddings=True, show_progress_bar=False)
                self.backend = f"sentence-transformers:{settings.st_model}"
            except Exception:
                if backend == "st":
                    raise
        if rows and self.dense is None:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True)
            self.dense = self._tfidf.fit_transform(texts)
            self.backend = "tfidf-char-ngrams"
        self._built = True

    def ensure(self):
        with self._lock:
            if not self._built:
                self._build()

    def _dense_scores(self, query: str) -> np.ndarray:
        qn = normalize(query)
        if self.backend.startswith("sentence-transformers"):
            qv = self._st.encode([qn], normalize_embeddings=True, show_progress_bar=False)[0]
            return self.dense @ qv
        qv = self._tfidf.transform([qn])
        return (self.dense @ qv.T).toarray().ravel()

    def search(self, query: str, doc_ids: set[str] | None = None, kinds: set[str] | None = None,
               top_k: int = 8, boosts: dict[str, float] | None = None) -> list[dict]:
        self.ensure()
        if not self.rows:
            return []
        mask = np.array([(doc_ids is None or r["document_id"] in doc_ids) and (kinds is None or r["kind"] in kinds)
                         for r in self.rows])
        if not mask.any():
            return []
        bm = np.asarray(self.bm25.get_scores(tokenize(query)))
        dn = self._dense_scores(query)
        ranks = {}
        for name, scores in (("bm25", bm), ("dense", dn)):
            s = np.where(mask, scores, -np.inf)
            order = np.argsort(-s)
            r = 0
            for idx in order:
                if not mask[idx] or s[idx] <= 0:
                    break
                ranks.setdefault(idx, {})[name] = r
                r += 1
                if r > 200:
                    break
        # cautare exacta dupa numar de articol mentionat in interogare
        for num in re.findall(r"art(?:icolul|\.)?\s*(\d+(?:\.\d+)*)", normalize(query)):
            for i, row in enumerate(self.rows):
                if mask[i] and row["number"] == num:
                    ranks.setdefault(i, {})["exact"] = 0
        fused = []
        for idx, rr in ranks.items():
            score = sum(1.0 / (RRF_K + v) for v in rr.values())
            cid = self.rows[idx]["id"]
            if boosts and cid in boosts:
                score *= boosts[cid]
            fused.append((score, idx, rr))
        fused.sort(key=lambda t: -t[0])
        out = []
        for score, idx, rr in fused[:top_k]:
            row = dict(self.rows[idx])
            row["retrieval"] = {"score": round(score, 5), "ranks": rr}
            out.append(row)
        return out

    def get(self, clause_id: str) -> dict | None:
        self.ensure()
        i = self.by_id.get(clause_id)
        return dict(self.rows[i]) if i is not None else None


index = HybridIndex()


def feedback_boosts() -> dict[str, float]:
    """Boost-uri din feedback VALIDAT de reviewer (relevanta clauzelor).

    Un singur marcaj nu devine regula: efectul este limitat (x1.3 / x0.6) si se
    aplica doar la ordonarea rezultatelor, nu la concluzia de acoperire.
    """
    out: dict[str, float] = {}
    for r in db.q("SELECT clause_id, content_json FROM feedback WHERE kind='clause_relevance' AND status='validated'"):
        import json
        c = json.loads(r["content_json"] or "{}")
        out[r["clause_id"]] = 1.3 if c.get("relevant") else 0.6
    return out
