#!/bin/bash
# Export + sanitise WordPress database.
# Usage: ./scripts/wp/db-snapshot-wp.sh [output-file]
set -e

OUTPUT=${1:-docker/snapshots/wp-$(date +%Y%m%d).sql}
mkdir -p "$(dirname "$OUTPUT")"

echo "Exporting WordPress database..."
docker compose exec -T wp wp db export - --allow-root > "$OUTPUT"

echo "Sanitising emails and passwords..."
sed -i.bak "s/\([a-zA-Z0-9._%+-]*\)@[a-zA-Z0-9.-]*\.[a-zA-Z]*/sanitised@example.com/g" "$OUTPUT"
rm -f "${OUTPUT}.bak"

echo "Snapshot saved: $OUTPUT"
