#!/bin/bash
# T13 v2.2 단계 3 D7.5 — Backup Restore Rehearsal (integrity check only).
#
# Verifies the most recent snapshot under data/backups/ is restorable:
#   1. Copies latest snapshot to /tmp/invasion_restore_test
#   2. SQLite schema dump (table list) — detects corruption
#   3. JSON / JSONL validity parse
#
# NEVER touches live data/ files. No bot restart. Exit 0 on pass, 1 on fail.
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_ROOT="$PROJECT_ROOT/data/backups"
TMP="/tmp/invasion_restore_test"

LATEST="$(ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sed 's:/$::' | tail -1)"
if [ -z "$LATEST" ]; then
  echo "[FAIL] no backup found under $BACKUP_ROOT"
  exit 1
fi

echo "[INFO] restoring from $LATEST"
rm -rf "$TMP" && mkdir -p "$TMP"
cp -R "$LATEST"/. "$TMP/"

# SQLite schema integrity
if [ -f "$TMP/invasion.sqlite" ]; then
  python3 - <<PYEOF
import sqlite3, sys
conn = sqlite3.connect("$TMP/invasion.sqlite")
try:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    print(f"[OK] sqlite tables ({len(tables)}): {', '.join(tables[:8])}"
          + ("..." if len(tables) > 8 else ""))
    # PRAGMA integrity_check surfaces page-level corruption.
    res = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if res != "ok":
        print(f"[FAIL] sqlite integrity_check: {res}")
        sys.exit(1)
    print("[OK] sqlite integrity_check passed")
finally:
    conn.close()
PYEOF
else
  echo "[WARN] invasion.sqlite not in snapshot"
fi

# JSON / JSONL validity
for f in "$TMP"/*.json "$TMP"/*.jsonl; do
  [ -e "$f" ] || continue
  python3 - "$f" <<'PYEOF'
import json, sys
path = sys.argv[1]
try:
    if path.endswith(".jsonl"):
        with open(path) as fp:
            n = 0
            for i, line in enumerate(fp, 1):
                if line.strip():
                    json.loads(line)
                    n += 1
        print(f"[OK] jsonl valid ({n} rows): {path}")
    else:
        with open(path) as fp:
            json.load(fp)
        print(f"[OK] json valid: {path}")
except Exception as e:
    print(f"[FAIL] {path}: {e}")
    sys.exit(1)
PYEOF
done

echo "[OK] restore rehearsal passed — snapshot is restorable"
rm -rf "$TMP"
exit 0
