import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from provision.service import Appliance, atomic, respond, validate, wifi_profile


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device = Appliance(self.temp.name)
        self.device.connections = Path(self.temp.name) / "connections"
        self.device.connections.mkdir()
        self.data = {"hostname": "codex-one", "ssid": "home", "password": "password"}

    def test_inputs_and_ssid_encoding(self):
        for field, invalid in (("hostname", "-bad"), ("hostname", "a; reboot"),
                               ("ssid", "a" * 33), ("ssid", "line\nbreak"),
                               ("password", "short"), ("ssh_key", "command=evil ssh-ed25519 xxx")):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                validate({**self.data, field: invalid})
        text = wifi_profile("IEEE", "password", "wlan0", "123")
        self.assertIn("ssid=73;69;69;69;", text)
        self.assertIn("psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", text)
        self.assertNotIn("password", text)
        self.assertNotIn("wifi-security", wifi_profile("open", "", "wlan0", "123"))

    def test_claim_is_saved_before_radio_switch_and_requires_token(self):
        with patch("provision.service.threading.Timer") as timer:
            response = self.device.request({**self.data, "op": "configure"})
            self.assertTrue(timer.return_value.start.called)
        token = response["token"]
        saved = json.loads((self.device.state / "config.json").read_text())
        self.assertEqual(saved["token"], token)
        self.assertFalse(saved.get("wifi_configured", False))
        rebooted = Appliance(self.temp.name)
        for op in ("status", "configure", "login", "tailscale"):
            with self.subTest(op=op), self.assertRaises(ValueError):
                rebooted.request({**self.data, "op": op, "token": "wrong"})
        with patch("provision.service.run", return_value="{}"):
            self.assertNotIn("token", rebooted.request({"op": "status", "token": token}))

    def test_success_disables_ap_across_reboots(self):
        self.device.config["token"] = "secret"
        with patch("provision.service.run", return_value="") as command:
            self.device.configure_wifi(validate(self.data))
            self.assertTrue(self.device.config["wifi_configured"])
            self.assertIn(("nmcli", "connection", "delete", "codex-setup"), [c.args for c in command.call_args_list])
            self.assertFalse(any("password" in c.args for c in command.call_args_list))
        rebooted = Appliance(self.temp.name)
        with patch("provision.service.run", return_value="") as command:
            rebooted.hotspot()
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args, ("nmcli", "connection", "delete", "codex-setup"))
        self.assertEqual((self.device.state / "hostname").read_text(), "codex-one")
        self.assertEqual(next(self.device.connections.glob("*.nmconnection")).stat().st_mode & 0o777, 0o600)

    def test_failed_first_connection_returns_ap_and_does_not_commission(self):
        def fail(*args, **kwargs):
            if "--wait" in args:
                raise ValueError("Wi-Fi failed")
            return ""
        with patch("provision.service.run", side_effect=fail), patch.object(self.device, "hotspot") as hotspot:
            self.device.configure_wifi(validate(self.data))
            hotspot.assert_called_once()
        self.assertFalse(self.device.config.get("wifi_configured", False))
        self.assertFalse(list(self.device.connections.iterdir()))
        self.assertFalse(self.device.busy)

    def test_failed_reconfiguration_keeps_old_network_and_never_starts_ap(self):
        self.device.config.update(wifi_configured=True, ssid="home")
        old = self.device.connections / "codex-old.nmconnection"
        atomic(old, "uuid=old\n")
        def fail(*args, **kwargs):
            if "--wait" in args:
                raise ValueError("Wi-Fi failed")
            return ""
        with patch("provision.service.run", side_effect=fail) as command, patch.object(self.device, "hotspot") as hotspot:
            self.device.configure_wifi(validate(self.data))
            hotspot.assert_not_called()
        self.assertTrue(old.exists())
        self.assertEqual(list(self.device.connections.iterdir()), [old])
        self.assertIn(("nmcli", "connection", "up", "codex-wifi"), [c.args for c in command.call_args_list])

    def test_codex_login_and_headscale_enrollment(self):
        self.device.config.update(token="secret", wifi_configured=True)
        with patch.object(self.device, "start_process") as start, patch("provision.service.run", return_value="{}"):
            self.device.request({"op": "login", "token": "secret"})
            name, args = start.call_args.args
            self.assertEqual(name, "codex")
            self.assertEqual(args[-3:], ["codex", "login", "--device-auth"])
            self.assertIn("CODEX_HOME=/var/lib/codex/.codex", args)
            self.device.request({"op": "tailscale", "token": "secret", "hostname": "my-board",
                                 "login_server": "https://headscale.example.com"})
            name, args = start.call_args.args
            self.assertEqual(name, "tailscale")
            self.assertIn("--login-server=https://headscale.example.com", args)
            self.assertIn("--hostname=my-board", args)
            self.assertNotIn("--force-reauth", args)
            for url in ("file:///etc/passwd", "https://user:secret@example.com", "--bad", "http://a\n"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.device.request({"op": "tailscale", "token": "secret", "login_server": url})

    def test_malformed_requests_and_validation_errors(self):
        for body in (b"not-json", b"[]", b'"string"', b'{"op":"configure","ssh_key":null}'):
            self.assertIn("error", respond(self.device, body))


if __name__ == "__main__":
    unittest.main()
