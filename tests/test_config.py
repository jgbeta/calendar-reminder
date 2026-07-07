import _path  # noqa: F401
import os
from unittest import TestCase
from unittest.mock import patch

from calendar_slack_bot.config import load_config_from_env


class ConfigTests(TestCase):
    def test_notification_horizon_defaults_to_seven_days(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config_from_env()
        self.assertEqual(config.notification_horizon_days, 7)

    def test_notification_horizon_comes_from_env(self):
        with patch.dict(os.environ, {"NOTIFICATION_HORIZON_DAYS": "14"}, clear=True):
            config = load_config_from_env()
        self.assertEqual(config.notification_horizon_days, 14)


if __name__ == "__main__":
    import unittest
    unittest.main()
