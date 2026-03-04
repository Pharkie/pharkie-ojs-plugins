# Post-rebuild Claude prompt

Copy-paste this into a fresh Claude session after a devcontainer rebuild.

---

The devcontainer was just rebuilt from clean. Run the full dev environment setup:

```
scripts/rebuild-dev.sh --with-sample-data
```

Run it in the background so we can keep working while it runs. Takes ~10 minutes.

This builds Docker images, brings up the compose stack, configures OJS + WP,
imports sample data, and runs all 35 e2e tests. Output is tee'd to
`logs/rebuild-<timestamp>.log` so nothing is lost.

If it fails, check:
- The log file (path printed at start of output)
- `.env` has all required `OJS_JOURNAL_*` vars (`setup-ojs.sh` aborts on missing)
- Docker socket is accessible (devcontainer mount)
- Ports 8080/8081 are free
- Both setup scripts end with health checks — look for `[FAIL]` lines

After success:
- WP:  http://localhost:8080  (admin / admin123)
- OJS: http://localhost:8081  (admin / admin123)
- All 36 e2e tests should pass
