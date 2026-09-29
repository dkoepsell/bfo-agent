"""Creating an ontology from bare BFO 2020 (no library clone)."""
from pathlib import Path

import pytest

from app.registry import BARE_BFO_SEED, OntologyRegistry

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.mark.skipif(not BFO_PATH.exists(), reason="bfo.owl not present")
def test_create_from_bare_bfo_2020(tmp_path):
    lib = tmp_path / "library"
    (lib / "Base" / "seed").mkdir(parents=True)
    (lib / "Base" / "seed" / "domain.ttl").write_text("")
    (lib / "Base" / "manifest.json").write_text('{"name": "Base"}')
    reg = OntologyRegistry(BFO_PATH, lib, active_name="Base")
    manifest = reg.create("Categories", "Aristotle test",
                          clone_seeds_from=BARE_BFO_SEED)
    seed = lib / "Categories" / "seed"
    assert [p.name for p in seed.iterdir()] == ["bfo_relations.ttl"]
    assert "bfoagent:quantifierDefaulted" in (seed / "bfo_relations.ttl").read_text()
    assert manifest["seeded_from"] == BARE_BFO_SEED
