#!/usr/bin/env bash
# PR Watch — uninstall script
set -euo pipefail

PLIST_NAME="com.monty.pr-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "🗑️  PR Watch — Uninstall"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Stop the agent
if [ -f "$PLIST_PATH" ]; then
    echo "Stopping LaunchAgent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm "$PLIST_PATH"
    echo "✅ LaunchAgent removed"
else
    echo "No LaunchAgent found"
fi

echo ""
echo "Note: PR Watch app files are still at $(cd "$(dirname "$0")" && pwd)"
echo "      Data is still at ~/.pr-watch/"
echo "      Delete those manually if you want a full cleanup."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
