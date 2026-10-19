#!/usr/bin/env bash
# Install the qnl user units. Run outside any sandbox: systemctl is blocked.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cp "$here/qnl-autoresearch.service" "$here/qnl-autoresearch.timer" "$units/"
systemctl --user daemon-reload
systemctl --user enable --now qnl-autoresearch.timer
echo "installed: $(systemctl --user is-enabled qnl-autoresearch.timer)"
echo "run one tick now: systemctl --user start qnl-autoresearch.service"
echo "if this account is not logged in: loginctl enable-linger $(id -un)"
