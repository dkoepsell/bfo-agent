"""Download BFO 2020 from the OBO foundry to ontology/bfo.owl."""
import sys
from pathlib import Path

import requests

BFO_URL = "http://purl.obolibrary.org/obo/bfo/2020/bfo.owl"
BFO_FALLBACK_URL = "https://raw.githubusercontent.com/BFO-ontology/BFO-2020/master/src/owl/bfo.owl"

OUT = Path(__file__).resolve().parent.parent / "ontology" / "bfo.owl"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for url in (BFO_URL, BFO_FALLBACK_URL):
        try:
            print(f"Trying {url} ...")
            r = requests.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
            if len(r.content) < 1000:
                raise ValueError(f"Response suspiciously small: {len(r.content)} bytes")
            OUT.write_bytes(r.content)
            print(f"Wrote {len(r.content):,} bytes to {OUT}")
            return 0
        except Exception as e:
            print(f"  failed: {e}")
    print("All download attempts failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
