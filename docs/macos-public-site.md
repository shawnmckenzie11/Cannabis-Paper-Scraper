# Mac public site (abandoned)

Do **not** host paperscraper on the Mac. launchd cannot read `~/Documents`,
sleep kills gunicorn, and Cloudflare Tunnel then has nothing to wrap.

Production path: GitHub Actions daily harvest + a small VPS + Cloudflare R2 +
`paperscraper.miladlab.ca`. See [github-live-catalog.md](github-live-catalog.md).
