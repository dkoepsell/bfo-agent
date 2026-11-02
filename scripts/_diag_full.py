import json, glob, os
sess = sorted(glob.glob(os.path.expanduser(
    "~/bfo-agent/ontology/library/icd11bfo_v2/sessions/*.jsonl")),
    key=os.path.getmtime)[-1]
rows = [json.loads(l) for l in open(sess) if l.strip()]
for r in rows:
    if r.get("event_type") == "propose":
        p = r["payload"]["proposal"]
        print("PROPOSAL", p.get("proposal_id"))
        print("  entities:")
        for e in p.get("entities", []):
            print("    -", e.get("label"), "|", e.get("bfo_label"),
                  "| parent:", e.get("parent_class"), "| kind:", e.get("kind"))
        for key in ("axioms", "relations", "triples", "restrictions"):
            if p.get(key):
                print(f"  {key}:")
                for a in p[key]:
                    print("    ", json.dumps(a)[:200])
        break
