#!/usr/bin/env bash
# PR Watch — setup script
# Creates venv, installs deps, installs LaunchAgent for auto-start.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PLIST_NAME="com.monty.pr-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
DATA_DIR="$HOME/.pr-watch"

echo "🏔️  PR Watch — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Create venv
echo "📦 Creating virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# 2. Install deps
echo "📥 Installing dependencies..."
pip install -q -r "$SCRIPT_DIR/requirements.txt"

# 3. Create data dir
mkdir -p "$DATA_DIR"

# 4. Create LaunchAgent plist
echo "⚙️  Installing LaunchAgent..."
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python3</string>
        <string>$SCRIPT_DIR/pr_watch.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/pr-watch.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/pr-watch.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

# 5. Load the agent
echo "🚀 Loading LaunchAgent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo ""
echo "✅ PR Watch is running!"
echo ""
echo "  Menu bar: Look for 📋 icon in your menu bar"
echo "  Config:   $DATA_DIR/config.json"
echo "  PR data:  $DATA_DIR/prs.json  (for agents)"
echo "  Logs:     $DATA_DIR/pr-watch.log"
echo ""
echo "  To stop:  launchctl unload $PLIST_PATH"
echo "  To start: launchctl load $PLIST_PATH"
echo "  To run manually: $VENV_DIR/bin/python3 $SCRIPT_DIR/pr_watch.py"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
