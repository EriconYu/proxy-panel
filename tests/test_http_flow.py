import argparse
import http.client
import os
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from unittest import mock

from app import app


class HttpFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temporary.name, "config.json")
        self.patches = [
            mock.patch.object(app, "CONFIG_PATH", self.config_path),
            mock.patch.object(app, "query_core_stats", return_value=({}, None)),
            mock.patch.object(app, "xray_online", return_value=False),
            mock.patch.object(app.Handler, "log_message"),
        ]
        for patcher in self.patches:
            patcher.start()
        with app.SESSION_LOCK:
            app.SESSIONS.clear()
            app.LOGIN_ATTEMPTS.clear()
        app.initialize(argparse.Namespace(
            password="111111",
            password_stdin=False,
            host="proxy.example.com",
            username="admin",
            base_path="/net-admin-0123456789abcdef/",
            xray_api_port=10085,
            device_port_min=11000,
            device_port_max=11999,
        ))
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary.cleanup()

    def request(self, method, path, fields=None, cookie=None):
        body = urlencode(fields or {})
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        self.connection.request(method, path, body=body if method == "POST" else None, headers=headers)
        response = self.connection.getresponse()
        return response, response.read().decode("utf-8")

    def test_login_and_password_change_remove_initial_warning(self):
        base = "/net-admin-0123456789abcdef/"
        response, page = self.request("GET", base)
        self.assertEqual(response.status, 200)
        self.assertIn('value="admin"', page)

        response, _ = self.request("POST", base + "login", {
            "username": "admin",
            "password": "111111",
        })
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        session_id = cookie.split("=", 1)[1]
        csrf = app.SESSIONS[session_id]["csrf"]

        response, _ = self.request("POST", base + "password", {
            "csrf": csrf,
            "current": "111111",
            "new": "Strong-Password-123!",
            "confirm": "Strong-Password-123!",
        }, cookie)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), base + "?notice=password-changed")

        response, page = self.request("GET", base, cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertNotIn("当前使用初始密码", page)
        self.assertTrue(app.verify_password("Strong-Password-123!", app.load_config()))


if __name__ == "__main__":
    unittest.main()
