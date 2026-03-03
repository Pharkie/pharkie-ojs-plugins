# Post-rebuild Claude prompt

Copy-paste this into a fresh Claude session after a devcontainer rebuild.

---

The devcontainer was just rebuilt from clean. Run the full dev environment setup:

```
scripts/rebuild-dev.sh --with-sample-data
```

This builds Docker images, brings up the compose stack, configures OJS + WP,
imports sample data, and runs all 35 e2e tests. Takes ~10 minutes.

If it fails, check:
- `.env` has all required `OJS_JOURNAL_*` vars (`setup-ojs.sh` aborts on missing)
- Docker socket is accessible (devcontainer mount)
- Ports 8080/8081 are free

After success:
- WP:  http://localhost:8080  (admin / admin123)
- OJS: http://localhost:8081  (admin / admin123)
