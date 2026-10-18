"""Harness de evaluare pe scenariile din tests/scenarios.json.

Ruleaza: python -m tests.eval_harness [--out docs/EVALUATION.md]
Foloseste modul LLM daca ANTHROPIC_API_KEY este setat, altfel modul euristic.
Masoara: acuratetea regasirii, a citarii clauzelor, a clasificarii, a detectiei
excluderilor, calitatea ranking-ului si frecventa concluziilor nesustinute.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup_env(workdir: Path) -> Path:
    os.environ["HCA_DATA_DIR"] = str(workdir / "data")
    os.environ.setdefault("HCA_OCR_LANGS", "ron+eng")
    from advisor import config, db, security
    config.settings.data_dir = Path(os.environ["HCA_DATA_DIR"])
    config.settings.ocr_langs = os.environ["HCA_OCR_LANGS"]
    db.reset_connection()
    security._fernet = None
    from tests.fixtures.build_corpus import build
    return build(str(workdir / "corpus"))


def run(out: str | None = None, workdir: str | None = None) -> dict:
    wd = Path(workdir or tempfile.mkdtemp(prefix="hca_eval_"))
    corpus = setup_env(wd)
    from advisor import analysis, facts, ingest, versions
    from advisor.retrieval import index
    src = ingest.register_source("local", str(corpus))
    sync = ingest.sync_source(src["id"])
    spec = json.loads((ROOT / "tests" / "scenarios.json").read_text(encoding="utf-8"))
    pmap = spec["products"]
    m = {k: [0, 0] for k in ("classification", "clause_citation", "exclusion_detection", "retrieval", "ranking", "other_checks")}
    unsupported = [0, 0]
    rows = []
    for sc in spec["scenarios"]:
        ex = facts.extract(sc["text"], use_llm=bool(os.environ.get("ANTHROPIC_API_KEY")))
        res = analysis.analyze(sc["text"], ex, sc.get("answers", {}), None)
        by_key = {p["product_key"]: p for p in res["products"]}
        errors, checks = [], []
        for alias, exp in sc["expect"].items():
            p = by_key.get(pmap[alias])
            if p is None:
                if "outcome" in exp and set(exp["outcome"]) <= {"INSUFFICIENT_INFORMATION", "LIKELY_EXCLUDED"}:
                    m["classification"][0] += 1
                    m["classification"][1] += 1
                    checks.append(f"{alias}: neanalizat (fara clauze relevante) - acceptat")
                else:
                    m["classification"][1] += 1
                    errors.append(f"{alias}: produs absent din rezultate")
                continue
            a = p["assessment"]
            unsupported[0] += a["n_unverified"]
            unsupported[1] += a["n_verified"] + a["n_unverified"]
            if "outcome" in exp:
                m["classification"][1] += 1
                if p["score"]["outcome"] in exp["outcome"]:
                    m["classification"][0] += 1
                else:
                    errors.append(f"{alias}: rezultat {p['score']['outcome']}, asteptat {'/'.join(exp['outcome'])}")
            comps = [c for c in a["components"] if c["category"] == exp.get("component")] if exp.get("component") else a["components"]
            if "coverage_clause" in exp:
                m["clause_citation"][1] += 1
                cited = {e.get("clause_number") for c in comps for e in c["coverage_evidence"]}
                if exp["coverage_clause"] in cited:
                    m["clause_citation"][0] += 1
                else:
                    errors.append(f"{alias}: clauza de acoperire asteptata {exp['coverage_clause']}, citate {sorted(map(str, cited)) or '-'}")
                # regasire: clauza asteptata se afla printre candidatii produsului
                m["retrieval"][1] += 1
                docs = versions.products()[pmap[alias]]
                appl, _ = versions.resolve_applicable(docs, res["reference_date"])
                cands = analysis.gather_candidates(appl, res["perils"], res["loss_components"])
                if any(c.get("number") == exp["coverage_clause"] for c in cands.values()):
                    m["retrieval"][0] += 1
                else:
                    errors.append(f"{alias}: clauza {exp['coverage_clause']} nu a fost regasita")
            if "exclusion_clause" in exp:
                m["exclusion_detection"][1] += 1
                found = [x for c in a["components"] for x in c["exclusions"] if x["verified"]
                         and str(x["evidence"].get("clause_number", "")).replace("Art. ", "").split(" ")[0] == exp["exclusion_clause"]
                         and x["applies"] in ("clearly", "likely", "possible", "possible_material")]
                if found:
                    m["exclusion_detection"][0] += 1
                else:
                    errors.append(f"{alias}: excluderea {exp['exclusion_clause']} nu a fost detectata")
            for key, fn in (("max_score", lambda: p["score"]["overall"] <= exp["max_score"]),
                            ("conflict", lambda: bool(a["conflicts"]) == exp["conflict"]),
                            ("exception_restores", lambda: any(x["applies"] == "exception_restores" for c in a["components"] for x in c["exclusions"])),
                            ("applicable_doc", lambda: exp["applicable_doc"] in [d["filename"] for d in p["documents"]]),
                            ("limitation_contains", lambda: any(exp["limitation_contains"] in l for l in p["version_limitations"]))):
                if key in exp:
                    m["other_checks"][1] += 1
                    if fn():
                        m["other_checks"][0] += 1
                    else:
                        errors.append(f"{alias}: verificarea '{key}' a esuat")
        if "expect_top" in sc:
            m["ranking"][1] += 1
            top = res["products"][0]["product_key"] if res["products"] else None
            if top in [pmap[a] for a in sc["expect_top"]]:
                m["ranking"][0] += 1
            else:
                errors.append(f"ranking: primul clasat {top}, asteptat {sc['expect_top']}")
        if "expect_verdict" in sc:
            m["ranking"][1] += 1
            if res["verdict"]["type"] == sc["expect_verdict"]:
                m["ranking"][0] += 1
            else:
                errors.append(f"verdict {res['verdict']['type']}, asteptat {sc['expect_verdict']}")
        rows.append({"id": sc["id"], "title": sc["title"], "text": sc["text"], "answers": sc.get("answers", {}),
                     "expected": sc["expect"], "verdict": res["verdict"]["text"],
                     "actual": [{"product": p["product_label"], "outcome": p["score"]["outcome"], "score": p["score"]["overall"],
                                 "coverage": sorted({str(e.get("clause_number")) for c in p["assessment"]["components"] for e in c["coverage_evidence"]}),
                                 "exclusions": sorted({f"{x['evidence'].get('clause_number')}:{x['applies']}" for c in p["assessment"]["components"] for x in c["exclusions"] if x["verified"]})}
                                for p in res["products"]],
                     "errors": errors, "mode": res["mode"]})
    metrics = {k: {"ok": v[0], "total": v[1], "rate": round(v[0] / v[1], 3) if v[1] else None} for k, v in m.items()}
    metrics["unsupported_conclusions"] = {"unverified": unsupported[0], "findings": unsupported[1],
                                          "rate": round(unsupported[0] / unsupported[1], 3) if unsupported[1] else 0}
    report = {"mode": rows[0]["mode"] if rows else None, "retrieval_backend": index.backend, "sync": sync,
              "metrics": metrics, "scenarios": rows}
    if out:
        Path(out).write_text(to_markdown(report), encoding="utf-8")
    if not workdir:
        shutil.rmtree(wd, ignore_errors=True)
    return report


def to_markdown(r: dict) -> str:
    L = ["# Raport de evaluare (generat automat)", "",
         f"Mod analiza: **{r['mode']}** · regasire: **{r['retrieval_backend']}** · corpus: SINTETIC (asiguratori fictivi).", "",
         "> Rezultatele masoara comportamentul motorului pe un corpus de test construit special. Nu sunt o validare pe "
         "documente reale de piata si nu sunt o calibrare statistica a probabilitatii de plata.", "",
         "> **Evaluare in-sample:** scenariile au fost scrise in paralel cu motorul euristic, iar motorul a fost corectat pana "
         "la trecerea lor (ex. cuvinte cheie prea largi - „furt” in „furtuna”, „in timp” in „in timpul”). Ratele de mai jos "
         "sunt deci o verificare de regresie, nu o estimare a acuratetei pe documente si incidente noi.", "",
         "## Metrici", "", "| Metrica | Corecte | Total | Rata |", "|---|---|---|---|"]
    names = {"classification": "Clasificarea acoperirii", "clause_citation": "Citarea clauzei de acoperire",
             "exclusion_detection": "Detectia excluderilor", "retrieval": "Regasire (clauza asteptata printre candidati)",
             "ranking": "Ranking / verdict", "other_checks": "Verificari specifice (versiuni, conflicte, exceptii, injectie)"}
    for k, n in names.items():
        v = r["metrics"][k]
        L.append(f"| {n} | {v['ok']} | {v['total']} | {v['rate'] if v['rate'] is not None else '-'} |")
    u = r["metrics"]["unsupported_conclusions"]
    L += [f"| Concluzii nesustinute (constatari neverificate) | {u['unverified']} | {u['findings']} | {u['rate']} |", "",
          "## Scenarii", ""]
    for s in r["scenarios"]:
        L += [f"### {s['id']} - {s['title']}", "", f"**Intrare:** {s['text']}",
              f"**Raspunsuri:** {json.dumps(s['answers'], ensure_ascii=False) if s['answers'] else '-'}", "",
              "**Asteptat:** `" + json.dumps(s["expected"], ensure_ascii=False) + "`", "",
              f"**Verdict:** {s['verdict']}", "", "| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |", "|---|---|---|---|---|"]
        for a in s["actual"]:
            L.append(f"| {a['product']} | {a['outcome']} | {a['score']} | {', '.join(a['coverage']) or '-'} | {', '.join(a['exclusions']) or '-'} |")
        L += ["", "**Erori identificate:** " + ("; ".join(s["errors"]) if s["errors"] else "niciuna"), ""]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "EVALUATION.md"))
    args = ap.parse_args()
    rep = run(args.out)
    print(json.dumps(rep["metrics"], indent=2, ensure_ascii=False))
    for s in rep["scenarios"]:
        if s["errors"]:
            print(s["id"], s["errors"])
