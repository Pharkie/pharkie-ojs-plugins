# Post-rebuild Claude prompt

Copy-paste this into a fresh Claude session after a devcontainer rebuild.

---

The devcontainer was just rebuilt from clean. Run the full dev environment setup in two steps:

**Step 1: Rebuild + sample data (~3–5 min)**

```
scripts/rebuild-dev.sh --with-sample-data --skip-tests
```

This builds Docker images, brings up the compose stack, configures OJS + WP,
and seeds ~1400 test WP users + subscriptions with 2 sample OJS issues.
Output is tee'd to `logs/rebuild-<timestamp>.log` so nothing is lost.

**Step 2: Import full journal archive (~10 min)**

```
backfill/import.sh backfill/output/*
```

Imports all 68 issues (30 years, ~1400 articles) with HTML + PDF galleys (469MB XML).
Overwrites the 2 sample issues from step 1 but keeps the WP test users.

If step 1 fails, check:
- The log file (path printed at start of output)
- `.env` has all required `OJS_JOURNAL_*` vars (`setup-ojs.sh` aborts on missing)
- Docker socket is accessible (devcontainer mount)
- Ports 8080/8081 are free
- Both setup scripts end with health checks — look for `[FAIL]` lines

After the rebuild, set up hcloud contexts for Hetzner (two accounts — personal and Michal's org):

```
HCLOUD_TOKEN="REDACTED_TOKEN_ROTATED" hcloud context create sea-personal --token-from-env
HCLOUD_TOKEN="REDACTED_TOKEN_ROTATED" hcloud context create sea-michal --token-from-env
```

This only needs to happen once — the contexts are stored in `~/.config/hcloud/cli.toml`
which is bind-mounted from the host, so they persist across subsequent rebuilds.

Switch accounts with: `hcloud context use sea-personal` / `hcloud context use sea-michal`

After setting up contexts, verify they survived the rebuild by running:
```
hcloud context list
```
You should see both `sea-personal` and `sea-michal`. If not (first rebuild only), run the two create commands above.

After success:
- WP:  http://localhost:8080  (admin / $WP_ADMIN_PASSWORD from .env)
- OJS: http://localhost:8081  (admin / $OJS_ADMIN_PASSWORD from .env)
- Run e2e tests: `npx playwright test` (66 tests, all should pass)
