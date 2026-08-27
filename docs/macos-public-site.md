# Mac public site (abandoned)

Do **not** host paperscraper on the Mac. launchd cannot read `~/Documents`,
sleep kills gunicorn, and Cloudflare Tunnel then has nothing to wrap.

Production path: GitHub Actions daily harvest + Cloudflare R2 + a free Render
web service. See [github-live-catalog.md](github-live-catalog.md).
