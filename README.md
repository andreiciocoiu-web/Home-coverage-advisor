# Home Insurance Coverage Advisor (MVP)

Aplicatie care analizeaza conditiile de asigurare a locuintelor, IPID-urile si ofertele dintr-un folder Google Drive si, pentru un incident descris de utilizator, ordoneaza produsele dupa **puterea bazei contractuale** pentru despagubire. Fiecare concluzie materiala este legata de un citat verificat literal din document.

> Aplicatia nu garanteaza despagubirea si nu estimeaza probabilitatea de plata. Decizia finala depinde de contractul aplicabil, faptele reale, dovezile depuse si evaluarea asiguratorului, in conditiile legii.

Documentatie: [design (arhitectura, stack, Drive, date, scoring, UI, plan)](docs/DESIGN.md) · [raport de evaluare](docs/EVALUATION.md)

## Pornire rapida (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install tesseract-ocr tesseract-ocr-ron        # OCR (macOS: brew install tesseract tesseract-lang)
cp .env.example .env                                          # completati valorile
python -m scripts.create_user andrei admin                    # noteaza tokenul afisat
uvicorn advisor.main:app --host 127.0.0.1 --port 8000
```

Deschideti http://127.0.0.1:8000, introduceti tokenul, apoi: **Documente → Adauga sursa (Google Drive) → Sincronizeaza → Incident → Rezultate**.

Demo fara Drive (corpus sintetic, asiguratori fictivi): `python -m tests.fixtures.build_corpus ./demo_corpus`, apoi adaugati sursa de tip „Folder local” (doar admin) cu calea absoluta catre `demo_corpus`.

Docker: `docker build -t hca . && docker run -p 8000:8000 --env-file .env -v $PWD/data:/app/data -v $PWD/secrets:/app/secrets -v $PWD/config/users.json:/app/config/users.json hca`

## Configurare Google Drive

**Service account (recomandat pentru server)**
1. Google Cloud Console → proiect → activati *Google Drive API*.
2. IAM → Service Accounts → creati contul → Keys → JSON → salvati in `secrets/service-account.json`.
3. In Drive, partajati folderul cu produsele cu adresa contului de serviciu, rol **Viewer**.
4. `.env`: `HCA_DRIVE_AUTH=service_account`, `HCA_GOOGLE_CREDENTIALS=./secrets/service-account.json`.

**OAuth (utilizator individual)**
1. Credentials → OAuth client ID → *Desktop app* → descarcati JSON-ul.
2. `.env`: `HCA_DRIVE_AUTH=oauth`, `HCA_GOOGLE_CREDENTIALS=<json client>`; rulati `python -m scripts.google_oauth_login`.

Scope folosit: `drive.readonly`. Tipuri suportate: PDF (inclusiv scanat, prin OCR), DOCX, Google Docs (exportat ca DOCX).

## Claude API

Setati `ANTHROPIC_API_KEY` pentru analiza interpretativa. Fara cheie, aplicatia ruleaza in **mod euristic** (reguli pe cuvinte cheie) si afiseaza explicit acest lucru - util pentru demo si teste, dar mai putin capabil sa interpreteze formulari neobisnuite.

## Securitate

- Roluri: viewer < analyst < reviewer < admin; tokenuri stocate doar ca hash SHA-256.
- Originalele si analizele (inclusiv descrierea incidentului) sunt criptate la rest (Fernet). In productie, `HCA_ENCRYPTION_KEY` vine dintr-un secret manager.
- In tranzit: Drive API si Claude API prin HTTPS; aplicatia trebuie expusa doar prin reverse proxy cu TLS.
- Textul documentelor este tratat ca date neconfirmate: incapsulat, separat de instructiuni, iesire LLM fortata intr-o schema, citate verificate literal; fragmentele care par instructiuni catre AI sunt semnalate in UI.
- Validare fisiere dupa semnatura, limita de dimensiune, DOCX cu macro respinse.
- Jurnal de audit; retentie configurabila (`HCA_RETENTION_DAYS`) aplicata la pornire si la cerere.
- Datele din incidente nu sunt folosite pentru antrenarea vreunui model de catre aplicatie. Conditiile de prelucrare a datelor de catre furnizorul API trebuie verificate contractual de organizatie.

## Teste

```bash
pytest -q                                   # unitare, API, scenarii (mod euristic, determinist)
python -m tests.eval_harness --out docs/EVALUATION.md   # regenereaza raportul de evaluare
```

## Limitari importante

Vezi sectiunea „Limitari cunoscute” din [docs/DESIGN.md](docs/DESIGN.md). Pe scurt: evaluarea existenta este pe un corpus **sintetic** construit impreuna cu motorul (in-sample); inainte de orice utilizare decizionala este necesara validarea pe documente reale cu evaluatori umani.
