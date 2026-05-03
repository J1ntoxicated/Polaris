#!/usr/bin/env bash
# Apply 17 colorGroups to vault/.obsidian/graph.json
# **Obsidian 종료 후 실행** (running 중이면 overwrite)
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GRAPH="$PROJECT_ROOT/vault/.obsidian/graph.json"

# Check Obsidian not running
if pgrep -fl 'Obsidian.app' > /dev/null 2>&1; then
    echo "❌ Obsidian app still running. Quit (Cmd+Q) first."
    exit 1
fi

python3 -c "
import json
config = {
    'collapse-filter': True, 'search': '', 'showTags': True,
    'showAttachments': False, 'hideUnresolved': True, 'showOrphans': False,
    'collapse-color-groups': False,
    'colorGroups': [
        {'query': 'path:00_north_star', 'color': {'a': 1, 'rgb': 16711680}},
        {'query': 'path:02_strategies', 'color': {'a': 1, 'rgb': 16753920}},
        {'query': 'path:03_tickers', 'color': {'a': 1, 'rgb': 16776960}},
        {'query': 'path:04_signals', 'color': {'a': 1, 'rgb': 9498256}},
        {'query': 'path:10_lessons', 'color': {'a': 1, 'rgb': 65535}},
        {'query': 'path:20_architecture', 'color': {'a': 1, 'rgb': 4283904}},
        {'query': 'path:30_components', 'color': {'a': 1, 'rgb': 5025616}},
        {'query': 'path:40_bus_topics', 'color': {'a': 1, 'rgb': 13684944}},
        {'query': 'path:50_cells', 'color': {'a': 1, 'rgb': 9764095}},
        {'query': 'path:60_exit_patterns', 'color': {'a': 1, 'rgb': 16737996}},
        {'query': 'path:70_regimes', 'color': {'a': 1, 'rgb': 12632256}},
        {'query': 'path:80_decisions', 'color': {'a': 1, 'rgb': 33023}},
        {'query': 'path:90_harness/insights', 'color': {'a': 1, 'rgb': 6710937}},
        {'query': 'path:90_harness/digests', 'color': {'a': 1, 'rgb': 4210940}},
        {'query': 'path:90_harness/audit', 'color': {'a': 1, 'rgb': 6697950}},
        {'query': 'path:90_harness/self_inspection', 'color': {'a': 1, 'rgb': 11141290}},
        {'query': 'path:_meta', 'color': {'a': 1, 'rgb': 8421504}}
    ],
    'collapse-display': True, 'showArrow': True,
    'textFadeMultiplier': 0, 'nodeSizeMultiplier': 1.5,
    'lineSizeMultiplier': 1, 'collapse-forces': True,
    'centerStrength': 0.5187, 'repelStrength': 12,
    'linkStrength': 0.8026, 'linkDistance': 250,
    'scale': 0.13, 'close': False
}
with open('$GRAPH', 'w') as f:
    json.dump(config, f, indent=2)
print(f'✓ Applied {len(config[\"colorGroups\"])} colorGroups')
"
echo "✅ Done. Reopen Obsidian → graph view → 17 colors visible"
