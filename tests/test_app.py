import argparse
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from app import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temporary.name, "config.json")
        self.path_patch = mock.patch.object(app, "CONFIG_PATH", self.config_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temporary.cleanup()

    def initialize(self):
        args = argparse.Namespace(
            password="111111",
            password_stdin=False,
            host="proxy.example.com",
            username="admin",
            base_path="/net-admin-0123456789abcdef/",
            xray_api_port=10085,
            device_port_min=11000,
            device_port_max=11999,
        )
        app.initialize(args)
        return app.load_config()

    def test_initializes_with_unique_device_identity(self):
        config = self.initialize()
        self.assertEqual(config["username"], "admin")
        self.assertFalse(config["password_changed"])
        self.assertTrue(app.verify_password("111111", config))
        device = config["devices"][0]
        self.assertEqual(device["name"], "我的设备")
        self.assertEqual(device["port"], 11000)
        self.assertRegex(device["subscription_token"], r"^[a-f0-9]{48}$")

    def test_password_change_clears_initial_warning_flag(self):
        config = self.initialize()
        app.set_password(config, "Upper-lower-123!")
        self.assertTrue(config["password_changed"])
        self.assertFalse(config["weak_password"])
        self.assertTrue(app.verify_password("Upper-lower-123!", config))
        self.assertFalse(app.verify_password("111111", config))

    def test_login_uses_configured_username(self):
        config = self.initialize()
        config["username"] = "another-admin"
        page = app.login_page(config)
        self.assertIn('value="another-admin"', page)
        self.assertNotIn('value="eric"', page.lower())

    def test_next_port_uses_configured_range(self):
        config = {"device_port_min": 12000, "device_port_max": 12001, "devices": [{"port": 12000}]}
        self.assertEqual(app.next_port(config), 12001)
        config["devices"].append({"port": 12001})
        with self.assertRaisesRegex(ValueError, "设备数量"):
            app.next_port(config)

    def test_subscription_contains_only_its_device_credentials(self):
        config = self.initialize()
        first = config["devices"][0]
        second = app.new_device("Second phone")
        config["devices"].append(second)
        payload = app.clash_yaml(config, first)
        self.assertIn(first["uuid"], payload)
        self.assertIn(first["ws_path"], payload)
        self.assertNotIn(second["uuid"], payload)

    def test_config_is_valid_json_and_private(self):
        self.initialize()
        with open(self.config_path, encoding="utf-8") as handle:
            json.load(handle)
        self.assertEqual(os.stat(self.config_path).st_mode & 0o777, 0o600)

    def test_http_log_does_not_include_secret_request_path(self):
        handler = SimpleNamespace(client_address=("127.0.0.1", 12345))
        output = StringIO()
        with redirect_stdout(output):
            app.Handler.log_message(handler, '"%s" %s %s', "GET /clash/secret-token HTTP/1.1", "200", "12")
        self.assertIn("response 200", output.getvalue())
        self.assertNotIn("secret-token", output.getvalue())

    def test_quota_apply_failure_reenables_only_newly_disabled_device(self):
        config = self.initialize()
        old_disabled = app.new_device("Previously disabled")
        old_disabled.update({"port": 11001, "enabled": False, "disabled_reason": "quota"})
        config["devices"].append(old_disabled)
        app.save_config(config)

        def reach_quota(current, enforce=True):
            current["devices"][0]["enabled"] = False
            current["devices"][0]["disabled_reason"] = "quota"
            app.save_config(current)
            return True, None, True

        with mock.patch.object(app, "sync_stats_locked", side_effect=reach_quota), \
             mock.patch.object(app, "apply_runtime_locked", side_effect=RuntimeError("apply failed")):
            with self.assertRaisesRegex(RuntimeError, "apply failed"):
                app.sync_stats()

        restored = app.load_config()
        self.assertTrue(restored["devices"][0]["enabled"])
        self.assertEqual(restored["devices"][0]["disabled_reason"], "")
        self.assertFalse(restored["devices"][1]["enabled"])
        self.assertEqual(restored["devices"][1]["disabled_reason"], "quota")


if __name__ == "__main__":
    unittest.main()
