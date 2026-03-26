#!/bin/bash
# Restricted shell for GitHub Actions monitoring SSH key.
# Deployed to /usr/local/bin/monitor-shell.sh on the live server.
# Referenced in authorized_keys: command="/usr/local/bin/monitor-shell.sh",restrict
#
# To deploy: scp scripts/lib/monitor-shell.sh sea-live:/usr/local/bin/monitor-shell.sh

CMD="${SSH_ORIGINAL_COMMAND:-}"

if [ -z "$CMD" ]; then
  echo "Rejected: interactive sessions not allowed"
  exit 1
fi

# Allowlist of command prefixes used by monitor-safe.sh and monitor-deep.sh.
# All remote() calls go through "cd /opt/pharkie-ojs-plugins && ..." (first rule).
# Direct SSH commands (env reads, server stats) match subsequent rules.
case "$CMD" in
  "cd /opt/pharkie-ojs-plugins && "*)  ;; # All remote() calls (docker, wp-cli, etc.)
  "grep '^"*"' /opt/pharkie-ojs-plugins/"*) ;; # Reading .env values (anchored path)
  "cat /proc/"*)                        ;; # Load average, uptime
  "free -"*)                            ;; # Memory checks (free -m)
  "df -"*)                              ;; # Disk checks (df -h)
  "uptime"*)                            ;; # Server uptime
  "nproc")                              ;; # CPU count (exact match)
  "docker inspect --format="*)          ;; # Compose file detection
  "docker logs --since="*)              ;; # Container log checks (time-bounded)
  "crontab -l"*)                        ;; # Cron listing
  "dmesg"*)                             ;; # OOM detection
  "journalctl -k "*)                    ;; # OOM detection (kernel messages only)
  "stat -c"*)                           ;; # Backup file stats
  *)
    echo "Rejected: command not allowed: ${CMD:0:80}"
    exit 1
    ;;
esac

exec /bin/bash -c "$CMD"
