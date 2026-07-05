# Shared Caddy — one box, several projects

The server runs several independent projects (each its own Docker Compose stack) behind a **single Caddy** reverse proxy. To let projects — and different Claude sessions — add or change their own routing without treading on each other, Caddy uses a **drop-in snippet directory** instead of one shared Caddyfile.

## How it works

- The committed `docker/caddy/Caddyfile` is a stable one-liner:
  ```
  import /etc/caddy/conf.d/*.caddy
  ```
  **Nobody edits it.** Because it never changes, `git pull` on this repo never aborts on a locally-modified Caddyfile (the problem that used to happen when projects edited the monolithic file directly on the box).

- Each project owns exactly one file: `/etc/caddy/conf.d/<project>.caddy` (bind-mounted from `/opt/caddy-conf.d` on the host). One file per project → no shared file, no merge collisions, unambiguous ownership.

- Caddy runs with `--watch`, so dropping in or editing a snippet **auto-reloads** — no container restart, no downtime for the other vhosts.

- All app containers share one Docker network (`sea-net`), so Caddy reaches each app by its service name (e.g. `reverse_proxy ojs:80`).

## Adding or changing a vhost (any project)

1. Write your site block to `/opt/caddy-conf.d/<your-project>.caddy` on the host. A snippet is a normal Caddy site block:
   ```
   your.domain.example {
       header { ... }
       reverse_proxy your-app-container:PORT
   }
   ```
2. That's it — `--watch` reloads within a second. Verify with `docker compose -f … logs caddy` and `curl -I https://your.domain.example`.
3. **Version-control your snippet in your own repo** and sync it to `/opt/caddy-conf.d` on deploy (see below). Don't rely on the file existing only on the box.

## Rules that keep the peace

- **Never edit another project's snippet**, and never edit the shared `Caddyfile`.
- **Never hand-edit routing directly on the box** as an untracked change — it silently diverges from git and blocks the next pull. Track your snippet in your repo.
- **One domain per project.** If two projects need the same hostname, that's a design conversation, not a file edit.
- Your app container must be attached to the shared `sea-net` network and reachable by a stable name.

## File ownership in the drop-in dir

The drop-in directory `/opt/caddy-conf.d/` and its snippets are normally `root:root`, but **a project may own its own snippet file under a non-root user** so that project's deploy pipeline (which typically runs as an unprivileged deploy user) can update it in place — without being handed write access to the whole directory. Ownership is therefore **per-file**, not per-directory.

**Consequence for re-provisioning:** if anything ever recreates the drop-in directory or rewrites the snippet files as root (e.g. a fresh box setup, or a `cp … && chown root:root` sweep), it must **restore each snippet's intended per-file ownership afterwards** — never blanket-`chown root:root` the directory. Overwriting a project's file ownership will break that project's next deploy at its snippet-sync step.

> Deployment-specific ownership details for this project's server (which snippets are non-root-owned, and the exact ownership to restore) are kept out of this public doc — see the private ops notes.

## This repo's snippets

`pharkie-ojs-plugins` owns `wp.caddy`, `ojs.caddy`, `umami.caddy`. They're version-controlled in `docker/caddy/conf.d/` and synced to `/opt/caddy-conf.d/` on deploy:

```bash
sudo mkdir -p /opt/caddy-conf.d
sudo cp docker/caddy/conf.d/*.caddy /opt/caddy-conf.d/
```

This copies only `wp/ojs/umami.caddy`, so it doesn't touch other projects' files. If you ever re-provision the directory itself (create it, or reset ownership across it), see [File ownership in the drop-in dir](#file-ownership-in-the-drop-in-dir) — other projects' snippets may be non-root-owned and must keep their ownership.

Env-var placeholders in a snippet (e.g. `{$CADDY_OJS_DOMAIN}`) are substituted from the Caddy container's environment (`docker-compose.caddy.yml`).
