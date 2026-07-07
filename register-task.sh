#!/usr/bin/env bash
# Registers ai-token-tracer as a macOS launchd scheduled job.
# Runs "tokentracer collect --lookback 1" daily at 23:50 if the packaged
# command is on PATH (pipx / uv tool install); otherwise falls back to
# running tracker.py from this repo checkout with python3.
# Run once as your normal user (no sudo required).
# To remove:  launchctl unload ~/Library/LaunchAgents/com.ai-token-tracer.plist
#             rm ~/Library/LaunchAgents/com.ai-token-tracer.plist

set -euo pipefail

LABEL="com.ai-token-tracer"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# -- Locate the command to run ------------------------------------------------
if command -v tokentracer &>/dev/null; then
    # Packaged install: console script on PATH; db defaults to ~/.tokentracer/usage.db
    PROG_ARGS="        <string>$(command -v tokentracer)</string>"
    WORK_DIR="$HOME"
    DATA_DIR="$HOME/.tokentracer"
    mkdir -p "$DATA_DIR"
    echo "Using packaged tokentracer: $(command -v tokentracer)"
else
    # Repo checkout: run tracker.py next to this script
    TRACKER="$SCRIPT_DIR/tracker.py"
    PYTHON="$(command -v python3 || true)"
    if [[ -z "$PYTHON" ]]; then
        echo "Error: neither 'tokentracer' nor 'python3' found on PATH" >&2
        exit 1
    fi
    if [[ ! -f "$TRACKER" ]]; then
        echo "Error: tracker.py not found at $TRACKER" >&2
        exit 1
    fi
    PROG_ARGS="        <string>$PYTHON</string>
        <string>$TRACKER</string>"
    WORK_DIR="$SCRIPT_DIR"
    DATA_DIR="$SCRIPT_DIR"
    echo "Using repo checkout: $TRACKER (python: $PYTHON)"
fi

# -- Unload existing job if present ------------------------------------------
if launchctl list "$LABEL" &>/dev/null; then
    echo "Job '$LABEL' already loaded — replacing existing job..."
    launchctl unload "$PLIST" 2>/dev/null || true
fi

# -- Write plist -------------------------------------------------------------
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
$PROG_ARGS
        <string>collect</string>
        <string>--lookback</string>
        <string>1</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$WORK_DIR</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>50</integer>
    </dict>

    <!-- Run as soon as possible if the last scheduled time was missed -->
    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$DATA_DIR/tracker.log</string>

    <key>StandardErrorPath</key>
    <string>$DATA_DIR/tracker.log</string>
</dict>
</plist>
EOF

# -- Load the job ------------------------------------------------------------
launchctl load "$PLIST"

echo ""
echo "Job registered: $LABEL"
echo "  Runs daily at 23:50 | lookback 1 day | db -> $DATA_DIR/usage.db"
echo "  Log: $DATA_DIR/tracker.log"
echo ""
echo "Useful commands:"
echo "  Run now : launchctl start $LABEL"
echo "  Status  : launchctl list $LABEL"
echo "  Remove  : launchctl unload \"$PLIST\" && rm \"$PLIST\""
