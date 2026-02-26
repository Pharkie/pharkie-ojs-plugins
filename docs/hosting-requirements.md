# Hosting Requirements

What we need to deploy, test, and maintain the WP-OJS sync integration.

---

## OJS hosting (new — we can specify)

OJS is a fresh deployment. The dev environment runs OJS in Docker, and production should match that as closely as possible.

### Recommended setup

Run OJS as a Docker container on a Linux VPS (e.g. DigitalOcean droplet, Hetzner, Linode). This mirrors the dev environment exactly and makes upgrades, backups, and troubleshooting straightforward.

**Minimum spec:** 2 GB RAM, 1 vCPU, 25 GB SSD. OJS is a PHP/MySQL app with light traffic (~700 members, not concurrent). This is comfortable for staging and production. Scale up if article traffic grows significantly.

### What we need

| Requirement | Why |
|---|---|
| **SSH access** | Deploy plugin files, edit `config.inc.php`, restart services, troubleshoot |
| **Docker + Docker Compose** | Run OJS the same way as dev — reproducible, easy upgrades |
| **Root or sudo** | Manage containers, install packages, configure firewall |
| **MariaDB/MySQL access** | Not for normal operations (the plugin uses the OJS API), but essential for troubleshooting sync issues, verifying data, and running the `/preflight` health check |
| **Firewall control** | OJS needs to accept traffic on port 443 (HTTPS). The API IP allowlist is handled in `config.inc.php`, but the server firewall should also be configured |
| **SSL/TLS certificate** | HTTPS required — the WP plugin rejects `http://` URLs. Let's Encrypt via Caddy or nginx reverse proxy |
| **SMTP relay** | Transactional email (Mailgun, SES, or Postmark) with SPF/DKIM/DMARC configured for the OJS sending domain. Required for welcome emails and password resets |
| **DNS control** | Point the journal subdomain at the server, configure MX/SPF/DKIM records for email |
| **Automated backups** | Database + uploaded files. Daily, retained for 30 days minimum |

### Docker architecture

The production Docker setup would be similar to dev but simplified (no sample data, no dev tools):

```
docker-compose.yml
├── ojs          # OJS 3.5 (Apache + PHP-FPM)
├── ojs-db       # MariaDB
└── caddy/nginx  # Reverse proxy with SSL termination
```

The OJS plugin is mounted into the container (or built into the image). Config is managed via `config.inc.php` mounted as a volume.

### What an OJS upgrade looks like

1. Pull new OJS image / rebuild container
2. Run `wp ojs-sync test-connection` from WP (calls `/preflight` to verify plugin compatibility)
3. If preflight passes: done
4. If preflight fails: check which classes/methods changed, update plugin, re-test

This is why Docker matters — you can test the upgrade on staging by rebuilding the container, verify everything works, then do the same on production. No manual PHP/Apache upgrades.

### What we do NOT need

- cPanel, Plesk, or any control panel (SSH is sufficient and preferred)
- Shared hosting (OJS needs its own server/container)
- Windows (OJS runs on Linux)

---

## WP hosting (existing — what access we need)

WP is on existing hosting. We don't need to change the hosting setup, but we need specific access to deploy and configure the sync plugin.

### Access required

| Requirement | Why |
|---|---|
| **SSH access** | Deploy plugin files, run WP-CLI commands (bulk sync, test connection, reconciliation) |
| **WP-CLI** | All sync operations are WP-CLI commands. Without it, bulk sync and troubleshooting become much harder. Most hosts have it or it can be installed. |
| **WP Admin access** (Administrator role) | Configure plugin settings (OJS URL, type mapping), view sync log, test connection |
| **`wp-config.php` edit access** | Add `WPOJS_API_KEY` constant. This is a one-time change. |
| **File upload to `wp-content/plugins/`** | Deploy the `wpojs-sync` plugin. Can be done via SSH, SFTP, or WP Admin plugin upload. |
| **Outbound HTTPS from WP server** | WP needs to reach the OJS server on port 443. Some managed hosts restrict outbound connections — verify this works. |

### What we do NOT need

- Direct database access (all WP operations go through WP-CLI and the WP plugin API)
- Root/sudo access
- PHP version changes (plugin works with PHP 7.4+, which any current WP host supports)
- Server configuration changes (no Apache/nginx changes needed on the WP side)

### WP server IP

The OJS API uses an IP allowlist. We need to know the WP server's outbound IP address. The plugin settings page displays this, or run:

```bash
wp eval 'echo file_get_contents("https://api.ipify.org");' --allow-root
```

This IP goes into OJS `config.inc.php` under `[wpojs] allowed_ips`.

### Verifying access

Once we have access, the first thing to run:

```bash
# Check WP-CLI works
wp --info

# Check outbound connectivity to OJS (replace with actual OJS URL)
wp eval 'echo wp_remote_retrieve_response_code(wp_remote_get("https://journal.example.org/api/v1/wpojs/ping"));'

# Deploy plugin and test
wp ojs-sync test-connection
```

---

## Can OJS and WP be on different providers?

Yes. OJS and WP do not need to be on the same host, network, or data centre. The entire integration is authenticated HTTPS requests over the public internet — the same way WP already talks to Stripe, payment gateways, and other external services.

For example, WP on Krystal shared hosting and OJS on a DigitalOcean droplet works fine. The only configuration needed:

1. **WP side:** Set the OJS URL in plugin settings (e.g. `https://journal.example.org/index.php/journal`)
2. **OJS side:** Add the WP server's outbound IP to `allowed_ips` in `config.inc.php`

To find WP's outbound IP:

```bash
wp eval 'echo file_get_contents("https://api.ipify.org");' --allow-root
```

The `test-connection` command verifies end-to-end connectivity, auth, and IP allowlisting in one step.

---

## Staging vs production

Both environments need the same access. Deploy to staging first, smoke test with a few users, then repeat on production.

| Step | Staging | Production |
|---|---|---|
| Deploy OJS plugin | Yes | After staging verified |
| Deploy WP plugin | Yes | After staging verified |
| Configure both sides | Yes | Same config, different URLs/IPs/keys |
| `test-connection` | Yes | Yes |
| Sync 5-10 test users | Yes | Skip (go straight to dry-run) |
| `sync --dry-run` | Yes | Yes |
| `sync` (bulk) | Yes | After dry-run reviewed |
| `send-welcome-emails` | Test with 1-2 addresses | After bulk sync verified |

The launch sequence in [`plan.md`](./plan.md) has the full checklist.
