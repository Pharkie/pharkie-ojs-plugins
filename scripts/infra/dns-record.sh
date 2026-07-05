#!/bin/bash
# Manage DNS for existentialanalysis.org.uk via cPanel UAPI over SSH.
#
# DNS is hosted in cPanel on the Krystal shared box. This script runs `uapi`
# ON that box via `ssh sea-wp-live`, so it needs NO API token — it uses the
# logged-in cPanel user's context. (The token-over-HTTPS method in the docs
# needs a token from .env.live, which is SOPS-encrypted to the LIVE age key and
# does NOT decrypt on a dev laptop. This SSH path is the reliable default.)
#
# Requires: `sea-wp-live` in ~/.ssh/config (port 722, user existent).
#
# Usage:
#   scripts/infra/dns-record.sh list
#   scripts/infra/dns-record.sh add  <name> <A|CNAME|TXT> <data> [ttl]
#   scripts/infra/dns-record.sh del  <name> [type]
#
# Examples:
#   scripts/infra/dns-record.sh add analytics A 46.225.173.209
#   scripts/infra/dns-record.sh add status CNAME statuspage.betteruptime.com.
#   scripts/infra/dns-record.sh list
#
# The serial required by mass_edit_zone is fetched automatically. `add` refuses
# to clobber an existing record of the same name+type (delete it first).
set -eo pipefail

ZONE="existentialanalysis.org.uk"
SSH_HOST="sea-wp-live"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
TTL_DEFAULT=14400

die() { echo "error: $*" >&2; exit 1; }

# Run a python parser against the live zone JSON. $1 = python body reading `rows`.
parse_zone() {
  ssh $SSH_OPTS "$SSH_HOST" \
    "uapi --output=json DNS parse_zone zone=$ZONE 2>/dev/null" 2>/dev/null \
  | python3 -c "$1"
}

cmd_list() {
  parse_zone '
import sys,json,base64
d=json.load(sys.stdin)
for r in d["result"]["data"]:
    if r.get("type")!="record": continue
    dn=base64.b64decode(r.get("dname_b64","")).decode(errors="replace") if r.get("dname_b64") else ""
    data=[base64.b64decode(x).decode(errors="replace") for x in r.get("data_b64",[])]
    print("%-32s %-6s ttl=%6s  %s" % (dn, r.get("record_type",""), r.get("ttl",""), data))
'
}

# Echo the current SOA serial (3rd field of SOA data).
get_serial() {
  parse_zone '
import sys,json,base64
d=json.load(sys.stdin)
for r in d["result"]["data"]:
    if r.get("record_type")=="SOA":
        print(base64.b64decode(r["data_b64"][2]).decode()); break
'
}

# Echo "line" numbers (one per line) for records matching name [+ type].
find_lines() {
  local name="$1" type="$2"
  parse_zone "
import sys,json,base64
d=json.load(sys.stdin)
for r in d['result']['data']:
    dn=base64.b64decode(r.get('dname_b64','')).decode(errors='replace') if r.get('dname_b64') else ''
    if dn=='$name' and ('$type'=='' or r.get('record_type')=='$type'):
        if r.get('line') is not None: print(r['line'])
"
}

cmd_add() {
  local name="$1" type="$2" data="$3" ttl="${4:-$TTL_DEFAULT}"
  [ -n "$name" ] && [ -n "$type" ] && [ -n "$data" ] || die "usage: add <name> <type> <data> [ttl]"
  type=$(echo "$type" | tr '[:lower:]' '[:upper:]')

  if [ -n "$(find_lines "$name" "$type")" ]; then
    die "a $type record for '$name' already exists — 'del $name $type' first, or pick another name"
  fi

  local serial; serial=$(get_serial)
  [ -n "$serial" ] || die "could not read zone serial"

  local add_json="{\"dname\":\"$name\",\"ttl\":$ttl,\"record_type\":\"$type\",\"data\":[\"$data\"]}"
  echo "Adding: $name.$ZONE  $type  $data  (ttl $ttl, serial $serial)"

  local status
  status=$(ssh $SSH_OPTS "$SSH_HOST" \
    "uapi --output=json DNS mass_edit_zone zone=$ZONE serial=$serial add='$add_json' 2>/dev/null" 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d["result"]; print(r.get("status")); sys.stderr.write(str(r.get("errors") or ""))')
  [ "$status" = "1" ] || die "UAPI reported failure (status=$status)"
  echo "[ok] added. Verify: dig +short $type $name.$ZONE @ns1.krystal.uk"
}

cmd_del() {
  local name="$1" type="$2"
  [ -n "$name" ] || die "usage: del <name> [type]"
  local lines; lines=$(find_lines "$name" "$type")
  [ -n "$lines" ] || die "no records match '$name'${type:+ $type}"

  local serial; serial=$(get_serial)
  # Delete highest line numbers first so earlier line indices stay valid.
  for line in $(echo "$lines" | sort -rn); do
    echo "Removing line $line ($name${type:+ $type})"
    ssh $SSH_OPTS "$SSH_HOST" \
      "uapi --output=json DNS mass_edit_zone zone=$ZONE serial=$serial remove=$line 2>/dev/null" >/dev/null 2>&1
    serial=$(get_serial)  # serial changes after each edit
  done
  echo "[ok] removed."
}

case "${1:-}" in
  list) cmd_list ;;
  add)  shift; cmd_add "$@" ;;
  del)  shift; cmd_del "$@" ;;
  *)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
