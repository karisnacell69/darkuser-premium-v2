#!/usr/bin/env bash
# Auto-update script: pulls latest from GitHub repository and restarts services
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/karisnacell69/darkuser-premium-v2.git}"
WORKDIR="/opt/darkuser-updates"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
if [ ! -d .git ]; then
  git clone "$REPO_URL" .
else
  git fetch --all --prune
  git reset --hard origin/main
fi
# copy files if present
cp -f install-darkuser-premium.sh /usr/local/bin/install-darkuser-premium.sh || true
cp -f telegram-ssh-panel.py /usr/local/bin/telegram-ssh-panel.py || true
chmod +x /usr/local/bin/install-darkuser-premium.sh /usr/local/bin/telegram-ssh-panel.py || true
systemctl restart darkuser-bot.service || true
echo "Update applied."
