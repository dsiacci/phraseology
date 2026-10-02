#!/usr/bin/env bash
# Push the committed code (HEAD) to the demo server, install it, restart it.
# Usage, from the repository root: deploy/update.sh root@<server>
set -euo pipefail
HOST="${1:?usage: deploy/update.sh root@<server>}"
cd "$(dirname "$0")/.."
git archive --format=tar HEAD | ssh "$HOST" \
    'rm -rf /opt/phraseology/src && mkdir -p /opt/phraseology/src && tar -x -C /opt/phraseology/src'
ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/phraseology
chown -R root:root src
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q --upgrade ./src
. /etc/default/phraseology
runuser -u phraseology -- env HOME=/opt/phraseology HF_HOME=/opt/phraseology/hf \
    venv/bin/python -m phraseology download --models "$MODEL" \
    --voices "$TOWER_VOICE" "$TRAFFIC_VOICE" --voices-dir /opt/phraseology/voices
systemctl restart phraseology
for i in $(seq 1 30); do
    curl -fsS http://127.0.0.1:8000/healthz && echo && exit 0
    sleep 2
done
echo "the service did not come up"; journalctl -u phraseology -n 30 --no-pager; exit 1
REMOTE
