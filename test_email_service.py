"""Tests for signup verification email delivery helpers."""

import os
import unittest
from unittest import mock

import email_service


class TestEmailService(unittest.TestCase):
    """Configuration and delivery routing for verification emails."""

    def test_not_configured_without_secrets(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("RESEND_API_KEY", None)
            os.environ.pop("SMTP_SERVER", None)
            os.environ.pop("SMTP_USERNAME", None)
            os.environ.pop("SMTP_PASSWORD", None)
            self.assertFalse(email_service.is_email_delivery_configured())
            self.assertFalse(email_service.allow_dev_verification_code())

    def test_configured_with_resend(self):
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}, clear=False):
            self.assertTrue(email_service.is_email_delivery_configured())

    def test_configured_with_smtp(self):
        with mock.patch.dict(
            os.environ,
            {
                "SMTP_SERVER": "smtp.gmail.com",
                "SMTP_USERNAME": "solutions@mckenzian.com",
                "SMTP_PASSWORD": "app-pass",
            },
            clear=False,
        ):
            self.assertTrue(email_service.is_email_delivery_configured())

    def test_send_prefers_resend(self):
        with mock.patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "re_test",
                "EMAIL_FROM": "Catalog <noreply@mckenzian.com>",
            },
            clear=False,
        ):
            with mock.patch.object(email_service, "_send_via_resend", return_value=True) as resend:
                with mock.patch.object(email_service, "_send_via_smtp") as smtp:
                    ok = email_service.send_verification_email("user@example.com", "user", "123456")
                    self.assertTrue(ok)
                    resend.assert_called_once()
                    smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
