# Hetzner v2 Migration — Quick Reference Card

Once the pre-flight checklist is green, run these commands in order.
Everything happens from **KoeppyBox** unless otherwise noted.

## The migration, in one command

```bash
cd ~/projects/bfo-agent
bash scripts/hetzner_v2_migrate.sh 2>&1 | tee /tmp/hetzner_migration.log
```

The `tee` command saves a log so if something goes wrong you can send
me the log for diagnosis.

## What you will see, in order

```
=== Preflight checks ===
  KoeppyBox: tree clean, all v2 tags present
  Hetzner: reachable
  Hetzner: bfo-agent.service exists
  Hetzner: v1 /health is ok

=== Tagging Hetzner state as v1-hetzner-stable ===
  initializing git (Hetzner had no repo)      [if Hetzner had no git repo]
  tagged v1-hetzner-stable at abc1234

=== Backing up Hetzner ontology/sessions/jobs to
    /home/drkoepsell/bfo-agent/backup_pre_v2_YYYYMMDD_HHMMSS ===
  backed up ontology/
  backed up sessions/
  backed up jobs/

=== Syncing v2 code to Hetzner ===
  [lots of rsync output showing files transferred]
  pushed app/, client/, scripts/, requirements.txt

=== Running phase 1 migration on Hetzner ===
  [phase 1 migration output, takes ~30 seconds]
  phase 1 migration done on Hetzner

=== Restarting bfo-agent.service ===
  [systemctl status output showing active (running)]

=== Verifying v2 endpoints ===
  /health: {"stats":{"num_classes":6881,...}}
  /ontologies: {"active":"SOoL_v1",...}
  PASSED

=== Committing v2 state on Hetzner ===
  tagged v2-hetzner at def5678

=== Migration complete ===
Hetzner is now on v2. Browse http://178.105.7.249 (password prompt).
Rollback with: scripts/hetzner_v2_migrate.sh --rollback
```

Total time: roughly 10 minutes. The longest step is the rsync if the
network is slow.

## Post-migration verification

From KoeppyBox terminal (so we test the public API, not loopback):

```bash
# Using the basic-auth credentials you set when you configured Caddy
curl -s -u drkoepsell:PASSWORD http://178.105.7.249/api/ontologies | python3 -m json.tool
```

Expected output:

```json
{
  "active": "SOoL_v1",
  "ontologies": [
    {
      "active": true,
      "name": "SOoL_v1",
      "stats": {"classes": 6881, "individuals": 1678},
      ...
    }
  ]
}
```

From a browser:

1. Open `http://178.105.7.249`
2. Enter basic auth credentials
3. Verify header shows `ontology: SOoL_v1 [active]`, `6881 classes | 1678 individuals`
4. Click the ontology pill. Dropdown opens.
5. Close the dropdown. Type a question in the Dialogue pane. Click Query.
6. Grounded answer returns referencing SOoL classes.

If all six check out, the migration is successful.

## If something goes wrong

### Migration script fails partway through

The script exits early without committing. Run rollback:

```bash
bash scripts/hetzner_v2_migrate.sh --rollback
```

This restores the backup and resets git to v1-hetzner-stable. Verify
by browsing `http://178.105.7.249` and confirming v1 behavior (no
ontology dropdown in the header, old UI).

### Verification fails (migration completed but endpoints wrong)

Likely cause: something with the ontology path or config didn't migrate
cleanly. Rollback, then SSH to Hetzner and investigate:

```bash
ssh drkoepsell@178.105.7.249
cd bfo-agent
sudo journalctl -u bfo-agent.service -n 100 --no-pager
```

Send me the journalctl output and I'll diagnose.

### Browser shows basic auth prompt but no UI after login

Check that Flask is running:

```bash
ssh drkoepsell@178.105.7.249 "sudo systemctl status bfo-agent --no-pager"
```

And that the client file has the API base as relative (not localhost):

```bash
ssh drkoepsell@178.105.7.249 "grep 'const API' /home/drkoepsell/bfo-agent/client/index.html"
```

Expected: `const API = "/api";` or `const API = "";`.

If it says `http://localhost:5000`, the rsync overwrote the
Hetzner-specific client. Fix:

```bash
ssh drkoepsell@178.105.7.249 "sed -i 's|const API = \"http://localhost:5000\"|const API = \"/api\"|' /home/drkoepsell/bfo-agent/client/index.html"
```

### I changed my mind and want to stay on v1

```bash
bash scripts/hetzner_v2_migrate.sh --rollback
```

v1 is now active on Hetzner. Your KoeppyBox v2 state is untouched.
You can run the migration again later.

## Rollback is safe. Rollback is cheap.

If anything feels weird, roll back. Take a breath. Rerun later when the
weirdness is understood. Don't try to patch forward during a migration
if the baseline isn't clean.
