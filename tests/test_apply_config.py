import json
import unittest
import uuid
from unittest import mock

from app import apply_config


def device(**overrides):
    value = {
        "id": "012345abcdef",
        "email": "device-012345abcdef",
        "ws_path": "/edge-0123456789abcdef01234567",
        "uuid": str(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        "port": 11000,
        "speed_mbps": 2.5,
        "enabled": True,
    }
    value.update(overrides)
    return value


class ApplyConfigTests(unittest.TestCase):
    def test_validates_and_normalizes_device(self):
        result = apply_config.validated_devices({
            "device_port_min": 11000,
            "device_port_max": 11999,
            "devices": [device()],
        })
        self.assertEqual(result[0]["port"], 11000)
        self.assertEqual(result[0]["speed_mbps"], 2.5)

    def test_rejects_path_and_port_outside_owned_range(self):
        with self.assertRaisesRegex(ValueError, "websocket"):
            apply_config.validated_devices({"devices": [device(ws_path="/anything")]})
        with self.assertRaisesRegex(ValueError, "reserved range"):
            apply_config.validated_devices({
                "device_port_min": 12000,
                "device_port_max": 12010,
                "devices": [device(port=11000)],
            })

    def test_rejects_duplicate_identity(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            apply_config.validated_devices({"devices": [device(), device()]})

    def test_xray_config_is_local_and_uses_selected_api_port(self):
        payload = json.loads(apply_config.build_xray([device()], api_port=12345))
        self.assertEqual(payload["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(payload["inbounds"][0]["port"], 12345)
        self.assertEqual(payload["inbounds"][1]["listen"], "127.0.0.1")

    def test_nginx_fragment_contains_only_exact_generated_route(self):
        payload = apply_config.build_nginx([device()])
        self.assertIn("location = /edge-0123456789abcdef01234567", payload)
        self.assertIn("127.0.0.1:11000/internal-012345abcdef", payload)
        self.assertNotIn("server_name", payload)

    def test_traffic_filter_deletion_has_an_exact_handle(self):
        record = {"port": 11000, "rate_kbps": 2000, "preference": 30000, "handle": 4096}
        with mock.patch.object(apply_config, "run") as runner:
            apply_config.delete_tc_record(record)
        self.assertEqual(runner.call_count, 2)
        for call in runner.call_args_list:
            command = call.args[0]
            self.assertIn("handle", command)
            self.assertIn("4096", command)
            self.assertIn("flower", command)

    def test_partial_filter_creation_rolls_back_only_created_filter(self):
        record = {"port": 11000, "rate_kbps": 2000, "preference": 30000, "handle": 4096}
        with mock.patch.object(apply_config, "run", side_effect=[None, RuntimeError("collision"), None]) as runner:
            with self.assertRaisesRegex(RuntimeError, "collision"):
                apply_config.add_tc_record(record)
        rollback = runner.call_args_list[2].args[0]
        self.assertIn("handle", rollback)
        self.assertIn("4096", rollback)


if __name__ == "__main__":
    unittest.main()
