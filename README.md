# BFO-Agent MVP

A dialogue-driven ontology learning agent grounded in Basic Formal Ontology (BFO 2020). The agent proposes typed assertions, runs them through a reasoner before committing, and accumulates a persistent, provenance-tracked knowledge graph across sessions. Every committed triple is auditable; every refusal is grounded in absence from the graph rather than model uncertainty.

## Architecture

Three services, one HTML client.

1. **Ontology Manager** (`app/ontology_manager.py`): wraps owlready2, loads BFO plus a working ontology, exposes add/query/consistency operations.
2. **LLM Proposer** (`app/llm_proposer.py`): calls Claude to turn a user utterance into a structured, BFO-typed proposal.
3. **Flask Orchestrator** (`app/orchestrator.py`): exposes `/propose`, `/commit`, `/graph`, `/query` endpoints.
4. **Client** (`client/index.html`): single-file UI with chat pane, proposal review panel, and graph browser.

## Setup

```bash
cd bfo-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then add your ANTHROPIC_API_KEY
python scripts/download_bfo.py
python run.py
```

Open `client/index.html` in a browser. The client calls `http://localhost:5000` by default.

## Evaluation

```bash
python evaluation/evaluate.py --baseline   # unmodified Claude
python evaluation/evaluate.py --agent      # BFO-grounded agent
```

Produces a JSON report measuring cross-session consistency, grounded refusal rate, and ontology convergence.

## Jobs (resumable, persistent extract + feed)

The Extract tab is built around **jobs**. A job bundles a named project (e.g. a book) with its extracted claims, their approval flags, and per-claim feed state. Jobs persist to `jobs/*.json` and survive browser reloads, server restarts, and mid-run stops. You can:

- Extract text into a job, then close the browser and resume later.
- Run extraction multiple times on one job to add more chapters.
- Pause a feed mid-run and resume it on another day.
- Append new claims to a job while earlier ones are still in flight.

Job lifecycle via the UI (Extract tab):

1. Click **New job...** and give it a name (e.g. "SOoL full book").
2. Paste or upload a chapter; set section label and confidence threshold; click **Extract & append**. Claims are persisted to disk chunk-by-chunk.
3. Review the claims list; check/uncheck approvals (also persisted on every change).
4. Click **Feed selected**. The feeder runs one claim at a time, updating each claim's status on disk after every proposal + reasoner check.
5. Click **Pause** to stop. The browser tab can close; the server remembers where it was. Later, re-select the job and click **Resume**.

Job API (for scripting):
- `GET /jobs` list all jobs with counts
- `POST /jobs` create `{name, meta?}`
- `GET /jobs/<id>` full job (claims included)
- `POST /jobs/<id>/append_claims` `{claims: [...]}` append more
- `POST /jobs/<id>/approve` `{approvals: {claim_id: bool}}` bulk approve
- `POST /jobs/<id>/feed_one` `{auto_accept: bool}` feed the next pending+approved claim
- `POST /jobs/<id>/pause`, `POST /jobs/<id>/resume` status transitions
- `DELETE /jobs/<id>` remove a job (does NOT affect already-committed ontology)

## Seeding the graph from a text (legacy CLI)

Two-stage pipeline for feeding assertions from a book chapter or article. Kept for batch/scripted use; the web UI (Jobs, above) is the recommended interactive path.

```bash
# 1. Extract atomic ontological claims to a reviewable JSON file
python scripts/extract_claims.py \
    --input path/to/chapter.txt \
    --output extracted/chapter04.json \
    --section "SOoL Ch.4" \
    --min-confidence medium

# 2. Review extracted/chapter04.json in an editor; set approved[i]=true

# 3. Feed approved claims through /propose with interactive review
python scripts/feed_claims.py --input extracted/chapter04.json
```

Never feed without review on a new source. Extraction hallucinates; committing hallucinated claims silently corrupts the graph.

## Project Layout

```
bfo-agent/
├── run.py                       # Flask entry point
├── app/
│   ├── config.py                # Env-driven config
│   ├── schema.py                # Pydantic models for proposals
│   ├── ontology_manager.py      # owlready2 wrapper + reasoner
│   ├── llm_proposer.py          # Claude API calls
│   ├── storage.py               # JSONL session logs, git-backed versioning
│   └── orchestrator.py          # Flask routes
├── ontology/
│   ├── bfo.owl                  # Downloaded BFO 2020 (read-only)
│   ├── working.owl              # The growing knowledge graph
│   └── seed/legal_seed.ttl      # Initial SOoL-aligned classes
├── client/index.html            # Single-file UI
├── scripts/
│   ├── download_bfo.py          # Fetch BFO 2020 from OBO
│   └── reset_working.py         # Reset working ontology to seed
├── evaluation/
│   ├── test_questions.json      # 90-item eval set
│   └── evaluate.py              # Harness
└── sessions/                    # JSONL logs (gitignored)
```

## Design Notes

- Proposals are always structured JSON with BFO typing, never free text.
- The reasoner (HermiT via owlready2) runs on every proposal *before* commit; inconsistent proposals are blocked.
- Every commit is a git commit on the working ontology, giving a free audit trail.
- The `/query` endpoint tags every answer as `grounded` (all referenced entities resolve to graph IRIs) or `ungrounded` (the agent should decline).

## Seed Domain

Legal personhood, pulled from the `StructuralOntologyofLaw` repo. This is the right first domain because BFO's realist commitments are genuinely strained by legal entities, and the dialogue should reveal exactly where the seed ontology needs refinement.
