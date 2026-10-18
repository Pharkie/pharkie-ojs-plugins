#!/bin/bash
# Restricted shell for GitHub Actions monitoring SSH key: an older, frozen copy
# of the live server's /usr/local/bin/monitor-shell.sh.
# Referenced in authorized_keys: command="/usr/local/bin/monitor-shell.sh",restrict
#
# 🛑 DO NOT DEPLOY THIS COPY. The box's /usr/local/bin/monitor-shell.sh is
# Harbour's scripts/monitoring/lib/monitor-shell.sh (Pharkie/sea-membership-platform),
# which holds this repo's rules AND Harbour's, because Harbour's monitoring key
# goes through the same file. Copying this one over it would drop Harbour's
# rules and break Harbour's monitoring. This copy is frozen: to add or change a
# rule, edit Harbour's copy and deploy from there.

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
  "crontab -l"*)                        ;; # Cron listing (user)
  "sudo crontab -l"*)                   ;; # Cron listing (root)
  "dmesg"*)                             ;; # OOM detection
  "journalctl -k "*)                    ;; # OOM detection (kernel messages only)
  "test -f /opt/backups/"*)             ;; # Backup file existence checks
  "ls -t /opt/backups/"*)              ;; # Backup file listing
  "echo "*)                             ;; # Backup age calculation
  "stat -c"*)                           ;; # Backup file stats
  *)
    echo "Rejected: command not allowed: ${CMD:0:80}"
    exit 1
    ;;
esac

exec /bin/bash -c "$CMD"
