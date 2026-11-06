#!/usr/bin/env bash
# Install meetnote for the current user: a virtual environment, the speech model
# and an entry in the app grid. Run it from the repository folder: ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
repo=$(pwd)

if command -v dpkg > /dev/null; then
    missing=()
    for pkg in python3-gi gir1.2-webkit2-4.1 pulseaudio-utils; do
        dpkg -s "$pkg" > /dev/null 2>&1 || missing+=("$pkg")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Missing system packages. Install them with:"
        echo "  sudo apt install ${missing[*]}"
        exit 1
    fi
fi

# The venv uses the system's GTK bindings (python3-gi), which pip cannot easily build.
if command -v uv > /dev/null; then
    uv venv --allow-existing --python /usr/bin/python3 --system-site-packages .venv
    uv pip install --python .venv/bin/python -e ./meetnote
else
    /usr/bin/python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -e ./meetnote
fi

.venv/bin/meetnote models  # about 670 MB, downloaded once

apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat > "$apps/meetnote.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=meetnote
Comment=Meeting notes with live offline transcription
Exec=$repo/.venv/bin/meetnote
Icon=$repo/meetnote/src/meetnote/web/icon.svg
Terminal=false
Categories=Office;AudioVideo;
EOF

echo
.venv/bin/meetnote doctor || true
echo
echo "Installed. Open meetnote from the app grid, or run: $repo/.venv/bin/meetnote"
