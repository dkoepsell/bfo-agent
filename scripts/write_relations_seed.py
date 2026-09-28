#!/usr/bin/env python3
"""Write the canonical relation + bfoagent: annotation seed (QS-B2/B4).

Successor to patch_add_relations_seed.py: the declarations now come from
app/relation_vocab.py, the same table the resolver's alias map uses, so the
seed cannot drift from the write path. New ontologies get this seed
automatically (OntologyManager._load); use this script to drop it into a
library's seed/ directory or to inspect it.

    python scripts/write_relations_seed.py ontology/library/X/seed/canonical_relations.ttl
    python scripts/write_relations_seed.py -          # print to stdout
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import relation_vocab  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    ttl = relation_vocab.seed_turtle()
    if argv[1] == "-":
        sys.stdout.write(ttl)
    else:
        dst = Path(argv[1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(ttl)
        print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
