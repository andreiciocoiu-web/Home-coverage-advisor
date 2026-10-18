"""Suita de scenarii (ruleaza ultima: reinitializeaza directorul de date)."""


def test_scenario_suite_metrics(env, tmp_path):
    """Ruleaza toate scenariile si verifica metricile agregate.

    ATENTIE: scenariile si motorul euristic au fost dezvoltate impreuna, pe acelasi corpus sintetic.
    Rezultatul este o verificare de regresie, nu o estimare a performantei pe documente reale.
    """
    from tests.eval_harness import run
    rep = run(out=str(tmp_path / "eval.md"), workdir=str(tmp_path / "w"))
    m = rep["metrics"]
    assert m["unsupported_conclusions"]["unverified"] == 0
    assert m["classification"]["rate"] >= 0.9
    assert m["clause_citation"]["rate"] >= 0.9
    assert m["exclusion_detection"]["rate"] >= 0.9
    assert m["other_checks"]["rate"] >= 0.8
