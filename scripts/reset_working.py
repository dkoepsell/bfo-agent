"""Delete the working ontology so it gets regenerated from the seed on next startup."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKING = ROOT / "ontology" / "working.owl"


def main():
    if WORKING.exists():
        WORKING.unlink()
        print(f"Deleted {WORKING}")
    else:
        print(f"No working ontology at {WORKING}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
