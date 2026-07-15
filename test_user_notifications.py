"""Tests for user notification preference helpers."""

import unittest
from datetime import datetime, timedelta

import user_notifications as un


class TestUserNotifications(unittest.TestCase):
    """Normalize prefs and due-date helpers."""

    def test_normalize_caps_keyword_alerts(self):
        prefs = un.normalize_notification_preferences(
            {
                "frequency": "daily",
                "summaries_enabled": True,
                "keyword_alerts": [
                    {"keyword": "vapor", "enabled": True},
                    {"keyword": "anxiety", "enabled": True},
                    {"keyword": "cbd", "enabled": False},
                    {"keyword": "extra", "enabled": True},
                ],
            }
        )
        self.assertEqual(prefs["frequency"], "daily")
        self.assertTrue(prefs["summaries_enabled"])
        self.assertEqual(len(prefs["keyword_alerts"]), 3)
        self.assertEqual(prefs["keyword_alerts"][0]["keyword"], "vapor")

    def test_digest_is_due_when_never_sent(self):
        prefs = un.normalize_notification_preferences(
            {"frequency": "weekly", "summaries_enabled": True}
        )
        self.assertTrue(un.digest_is_due(prefs))

    def test_digest_not_due_when_recently_sent(self):
        prefs = un.normalize_notification_preferences(
            {
                "frequency": "weekly",
                "summaries_enabled": True,
                "last_digest_sent_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.assertFalse(un.digest_is_due(prefs))

    def test_digest_due_after_interval(self):
        prefs = un.normalize_notification_preferences(
            {
                "frequency": "daily",
                "keyword_alerts": [{"keyword": "thc", "enabled": True}],
                "last_digest_sent_at": (datetime.now() - timedelta(days=2)).isoformat(
                    timespec="seconds"
                ),
            }
        )
        self.assertTrue(un.digest_is_due(prefs))

    def test_build_summary_sections(self):
        text, html = un.build_summary_sections(
            {
                "paper_count": 2,
                "study_design": {"Clinical (RCT)": 2},
                "cannabis_type": {},
                "outcome": {"Anxiety": 1},
                "clinical_exposure": {},
                "vitro_exposure": {},
                "vivo_exposure": {},
            },
            [{"title": "Example paper", "year": 2024}],
            timeframe_label="week",
        )
        self.assertIn("Papers added: 2", text)
        self.assertIn("Example paper", html)


if __name__ == "__main__":
    unittest.main()
