#!/usr/bin/env bash
# One-time setup of a Debian 13 server for the online demo. Run as root
# from a copy of this repository: bash deploy/setup-vm.sh demo.example.org
# Safe to run again.
set -euo pipefail
DOMAIN="${1:?usage: setup-vm.sh <domain>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/phraseology

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends python3-venv caddy nftables unattended-upgrades rsync curl

# A system user with no login runs the service.
id phraseology >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin phraseology
install -d -m 0755 "$APP"
install -d -m 0755 -o phraseology -g phraseology "$APP/voices" "$APP/hf"

# Firewall: 22, 80 and 443 in, everything out.
nft -c -f "$HERE/nftables.conf"
install -m 0644 "$HERE/nftables.conf" /etc/nftables.conf
systemctl enable --now nftables
nft -f /etc/nftables.conf

# No LLMNR or mDNS listening on every interface.
install -d /etc/systemd/resolved.conf.d
printf '[Resolve]\nLLMNR=no\nMulticastDNS=no\n' > /etc/systemd/resolved.conf.d/no-llmnr.conf
systemctl restart systemd-resolved

# SSH with keys only (the first value read wins, hence the 10- prefix).
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/10-keys-only.conf
sshd -t
systemctl reload ssh

# Security updates, every day.
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades

# Caddy in front, HTTPS certificate obtained automatically for $DOMAIN.
install -m 0644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
install -d /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=DEMO_DOMAIN=%s\n' "$DOMAIN" > /etc/systemd/system/caddy.service.d/domain.conf

# The application service.
[ -f /etc/default/phraseology ] || install -m 0644 "$HERE/phraseology.default" /etc/default/phraseology
install -m 0644 "$HERE/phraseology.service" /etc/systemd/system/phraseology.service
systemctl daemon-reload
systemctl enable phraseology caddy
systemctl restart caddy
echo "Setup done. Now push the code: deploy/update.sh root@<server>"
