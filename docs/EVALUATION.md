# Raport de evaluare (generat automat)

Mod analiza: **heuristic** · regasire: **tfidf-char-ngrams** · corpus: SINTETIC (asiguratori fictivi).

> Rezultatele masoara comportamentul motorului pe un corpus de test construit special. Nu sunt o validare pe documente reale de piata si nu sunt o calibrare statistica a probabilitatii de plata.

> **Evaluare in-sample:** scenariile au fost scrise in paralel cu motorul euristic, iar motorul a fost corectat pana la trecerea lor (ex. cuvinte cheie prea largi - „furt” in „furtuna”, „in timp” in „in timpul”). Ratele de mai jos sunt deci o verificare de regresie, nu o estimare a acuratetei pe documente si incidente noi.

## Metrici

| Metrica | Corecte | Total | Rata |
|---|---|---|---|
| Clasificarea acoperirii | 52 | 52 | 1.0 |
| Citarea clauzei de acoperire | 23 | 23 | 1.0 |
| Detectia excluderilor | 9 | 9 | 1.0 |
| Regasire (clauza asteptata printre candidati) | 23 | 23 | 1.0 |
| Ranking / verdict | 2 | 2 | 1.0 |
| Verificari specifice (versiuni, conflicte, exceptii, injectie) | 6 | 6 | 1.0 |
| Concluzii nesustinute (constatari neverificate) | 0 | 161 | 0.0 |

## Scenarii

### S01 - Teava sparta brusc, locuinta ocupata (dauna proprie)

**Intrare:** In apartamentul nostru o teava de apa din bucatarie s-a spart brusc ieri si a udat parchetul. Locuim permanent in apartament.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["POTENTIALLY_COVERED"], "coverage_clause": "5.2", "exclusion_clause": "7", "uncertainty_note": "uzura/intretinere nestabilita"}, "beta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "3.2"}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION", "LIKELY_EXCLUDED"]}, "delta": {"outcome": ["LIKELY_COVERED"], "coverage_clause": "2"}}`

**Verdict:** Produsele Asigurator Beta (fictiv) - Casa Sigura, Asigurator Delta (fictiv) - Total Protect sunt practic echivalente pentru acest incident (diferenta de scor <= 5 puncte, acelasi rezultat). Diferentiati-le dupa limitele, fransizele si conditiile din detalii.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_COVERED | 95 | 3.2 | - |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 90 | 2 | 3:exception_restores |
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 76 | 5.2 | Art. 7 lit. b):possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S02 - Scurgere lenta, pete de umezeala (dauna treptata)

**Intrare:** De cateva luni au aparut pete de umezeala pe peretele din baie din cauza unei scurgeri lente la teava de apa. Locuim in casa.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["LIKELY_EXCLUDED"], "coverage_clause": "5.2", "exclusion_clause": "7"}, "delta": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "3"}, "beta": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "3.2"}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION", "LIKELY_EXCLUDED"]}}`

**Verdict:** Asigurator Beta (fictiv) - Casa Sigura are cea mai puternica baza contractuala pentru incidentul descris (scor 95/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_COVERED | 95 | 3.2 | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | LIKELY_EXCLUDED | 30 | 5.2 | Art. 7 lit. a):clearly, Art. 7 lit. b):possible |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_EXCLUDED | 30 | 2 | 3:clearly |

**Erori identificate:** niciuna

### S03 - Infiltratie prin acoperis dupa furtuna cu grindina

**Intrare:** In timpul unei furtuni cu grindina cateva tigle au fost smulse si apa de ploaie a patruns prin acoperis, deteriorand tavanul din dormitor.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "5.3"}, "gama": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "2"}, "delta": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "2"}, "beta": {"outcome": ["INSUFFICIENT_INFORMATION", "LIKELY_EXCLUDED"]}}`

**Verdict:** Produsele Asigurator Alfa (fictiv) - Locuinta Plus, Asigurator Gama (fictiv) - Basic Home sunt practic echivalente pentru acest incident (diferenta de scor <= 5 puncte, acelasi rezultat). Diferentiati-le dupa limitele, fransizele si conditiile din detalii.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 77 | 5.3 | Art. 7 lit. a):possible, Art. 7 lit. b):possible, Art. 7 lit. c):possible |
| Asigurator Gama (fictiv) - Basic Home | POTENTIALLY_COVERED | 74 | 2 | 5:possible |
| Asigurator Delta (fictiv) - Total Protect | POTENTIALLY_COVERED | 65 | 2 | 3:possible |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S04 - Inundatie din revarsarea unui rau (IPID vs conditii)

**Intrare:** Raul din apropiere s-a revarsat dupa ploi torentiale si a inundat parterul casei, deteriorand mobila si peretii.
**Raspunsuri:** -

**Asteptat:** `{"beta": {"outcome": ["POTENTIALLY_COVERED", "INSUFFICIENT_INFORMATION"], "max_score": 60, "conflict": true}, "alfa": {"outcome": ["INSUFFICIENT_INFORMATION", "LIKELY_EXCLUDED"]}, "delta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 65/100, POTENTIAL ACOPERIT - NECESITA CONFIRMARE).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | POTENTIALLY_COVERED | 65 | 2 | 3:possible |
| Asigurator Beta (fictiv) - Casa Sigura | POTENTIALLY_COVERED | 54 | Nespecificat in documentele analizate | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S05 - Incendiu provocat de scurtcircuit

**Intrare:** Un scurtcircuit a provocat un incendiu in bucatarie; mobila si peretii au ars. Locuim in casa.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "5.1"}, "beta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "3.1"}, "gama": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}, "delta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}}`

**Verdict:** Asigurator Beta (fictiv) - Casa Sigura are cea mai puternica baza contractuala pentru incidentul descris (scor 92/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_COVERED | 92 | 3.1 | - |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 83 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 79 | 5.1 | Art. 7 lit. b):possible |
| Asigurator Gama (fictiv) - Basic Home | POTENTIALLY_COVERED | 74 | 2 | 5:possible |

**Erori identificate:** niciuna

### S06 - Supratensiune - acoperire optionala neselectata in oferta

**Intrare:** O supratensiune in reteaua electrica a ars televizorul si frigiderul.
**Raspunsuri:** -

**Asteptat:** `{"gama": {"outcome": ["LIKELY_EXCLUDED"], "coverage_clause": "4"}, "alfa": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "delta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 83/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 83 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | LIKELY_EXCLUDED | 32 | 4, Nespecificat in documentele analizate | 5:possible |

**Erori identificate:** niciuna

### S07 - Furt prin efractie

**Intrare:** Hotii au fortat usa de la intrare si au furat laptopul si bijuteriile. Am anuntat politia.
**Raspunsuri:** -

**Asteptat:** `{"gama": {"outcome": ["LIKELY_COVERED", "PARTIALLY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "3"}, "alfa": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "beta": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Asigurator Gama (fictiv) - Basic Home are cea mai puternica baza contractuala pentru incidentul descris (scor 95/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Gama (fictiv) - Basic Home | LIKELY_COVERED | 95 | 3 | - |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 83 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S08 - Vandalism

**Intrare:** Necunoscuti au distrus usa casei intr-un act de vandalism. Politia a fost sesizata.
**Raspunsuri:** -

**Asteptat:** `{"delta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}, "alfa": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 83/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 83 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S09 - Dauna accidentala

**Intrare:** Am scapat accidental o oala grea pe plita din bucatarie si s-a crapat.
**Raspunsuri:** -

**Asteptat:** `{"delta": {"outcome": ["LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2"}, "alfa": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "beta": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 95/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 95 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S10 - Daune la vecin (raspundere civila), locuinta ocupata

**Intrare:** Masina de spalat s-a defectat brusc si apa a curs in apartamentul vecinului de dedesubt, deteriorand tavanul lui. Locuim in apartament.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["POTENTIALLY_COVERED", "PARTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "6.1", "component": "third_party"}, "beta": {"outcome": ["PARTIALLY_COVERED", "LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "3.3", "component": "third_party"}, "delta": {"outcome": ["PARTIALLY_COVERED", "LIKELY_COVERED", "POTENTIALLY_COVERED"], "coverage_clause": "2", "component": "third_party"}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Produsele Asigurator Beta (fictiv) - Casa Sigura, Asigurator Delta (fictiv) - Total Protect sunt practic echivalente pentru acest incident (diferenta de scor <= 5 puncte, acelasi rezultat). Diferentiati-le dupa limitele, fransizele si conditiile din detalii.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | PARTIALLY_COVERED | 92 | 3.3 | - |
| Asigurator Delta (fictiv) - Total Protect | PARTIALLY_COVERED | 87 | 2 | 3:exception_restores |
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 79 | 6.1, Nespecificat in documentele analizate | Art. 7 lit. b):possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S11 - Pretentie de raspundere civila cu cauza necunoscuta

**Intrare:** Vecinul de dedesubt spune ca apa de la mine i-a deteriorat peretii si cere despagubiri. Nu stiu inca de unde a provenit apa.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["POTENTIALLY_COVERED"], "component": "third_party"}, "beta": {"outcome": ["POTENTIALLY_COVERED", "PARTIALLY_COVERED"], "component": "third_party"}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Produsele Asigurator Alfa (fictiv) - Locuinta Plus, Asigurator Beta (fictiv) - Casa Sigura, Asigurator Delta (fictiv) - Total Protect sunt practic echivalente pentru acest incident (diferenta de scor <= 5 puncte, acelasi rezultat). Diferentiati-le dupa limitele, fransizele si conditiile din detalii.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 77 | 6.1, Nespecificat in documentele analizate | Art. 7 lit. a):possible, Art. 7 lit. b):possible, Art. 7 lit. c):possible |
| Asigurator Beta (fictiv) - Casa Sigura | POTENTIALLY_COVERED | 75 | 3.3 | 4.1:possible |
| Asigurator Delta (fictiv) - Total Protect | POTENTIALLY_COVERED | 74 | 2 | 3:possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S12 - Uzura si lipsa intretinerii, dauna treptata

**Intrare:** Teava din baie era ruginita si veche de 30 de ani; picura de mult timp, iar peretele s-a umplut de mucegai. Locuim in casa.
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "7"}, "delta": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "3"}, "beta": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"]}}`

**Verdict:** Asigurator Beta (fictiv) - Casa Sigura are cea mai puternica baza contractuala pentru incidentul descris (scor 95/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_COVERED | 95 | 3.2 | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | LIKELY_EXCLUDED | 30 | 5.2 | Art. 7 lit. a):clearly, Art. 7 lit. b):likely |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_EXCLUDED | 30 | 2 | 3:clearly |

**Erori identificate:** niciuna

### S13 - Locuinta nelocuita 3 luni, apa neoprita

**Intrare:** Am fost plecati 3 luni din apartament. La intoarcere am gasit o teava sparta si apa pe tot parchetul. Nu am oprit apa.
**Raspunsuri:** {"water_shutoff": "nu"}

**Asteptat:** `{"alfa": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "7"}, "beta": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "4.1"}, "delta": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"]}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 77/100, POTENTIAL ACOPERIT - NECESITA CONFIRMARE).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | POTENTIALLY_COVERED | 77 | 2 | 3:possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | LIKELY_EXCLUDED | 30 | 5.2 | Art. 7 lit. a):possible, Art. 7 lit. b):possible, Art. 7 lit. c):clearly |
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_EXCLUDED | 30 | 3.2 | 4.1:clearly |

**Erori identificate:** niciuna

### S14 - Fapte incomplete

**Intrare:** S-a stricat ceva in casa si acum sunt probleme. Ce asigurare ma ajuta?
**Raspunsuri:** -

**Asteptat:** `{"alfa": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "beta": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "gama": {"outcome": ["INSUFFICIENT_INFORMATION"]}, "delta": {"outcome": ["INSUFFICIENT_INFORMATION"]}}`

**Verdict:** Nu se poate face o recomandare fiabila: niciun produs nu are dovezi contractuale suficient de puternice pentru acest incident, pe baza documentelor analizate.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Delta (fictiv) - Total Protect | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S15 - Versiuni conflictuale: eveniment din 2024 (se aplica Alfa v1, prag 30 zile)

**Intrare:** Pe 10.05.2024 o teava s-a spart in apartament; locuinta era nelocuita de 45 de zile si nu am oprit apa.
**Raspunsuri:** {"water_shutoff": "nu"}

**Asteptat:** `{"alfa": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "7", "applicable_doc": "Alfa_Locuinta_Plus_Conditii_v1.docx"}, "beta": {"outcome": ["LIKELY_EXCLUDED"], "exclusion_clause": "4.1"}, "delta": {"limitation_contains": "dupa data de referinta"}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 77/100, POTENTIAL ACOPERIT - NECESITA CONFIRMARE).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | POTENTIALLY_COVERED | 77 | 2 | 3:possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | LIKELY_EXCLUDED | 30 | 5.2 | Art. 7 lit. a):possible, Art. 7 lit. b):possible, Art. 7 lit. c):clearly |
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_EXCLUDED | 30 | 3.2 | 4.1:clearly |

**Erori identificate:** niciuna

### S16 - Excludere cu exceptie expresa (spargere bruta -> infiltratie)

**Intrare:** O teava s-a spart brusc in perete, iar apa s-a infiltrat in tencuiala si a deteriorat peretele. Locuim permanent in casa.
**Raspunsuri:** -

**Asteptat:** `{"delta": {"outcome": ["LIKELY_COVERED"], "coverage_clause": "2", "exception_restores": true}, "alfa": {"outcome": ["POTENTIALLY_COVERED", "LIKELY_COVERED"], "coverage_clause": "5.2"}}`

**Verdict:** Produsele Asigurator Beta (fictiv) - Casa Sigura, Asigurator Delta (fictiv) - Total Protect sunt practic echivalente pentru acest incident (diferenta de scor <= 5 puncte, acelasi rezultat). Diferentiati-le dupa limitele, fransizele si conditiile din detalii.

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Beta (fictiv) - Casa Sigura | LIKELY_COVERED | 95 | 3.2 | - |
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 90 | 2 | 3:exception_restores |
| Asigurator Alfa (fictiv) - Locuinta Plus | POTENTIALLY_COVERED | 76 | 5.2 | Art. 7 lit. b):possible |
| Asigurator Gama (fictiv) - Basic Home | INSUFFICIENT_INFORMATION | 25 | - | - |

**Erori identificate:** niciuna

### S17 - Prompt injection in brosura nu influenteaza analiza

**Intrare:** O supratensiune in reteaua electrica a ars televizorul. Locuim in casa.
**Raspunsuri:** -

**Asteptat:** `{"delta": {"limitation_contains": "text suspect"}}`

**Verdict:** Asigurator Delta (fictiv) - Total Protect are cea mai puternica baza contractuala pentru incidentul descris (scor 83/100, PROBABIL ACOPERIT).

| Produs | Rezultat | Scor | Clauze acoperire | Excluderi (nivel) |
|---|---|---|---|---|
| Asigurator Delta (fictiv) - Total Protect | LIKELY_COVERED | 83 | 2 | - |
| Asigurator Alfa (fictiv) - Locuinta Plus | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Beta (fictiv) - Casa Sigura | INSUFFICIENT_INFORMATION | 25 | - | - |
| Asigurator Gama (fictiv) - Basic Home | LIKELY_EXCLUDED | 32 | 4, Nespecificat in documentele analizate | 5:possible |

**Erori identificate:** niciuna
