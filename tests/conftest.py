import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HCA_OCR_LANGS", "eng")
os.environ.pop("ANTHROPIC_API_KEY", None)  # testele ruleaza determinist, in mod euristic


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    """Director de date izolat + corpus sintetic sincronizat o singura data."""
    wd = tmp_path_factory.mktemp("hca")
    from tests.eval_harness import setup_env
    from advisor import config
    config.settings.anthropic_api_key = ""
    corpus = setup_env(wd)
    from advisor import ingest
    src = ingest.register_source("local", str(corpus))
    run = ingest.sync_source(src["id"])
    return {"wd": wd, "corpus": corpus, "source": src, "sync": run}
