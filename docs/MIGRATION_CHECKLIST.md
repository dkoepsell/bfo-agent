# Hetzner v2 Migration — Pre-flight Checklist

Before running `hetzner_v2_migrate.sh`, go through this list. Every item
should be confirmed green. Do not start the migration until you have.

## 1. You are rested and focused

- [ ] You slept.
- [ ] You have 30 uninterrupted minutes.
- [ ] You have a reliable network connection (not hotel WiFi, not a cafe).
- [ ] You are not about to catch a flight or start teaching a class.

## 2. KoeppyBox state is clean

Run, from `~/projects/bfo-agent`:

```bash
git status
```

Expected: `nothing to commit, working tree clean`.

If dirty, commit or stash. Do not run the migration against a dirty tree.

```bash
git tag -l | grep ^v2-
```

Expected: all six phase tags present:
```
v2-phase1-filesystem
v2-phase2-registry
v2-phase3-read-selector
v2-phase4-lifecycle
v2-phase5-ui
v2-phase6-release
```

## 3. KoeppyBox Flask is stopped

Stop any running Flask on KoeppyBox before migrating. You will restart it
after to continue development, but the migration should run with no
local server busy.

```bash
pkill -f "python run.py" || true
```

## 4. Hetzner is currently healthy as v1

From KoeppyBox:

```bash
ssh drkoepsell@178.105.7.249 "curl -s http://127.0.0.1:5000/health"
```

Expected: JSON containing `"num_classes":6881`.

Also verify browser access works at `http://178.105.7.249` — you should
get a basic auth prompt and then the v1 UI. Do NOT skip this. If v1 is
broken before you start, you are migrating broken state.

## 5. You can SSH to Hetzner without a password

```bash
ssh drkoepsell@178.105.7.249 "echo ok"
```

Expected: prints `ok` with no password prompt.

If it asks for a password, the migration script will hang waiting for
input at multiple points. Fix SSH key auth first.

## 6. You have the migration script

```bash
ls scripts/hetzner_v2_migrate.sh
chmod +x scripts/hetzner_v2_migrate.sh
head -30 scripts/hetzner_v2_migrate.sh
```

Expected: file exists, is executable, top is the documentation
comment block.

## 7. You understand what will happen

Read the migration-reference card before running. Specifically know:

- The migration takes roughly 10 minutes end-to-end
- If anything goes wrong, `bash scripts/hetzner_v2_migrate.sh --rollback`
  restores Hetzner to v1 from a backup the script takes during step 2
- The script is idempotent on re-run (detects prior migration)
- Your Hetzner data is backed up to
  `/home/drkoepsell/bfo-agent/backup_pre_v2_TIMESTAMP/` as the very first
  real action after preflight

## 8. You have a backup plan if the migration fails

- Primary: `bash scripts/hetzner_v2_migrate.sh --rollback`
- Secondary: SSH to Hetzner, `cd bfo-agent`, `git reset --hard v1-hetzner-stable`,
  `sudo systemctl restart bfo-agent.service`
- Tertiary: restore from the `backup_pre_v2_*` directory manually

If all three fail, you still have v1 running at the IP level as long as
systemd restarts the service correctly, and the Zenodo DOI points to the
canonical ontology file which lives in the backup.

## 9. You are prepared for the browser URL change

Currently, the Hetzner UI is served at `http://178.105.7.249` and opens
directly to the BFO-Agent.

After v2, the exact same URL serves the exact same UI, but the page now
has the ontology dropdown in the header. No URL change, just new UI
elements.

## 10. You know what success looks like

After migration:

1. `http://178.105.7.249` loads the basic auth prompt
2. After login, the BFO-Agent UI loads with the ontology pill showing
   `ontology: SOoL_v1 [active]` and `6881 classes, 1678 individuals`
3. Clicking the pill shows a dropdown with one entry (SOoL_v1) and
   two action buttons (New ontology..., Finalize current)
4. Submitting a query ("What is recognition?") returns a grounded
   response from the SOoL graph

All four above, and migration is complete.
