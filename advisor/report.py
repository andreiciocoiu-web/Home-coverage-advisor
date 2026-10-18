"""Export raport comparativ: HTML autonom, PDF (reportlab) si JSON."""
from __future__ import annotations

import html
import io
import json

from .analysis import NS


def _e(x) -> str:
    return html.escape(str(x if x is not None else NS))


def _ev_html(e: dict) -> str:
    return (f"<div class='ev'><div class='meta'>{_e(e.get('insurer'))} · {_e(e.get('document'))} ({_e(e.get('doc_type'))}, "
            f"in vigoare: {_e(e.get('effective_date'))}) · Clauza {_e(e.get('clause_number'))} · {_e(e.get('location'))}</div>"
            f"<blockquote>„{_e(e.get('quote'))}”</blockquote>"
            f"<div class='interp'><b>Interpretarea aplicatiei:</b> {_e(e.get('interpretation'))}</div>"
            + (f"<div class='interp'><b>Exceptie:</b> „{_e(e.get('exception_quote'))}”</div>" if e.get("exception_quote") else "")
            + f"<div class='ver {'ok' if e.get('verified') else 'ko'}'>{'Citat verificat literal' if e.get('verified') else 'NEVERIFICAT: ' + _e(e.get('verification_note'))}</div></div>")


def render_html(r: dict, incident_text: str) -> str:
    rows = "".join(
        f"<tr><td>{t['rank']}</td><td>{_e(t['insurer'])}</td><td>{_e(t['product'])}</td><td>{_e(t['outcome'])}</td>"
        f"<td>{t['score']}/100</td><td>{_e(t['evidence_confidence'])}</td><td>{_e(t['main_clause'])}</td>"
        f"<td>{_e(t['main_exclusion'])}</td><td>{_e(t['deductible_or_sublimit'])}</td>"
        f"<td>{'<br>'.join(_e(m) for m in t['missing_info']) or '-'}</td><td>{_e(t['recommendation'])}</td></tr>"
        for t in (p["table"] for p in r["products"]))
    facts = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(v.get('value') if not isinstance(v.get('value'), list) else ', '.join(map(str, v['value'])))}</td>"
        f"<td>{ {'provided': 'Furnizat de utilizator', 'inferred': 'Dedus (nu este fapt stabilit)', 'unknown': 'Necunoscut'}.get(v.get('status'), v.get('status'))}</td>"
        f"<td>{_e(v.get('evidence') or '-')}</td></tr>" for k, v in r["facts"].items())
    details = ""
    for p in r["products"][:3]:
        d, sc = p["details"], p["score"]
        dims = "".join(f"<tr><td>{_e(r['dimension_labels'][k])}</td><td>{_e(v['level'])}</td><td>{v['score']}</td></tr>"
                       for k, v in sc["dimensions"].items())
        details += (f"<h3>{p['rank']}. {_e(p['product_label'])} — {_e(sc['outcome_label'])} ({sc['overall']}/100)</h3>"
                    f"<p><b>Incredere in dovezi:</b> {_e(sc['evidence_confidence'])} · <b>Incertitudinea rezultatului:</b> {_e(sc['uncertainty'])}</p>"
                    f"<p><b>Motive:</b> {' '.join(_e(x) for x in sc['outcome_reasons'])} {' '.join(_e(x) for x in sc['caps'])}</p>"
                    f"<table><tr><th>Dimensiune</th><th>Nivel</th><th>Scor</th></tr>{dims}</table>"
                    "<h4>1-2. Clauze care sustin acoperirea</h4>" + ("".join(_ev_html(e) for e in d["supporting_clauses"]) or f"<p>{NS}</p>")
                    + "<h4>3. Componente posibil despagubibile / fara acoperire identificata</h4>"
                    f"<p>Posibil despagubibile: {_e('; '.join(d['covered_components']) or '-')}<br>Fara acoperire identificata: {_e('; '.join(d['uncovered_components']) or '-')}</p>"
                    "<p>" + "<br>".join(f"{_e(c['component'])}: {_e(c['coverage_level'])} ({'raspundere civila fata de terti' if c['category']=='third_party' else 'dauna la bunurile asigurate'})" for c in d["property_vs_liability"]) + "</p>"
                    "<h4>4. Excluderi care ar putea fi aplicabile</h4>" + ("".join(_ev_html(e) for e in d["exclusions"]) or "<p>Nicio excludere relevanta identificata in documentele analizate.</p>")
                    + "<h4>5. Fapte necunoscute</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in d["unknown_facts"]) + "</ul>"
                    "<h4>6. Limite si fransize</h4>" + ("".join(_ev_html(e) for e in d["limits"]) or f"<p>{NS}</p>")
                    + "<h4>7. Ce ar putea duce la respingerea sau reducerea daunei</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in d["reject_reasons"]) + "</ul>"
                    "<h4>8. Documente / dovezi de obtinut</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in d["documents_to_obtain"]) + "</ul>"
                    "<h4>9. De clarificat cu asiguratorul inainte de cumparare</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in d["clarify_before_purchase"]) + "</ul>"
                    + ("<h4>Conflicte intre documente</h4>" + "".join(f"<p>{_e(c['explanation'])}</p>" + _ev_html(c['evidence']) + (_ev_html(c['evidence_b']) if c.get('evidence_b') else '') for c in d["conflicts"]) if d["conflicts"] else "")
                    + ("<h4>Limitari privind versiunile documentelor</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in p["version_limitations"]) + "</ul>" if p["version_limitations"] else "")
                    + ("<h4>Constatari NEVERIFICATE (nu sustin scorul)</h4>" + "".join(_ev_html(e) for e in d["unverified"]) if d["unverified"] else ""))
    return f"""<!doctype html><html lang="ro"><head><meta charset="utf-8"><title>Raport acoperire {r['id']}</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1b1f24;font-size:13px}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}td,th{{border:1px solid #cfd6de;padding:5px;vertical-align:top;text-align:left}}
th{{background:#eef2f6}}blockquote{{margin:4px 0;padding:6px 10px;background:#f6f8fa;border-left:3px solid #4a6fa5}}
.ev{{border:1px solid #e1e6eb;padding:8px;margin:6px 0;border-radius:4px}}.meta{{color:#555;font-size:12px}}
.ver.ok{{color:#1a7f37;font-size:12px}}.ver.ko{{color:#b42318;font-weight:bold;font-size:12px}}
.disc{{background:#fff8e6;border:1px solid #f0c36d;padding:10px;border-radius:4px}}</style></head><body>
<h1>Home Insurance Coverage Advisor — raport comparativ</h1>
<p>Analiza {r['id']} · {r['created_at']} · mod: {_e(r['mode'])} · cautare: {_e(r['retrieval_backend'])} · metodologie v{r['methodology_version']}</p>
<div class="disc">{_e(r['disclaimer'])}</div>
<h2>Incidentul descris</h2><p>{_e(incident_text)}</p>
<h2>Fapte</h2><table><tr><th>Fapt</th><th>Valoare</th><th>Statut</th><th>Sursa / rationament</th></tr>{facts}</table>
<h2>Concluzie</h2><p><b>{_e(r['verdict']['text'])}</b></p>
<h2>Clasament</h2><table><tr><th>Loc</th><th>Asigurator</th><th>Produs</th><th>Rezultat</th><th>Scor sustinere contractuala</th>
<th>Incredere dovezi</th><th>Clauza principala</th><th>Excludere/limitare principala</th><th>Fransiza/sublimita</th>
<th>Informatii lipsa</th><th>Recomandare</th></tr>{rows}</table>
<p><i>{_e(r['radar']['note'])}</i></p>
<h2>Analiza detaliata (primele 3 produse)</h2>{details}
<h2>Ponderi metodologie</h2><p>{_e(json.dumps(r['weights'], ensure_ascii=False))}</p>
</body></html>"""


def render_pdf(r: dict, incident_text: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .textnorm import strip_diacritics

    def P(t, st="BodyText"):
        # fonturile standard PDF nu au diacritice romanesti; textul este exportat fara diacritice
        return Paragraph(html.escape(strip_diacritics(str(t if t is not None else NS))), styles[st])

    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize = 8
    styles["BodyText"].leading = 10
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    el = [P("Home Insurance Coverage Advisor - raport comparativ", "Title"),
          P(f"Analiza {r['id']} | {r['created_at']} | mod: {r['mode']} | metodologie v{r['methodology_version']}"),
          Spacer(1, 6), P(r["disclaimer"]), Spacer(1, 6), P("Incident: " + incident_text), Spacer(1, 6),
          P("Concluzie: " + r["verdict"]["text"], "Heading4")]
    head = ["Loc", "Asigurator", "Produs", "Rezultat", "Scor", "Incredere", "Clauza principala", "Excludere/limitare",
            "Fransiza/sublimita", "Informatii lipsa", "Recomandare"]
    data = [[P(h) for h in head]]
    for p in r["products"]:
        t = p["table"]
        data.append([P(t["rank"]), P(t["insurer"]), P(t["product"]), P(t["outcome"]), P(f"{t['score']}/100"),
                     P(t["evidence_confidence"]), P(t["main_clause"]), P(t["main_exclusion"]), P(t["deductible_or_sublimit"]),
                     P("; ".join(t["missing_info"]) or "-"), P(t["recommendation"])])
    tb = Table(data, repeatRows=1, colWidths=[24, 70, 70, 70, 40, 50, 80, 120, 70, 110, 70])
    tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [Spacer(1, 6), tb, Spacer(1, 8), P(r["radar"]["note"])]
    for p in r["products"][:3]:
        d, sc = p["details"], p["score"]
        el += [Spacer(1, 10), P(f"{p['rank']}. {p['product_label']} - {sc['outcome_label']} ({sc['overall']}/100)", "Heading3"),
               P("Motive: " + " ".join(sc["outcome_reasons"] + sc["caps"]))]
        for title, evs in (("Clauze de acoperire", d["supporting_clauses"]), ("Excluderi", d["exclusions"]), ("Limite/fransize", d["limits"])):
            el.append(P(title, "Heading4"))
            for e in evs or []:
                el.append(P(f"[{e.get('document')} | clauza {e.get('clause_number')} | {e.get('location')}] \"{e.get('quote')}\" - {e.get('interpretation')}"
                            + ("" if e.get("verified") else " [NEVERIFICAT]")))
            if not evs:
                el.append(P(NS))
        el.append(P("Fapte necunoscute: " + "; ".join(d["unknown_facts"] or ["-"])))
        el.append(P("De clarificat: " + "; ".join(d["clarify_before_purchase"] or ["-"])))
    doc.build(el)
    return buf.getvalue()
