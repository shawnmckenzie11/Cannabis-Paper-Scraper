# Public site on your Mac: paperscraper.miladlab.com

The dashboard runs on the Mac against local SQLite. Cloudflare Tunnel publishes
`https://paperscraper.miladlab.com` without opening router ports. GoDaddy stays
DNS-only (one CNAME). Fly is not used.

The Mac must stay **on and awake**. Sleep or power-off takes the site and daily
harvest down.

**Do not run the LaunchAgent installers in Cursor Cloud** (`workspace $` on Linux).
Those commands need `launchctl` on the physical Mac. Use **Terminal.app** on the
Mac, with the repo under `Documents` (or wherever your full `cannabis_papers.db`
and `venv` already exist).

## 1. One-time tab-flag repair

From the repo root (so harvested papers appear on dashboard tabs):

```bash
unset DATABASE_URL
export DATABASE_PATH="$PWD/cannabis_papers.db"
./venv/bin/python scripts/repair_recent_tab_flags.py --since-harvested 2026-07-17
```

## 2. Start the site (LaunchAgent)

```bash
./scripts/macos/install_macos_site.sh
```

This serves `cannabis_papers.db` on `127.0.0.1:8080` with `DATABASE_URL` unset
(so leftover Fly Postgres in `.env` cannot win). One gunicorn worker keeps the
in-process daily PubMed harvest thread alive.

Confirm locally:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
```

Expect `200`. Logs: `logs/macos_site.error.log`.

Uninstall: `./scripts/macos/install_macos_site.sh --uninstall`

## 3. Cloudflare Tunnel

You need a **free Cloudflare account**. DNS for `miladlab.com` does **not** need
to move to Cloudflare.

```bash
cloudflared tunnel login    # once; authorize in the browser
./scripts/macos/install_macos_cloudflared.sh
```

The installer prints a tunnel UUID. If `cloudflared` is missing, install it with
Homebrew (`brew install cloudflared`) or from Cloudflare’s docs, then re-run.

## 4. GoDaddy CNAME

In GoDaddy DNS for `miladlab.com`:

| Field | Value |
| --- | --- |
| Type | CNAME |
| Name | `paperscraper` |
| Value | `<TUNNEL-UUID>.cfargotunnel.com` |
| TTL | 600 or default |

Do not change nameservers. Wait a few minutes for DNS.

## 5. Keep the Mac awake

System Settings → Energy (or Battery → Options):

- Prevent automatic sleeping when the display is off (power adapter).
- The site LaunchAgent also runs `caffeinate -i` while gunicorn is up.

Lid-closed laptops often still sleep unless “prevent sleep” is on and power is connected.

## 6. Verify

```bash
curl -sI https://paperscraper.miladlab.com
curl -sS https://paperscraper.miladlab.com/api/scheduler/status
```

- Site HTTP 200.
- `last_run_date` on scheduler status advances on the next calendar day the Mac is awake.
- Recently Harvested filters use `date_harvested` on the local catalog.

Confirm the process is not using Fly Postgres: site LaunchAgent logs must not mention a `postgres://` URL. Catalog path is repo-root `cannabis_papers.db`.

## Limits

- No Mac / no network / sleep → public URL fails and harvest does not run.
- This is not a datacenter. It is your always-on Mac with a public name.
