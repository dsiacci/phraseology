# Deploying an online demo

The demo server used for the video (switched off since) was one small Debian 13 server with no GPU: 2 vCPUs and 4 GB of memory. Whisper `small.en` ran on its CPU, with 2 threads: about 4.2 s per readback, against 1.4 s for `base.en`. The demo started on `base.en` for speed, and the first real test showed the cost: a French-accented "at the flying club, request taxi" came out as "de France, clamps, stochasticity". On readbacks spoken with a strong French accent (a French Piper voice reading English), `small.en` got 5 of 6 right and `base.en` 3 of 6; both got 12 of 12 with native voices. `distil-small.en` looped on its own output in this setup and was dropped. The model is a setting: `MODEL` in `/etc/default/phraseology`.

```
browser ──HTTPS──► Caddy (:443, certificate from Let's Encrypt)
                     │ reverse proxy, request bodies capped at 600 kB
                     ▼
               phraseology (127.0.0.1:8000, systemd, user "phraseology")
               Whisper small.en, 2 threads · Piper · rules
```

## What the setup does

`setup-vm.sh <domain>`, run once as root on the server:

- installs `python3-venv`, `caddy`, `nftables`, `unattended-upgrades`;
- creates the system user `phraseology`, with no login shell;
- firewall (`nftables.conf`): only TCP 22, 80 and 443 come in;
- turns off LLMNR and mDNS in systemd-resolved (LLMNR listened on every interface);
- SSH with keys only, root by key only;
- daily security updates;
- Caddy with automatic HTTPS for the domain, no access log;
- the `phraseology` systemd service (`phraseology.service`), restarted automatically, hardened: read-only system, no home, private /tmp, no capabilities, memory capped, no core dumps.

`update.sh root@<server>`, run from a clone of the repository whenever the code changes: copies the committed code (`git archive HEAD`), installs it in a virtualenv owned by root, downloads the Whisper model and the voices as the service user, restarts the service and waits for `/healthz`.

## Guards in the application (`--public`)

- one transcription at a time, at most 6 waiting; the next one gets "the frequency is busy";
- 10 seconds of audio at most (the page stops recording at 10 s, the server rejects longer clips);
- per address: 60 transcriptions and 600 requests per 10 minutes;
- request bodies: 400 kB for audio, 8 kB for JSON;
- at most 300 games in memory, dropped after an hour of inactivity.

Audio is decoded in memory, transcribed and dropped: it is never written to disk and never logged. The service log holds method, path, status and duration only. Caddy keeps no access log.

## Settings

`/etc/default/phraseology` sets the voices, the callsign, the aerodrome's name and QNH or QFE. Restart after a change: `systemctl restart phraseology`.

## Checks

```bash
curl https://<domain>/healthz
ss -tulpn                       # 22, 80, 443 open; the app on 127.0.0.1:8000 only
nft list ruleset
journalctl -u phraseology -f
```
