import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from provision.service import Appliance, atomic, respond, serve_ble, validate, wifi_profile


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

    def test_ble_frames_large_login_output_and_isolates_peers(self):
        # A minimal BlueZ bus exercises the exported methods without radios.
        objects, signals = {}, {}
        class Object:
            def __init__(self, bus, path):
                objects[path] = self
        class Error(Exception):
            def __init__(self, message, name=None):
                super().__init__(message)
        bus = types.SimpleNamespace(get_object=lambda *args: None, close=lambda: None,
                                    add_signal_receiver=lambda fn, **kw: signals.update({kw["signal_name"]: fn}))
        adapter = {"/org/bluez/hci0": {"org.bluez.GattManager1": {}, "org.bluez.LEAdvertisingManager1": {}}}
        manager = types.SimpleNamespace(GetManagedObjects=lambda: adapter, Set=lambda *args: None,
            RegisterAgent=lambda *args: None, RequestDefaultAgent=lambda *args: None,
            RegisterApplication=lambda *args, **kw: kw["reply_handler"](),
            RegisterAdvertisement=lambda *args, **kw: kw["reply_handler"]())
        dbus = types.ModuleType("dbus")
        service = types.ModuleType("dbus.service")
        service.Object, service.method = Object, lambda *args, **kw: lambda fn: fn
        dbus.service = service
        dbus.SystemBus, dbus.Interface = lambda **kw: bus, lambda *args: manager
        dbus.ObjectPath, dbus.Boolean, dbus.UInt32 = str, bool, int
        dbus.Array = lambda value, **kw: list(value)
        dbus.exceptions = types.SimpleNamespace(DBusException=Error)
        glib = types.ModuleType("dbus.mainloop.glib")
        glib.DBusGMainLoop = lambda **kw: None
        repository = types.ModuleType("gi.repository")
        def exercise():
            command = objects["/com/codex/provision/service/command"]
            def read(peer):
                output = b""
                while not output.endswith(b"\n"):
                    chunk = bytes(command.ReadValue({"device": peer, "mtu": 23}))
                    self.assertTrue(chunk)
                    self.assertLess(len(chunk), 22)  # Short ATT reply, no Read Blob.
                    output += chunk
                return output
            payload = b'{"op":"status","token":"secret"}\n'
            for offset in range(0, len(payload), 20):
                command.WriteValue(list(payload[offset:offset + 20]), {"device": "owner"})
            response = read("owner")
            self.assertGreater(len(response), 512)
            self.assertEqual(json.loads(response)["login_output"]["codex"], "x" * 1200)
            self.assertNotIn("login_output", json.loads(read("other")))
            signals["PropertiesChanged"]("org.bluez.Device1", {"Connected": False}, [], path="owner")
            self.assertNotIn("login_output", json.loads(read("owner")))
            with self.assertRaises(Error):
                command.WriteValue(list(b"x" * 4097), {"device": "other"})
            self.assertNotIn("other", command.buffers)
        repository.GLib = types.SimpleNamespace(MainLoop=lambda: types.SimpleNamespace(run=exercise, quit=lambda: None))
        modules = {"dbus": dbus, "dbus.service": service, "dbus.mainloop": types.ModuleType("dbus.mainloop"),
                   "dbus.mainloop.glib": glib, "gi": types.ModuleType("gi"), "gi.repository": repository}
        self.device.config.update(token="secret", wifi_configured=True)
        self.device.outputs["codex"] = "x" * 1200
        with patch.dict("sys.modules", modules), patch("provision.service.run", return_value="{}"):
            serve_ble(self.device)


if __name__ == "__main__":
    unittest.main()
