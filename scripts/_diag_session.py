import json, sys, glob, os

sess = sorted(glob.glob(os.path.expanduser(
    "~/bfo-agent/ontology/library/icd11bfo_v2/sessions/*.jsonl")),
    key=os.path.getmtime)[-1]
rows = [json.loads(l) for l in open(sess) if l.strip()]
print("SESSION:", sess, "events:", len(rows))
for i, r in enumerate(rows):
    print("\n===== event", i, "=====")
    for k, v in r.items():
        s = v if isinstance(v, str) else json.dumps(v)
        if len(str(s)) > 700:
            s = str(s)[:700] + " …"
        print(f"  {k}: {s}")
    if i >= 5:
        break
