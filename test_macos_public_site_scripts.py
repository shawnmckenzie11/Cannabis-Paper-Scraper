"""Checks for the Mac public-site LaunchAgent scripts."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "scripts" / "macos" / "run_local_site.sh"
INSTALL_SITE = ROOT / "scripts" / "macos" / "install_macos_site.sh"
INSTALL_TUNNEL = ROOT / "scripts" / "macos" / "install_macos_cloudflared.sh"
EXAMPLE = ROOT / "scripts" / "macos" / "cloudflared-config.yml.example"
DOCS = ROOT / "docs" / "macos-public-site.md"


class MacosPublicSiteScriptsTests(unittest.TestCase):
    """Mac host scripts must force SQLite and publish paperscraper.miladlab.com."""

    def test_site_runner_unsets_database_url_and_binds_localhost(self):
        """Leftover Fly DATABASE_URL must not reach gunicorn; bind is loopback 8080."""
        text = SITE.read_text(encoding="utf-8")
        self.assertIn("unset DATABASE_URL", text)
        self.assertIn("127.0.0.1:8080", text)
        self.assertIn("--workers 1", text)
        self.assertIn("cannabis_papers.db", text)

    def test_site_installer_uses_application_support_wrapper(self):
        """launchd cannot reliably read ~/Documents; wrapper lives in Application Support."""
        text = INSTALL_SITE.read_text(encoding="utf-8")
        self.assertIn("Application Support/cannabis-paper-scraper", text)
        self.assertIn("caffeinate", text)
        self.assertIn("com.mckenzian.cannabis-site", text)

    def test_cloudflared_example_routes_hostname(self):
        """Tunnel ingress must send paperscraper.miladlab.com to local gunicorn."""
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("paperscraper.miladlab.com", text)
        self.assertIn("http://127.0.0.1:8080", text)
        self.assertIn("cfargotunnel.com", text)

    def test_tunnel_installer_prints_godaddy_cname(self):
        """Installer must tell the operator the GoDaddy CNAME target."""
        text = INSTALL_TUNNEL.read_text(encoding="utf-8")
        self.assertIn("paperscraper.miladlab.com", text)
        self.assertIn("cfargotunnel.com", text)
        self.assertIn("GoDaddy", text)

    def test_docs_cover_verify_and_repair(self):
        """Operator doc includes tab-flag repair and public URL checks."""
        text = DOCS.read_text(encoding="utf-8")
        self.assertIn("repair_recent_tab_flags.py", text)
        self.assertIn("https://paperscraper.miladlab.com", text)
        self.assertIn("/api/scheduler/status", text)


if __name__ == "__main__":
    unittest.main()
