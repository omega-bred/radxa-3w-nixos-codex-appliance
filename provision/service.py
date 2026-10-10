"""One provisioning API, served on the first-use hotspot and BlueZ GATT."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ADDRESS = "10.42.0.1"
SERVICE_UUID = "d93f0001-7b2c-4e2a-9e74-41c868b36f90"
COMMAND_UUID = "d93f0002-7b2c-4e2a-9e74-41c868b36f90"
HOSTNAME = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise ValueError(f"{args[0]} failed; check connectivity and settings")
    return result.stdout.strip()


def atomic(path, value, mode=0o600):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, mode)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def validate(data):
    hostname = data.get("hostname", "")
    if not isinstance(hostname, str) or not re.fullmatch(HOSTNAME, hostname):
        raise ValueError("Use a hostname of 1–63 lowercase letters, digits or hyphens")
    ssid, password = data.get("ssid", ""), data.get("password", "")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError("SSID must be 1–32 bytes without control characters")
    if not isinstance(password, str) or (password and not (
        8 <= len(password.encode()) <= 63 and all(32 <= ord(c) <= 126 for c in password)
        or re.fullmatch(r"[a-fA-F0-9]{64}", password)
    )):
        raise ValueError("Use an 8–63 character WPA2 password, 64 hex digits, or leave empty for open Wi-Fi")
    key = data.get("ssh_key", "")
    if not isinstance(key, str):
        raise ValueError("Invalid SSH public key")
    key = key.strip()
    if key:
        if not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]+(?: [^\r\n]+)?", key):
            raise ValueError("Enter one SSH public key without key options")
        try:
            blob = base64.b64decode(key.split()[1], validate=True)
            size = int.from_bytes(blob[:4], "big")
            if blob[4:4 + size].decode() != key.split()[0] or len(blob) < size + 8:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError("Invalid SSH public key") from None
    return hostname, ssid, password, key


def wifi_profile(ssid, password, interface, connection_id):
    # Keyfile byte arrays avoid escaping arbitrary SSIDs; derive PSKs to keep
    # passphrases out of command arguments, logs and Nix's world-readable store.
    text = f"[connection]\nid=codex-wifi\nuuid={connection_id}\ntype=wifi\ninterface-name={interface}\nautoconnect=true\n\n"
    text += "[wifi]\nmode=infrastructure\nssid=" + ";".join(str(b) for b in ssid.encode()) + ";\n\n"
    if password:
        psk = password if len(password) == 64 else hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()
        text += f"[wifi-security]\nkey-mgmt=wpa-psk\npsk={psk}\n\n"
    return text + "[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n"


class Appliance:
    def __init__(self, state="/var/lib/codex-provision"):
        self.state = Path(state)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.config = json.loads((self.state / "config.json").read_text()) if (self.state / "config.json").exists() else {}
        self.interface = os.environ.get("WIFI_INTERFACE", "wlan0")
        self.lock = threading.RLock()
        self.busy = False
        self.error = ""
        self.processes, self.outputs = {}, {}
        self.connections = Path("/etc/NetworkManager/system-connections")

    def save(self):
        atomic(self.state / "config.json", json.dumps(self.config))

    def hotspot(self):
        if self.config.get("wifi_configured"):
            run("nmcli", "connection", "delete", "codex-setup", check=False)
            return
        if run("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", self.interface, check=False) == "codex-setup":
            return
        suffix = Path("/etc/machine-id").read_text().strip()[-6:]
        # Use a fixed channel on 2.4 GHz; boards need only AP or station mode.
        run("nmcli", "connection", "delete", "codex-setup", check=False)
        run("nmcli", "connection", "add", "type", "wifi", "ifname", self.interface,
            "con-name", "codex-setup", "ssid", f"codex-setup-{suffix}",
            "connection.autoconnect", "no", "802-11-wireless.mode", "ap",
            "802-11-wireless.band", "bg", "802-11-wireless.channel", "6",
            "ipv4.method", "shared", "ipv4.addresses", ADDRESS + "/24", "ipv6.method", "disabled",
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", "codex-setup")
        run("nmcli", "connection", "up", "codex-setup")

    def maintain_hotspot(self):
        while True:
            try:
                with self.lock:
                    if self.config.get("wifi_configured"):
                        self.hotspot()
                        return
                    if not self.busy:
                        self.hotspot()
            except (ValueError, subprocess.TimeoutExpired):
                print("Waiting for the Wi-Fi interface", flush=True)
            time.sleep(5)

    def configure_wifi(self, values):
        hostname, ssid, password, key = values
        connection_id = str(uuid.uuid4())
        profile = self.connections / f"codex-{connection_id}.nmconnection"
        joined = False
        try:
            atomic(profile, wifi_profile(ssid, password, self.interface, connection_id))
            run("nmcli", "connection", "load", str(profile))
            run("nmcli", "--wait", "40", "connection", "up", "uuid", connection_id)
            joined = True
            # Only a successful association permanently disables first-use AP.
            with self.lock:
                self.config["wifi_configured"] = True
                self.config["ssid"] = ssid
                self.save()
            run("nmcli", "connection", "delete", "codex-setup", check=False)
            for old in profile.parent.glob("codex-*.nmconnection"):
                if old != profile:
                    old_id = re.search(r"^uuid=(.+)$", old.read_text(), re.M)
                    if old_id:
                        run("nmcli", "connection", "delete", "uuid", old_id[1], check=False)
            run("hostnamectl", "--transient", "set-hostname", hostname)
            atomic(self.state / "hostname", hostname)
            if key:
                # Root-owned parents prevent a shell user replacing a path with
                # a symlink while the privileged provisioner writes public keys.
                for user in ("codex", "admin"):
                    atomic(Path("/var/lib/appliance-keys") / user, key + "\n", mode=0o644)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else "Could not apply settings"
            if not joined:
                run("nmcli", "connection", "delete", "uuid", connection_id, check=False)
                profile.unlink(missing_ok=True)
                if not self.config.get("wifi_configured"):
                    self.hotspot()
                else:
                    run("nmcli", "connection", "up", "codex-wifi", check=False)
        finally:
            with self.lock:
                self.busy = False

    def start_process(self, name, args):
        process = self.processes.get(name)
        if process and process.poll() is None:
            return
        self.outputs[name] = ""
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.processes[name] = process

        def collect():
            for line in process.stdout:
                with self.lock:
                    if '"QR":' not in line:
                        self.outputs[name] = (self.outputs[name] + line)[-4096:]
            process.wait()
        threading.Thread(target=collect, daemon=True).start()

    def request(self, data):
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        with self.lock:
            if self.config.get("token") and not hmac.compare_digest(str(data.get("token", "")), self.config["token"]):
                raise ValueError("Provisioning token required")
            op = data.get("op", "status")
            if op == "configure":
                if self.busy:
                    raise ValueError("Wi-Fi setup is already running")
                values = validate(data)
                self.config.setdefault("token", secrets.token_urlsafe(24))
                self.save()
                self.busy, self.error = True, ""
                # Let HTTP deliver the token before switching the single radio.
                worker = threading.Timer(2, self.configure_wifi, args=(values,))
                worker.daemon = True
                worker.start()
                return {"token": self.config["token"], "message": "Connecting; save this token before leaving setup"}
            if op in ("login", "tailscale"):
                if not self.config.get("wifi_configured"):
                    raise ValueError("Configure Wi-Fi first")
                if op == "login":
                    self.start_process("codex", ["runuser", "-u", "codex", "--", "env", "HOME=/var/lib/codex",
                        "CODEX_HOME=/var/lib/codex/.codex", "NO_COLOR=1", "codex", "login", "--device-auth"])
                else:
                    server = data.get("login_server", "https://controlplane.tailscale.com")
                    name = data.get("hostname", Path("/proc/sys/kernel/hostname").read_text().strip())
                    if not isinstance(server, str) or any(ord(c) <= 32 for c in server):
                        raise ValueError("Invalid control-server URL")
                    url = urlsplit(server)
                    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
                        raise ValueError("Use a control-server base URL without credentials")
                    if not isinstance(name, str) or not re.fullmatch(HOSTNAME, name):
                        raise ValueError("Invalid Tailscale hostname")
                    self.start_process("tailscale", ["tailscale", "up", "--json", "--timeout=2m",
                        "--accept-dns=false", "--login-server=" + server, "--hostname=" + name])
            elif op != "status":
                raise ValueError("Unknown operation")
            auth = Path("/var/lib/codex/.codex/auth.json")
            try:
                tail = json.loads(run("tailscale", "status", "--json"))
                tail = {key: tail.get(key) for key in ("BackendState", "TailscaleIPs", "AuthURL")}
            except (ValueError, OSError, subprocess.TimeoutExpired):
                tail = {"BackendState": "Unavailable"}
            return {"hostname": self.config.get("hostname", Path("/proc/sys/kernel/hostname").read_text().strip()),
                    "wifi_configured": bool(self.config.get("wifi_configured")), "busy": self.busy, "error": self.error,
                    "codex_credentials_present": auth.exists(), "login_output": dict(self.outputs),
                    "login_exit": {name: process.poll() for name, process in self.processes.items()}, "tailscale": tail}


def respond(appliance, payload):
    try:
        return appliance.request(json.loads(payload))
    except (ValueError, TypeError, UnicodeError) as exc:
        return {"error": str(exc)}


def serve_web(appliance):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            page = Path(os.environ["PROVISION_WEB"]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            # A JSON-only, same-origin endpoint blocks cross-site setup requests.
            if (self.path != "/api" or self.headers.get("Host") != ADDRESS
                    or self.headers.get("Content-Type") != "application/json"
                    or self.headers.get("Origin", "http://" + ADDRESS) != "http://" + ADDRESS):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if not 0 < size <= 4096:
                self.send_error(413)
                return
            body = json.dumps(respond(appliance, self.rfile.read(size))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    # The socket disappears with the AP; the LAN never gets a provisioning port.
    while True:
        if not appliance.config.get("wifi_configured"):
            try:
                server = ThreadingHTTPServer((ADDRESS, 80), Handler)
                server.timeout = 1
                while not appliance.config.get("wifi_configured"):
                    server.handle_request()
                server.server_close()
            except OSError:
                pass
        time.sleep(2)


def serve_ble(appliance):
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus(private=True)
    properties, gatt = "org.freedesktop.DBus.Properties", "org.bluez.Gatt"
    root = "/com/codex/provision"

    class Object(dbus.service.Object):
        def __init__(self, path, interface, props):
            super().__init__(bus, path)
            self.path, self.interface, self.props = path, interface, props

        @dbus.service.method(properties, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props if interface == self.interface else {}

        @dbus.service.method(properties, in_signature="ss", out_signature="v")
        def Get(self, interface, name):
            return self.GetAll(interface)[name]

    class Command(Object):
        def __init__(self):
            super().__init__(root + "/service/command", gatt + "Characteristic1", {
                "UUID": COMMAND_UUID, "Service": dbus.ObjectPath(root + "/service"),
                "Flags": dbus.Array(["read", "write", "encrypt-read", "encrypt-write"], signature="s"),
            })
            self.buffers, self.responses = {}, {}

        @dbus.service.method(gatt + "Characteristic1", in_signature="aya{sv}", out_signature="")
        def WriteValue(self, value, options):
            device = str(options["device"])
            buffer = self.buffers.get(device, b"") + bytes(value)
            if len(buffer) > 4096:
                self.buffers.pop(device, None)
                raise dbus.exceptions.DBusException("Request too large", name="org.bluez.Error.InvalidValueLength")
            if buffer.endswith(b"\n"):
                self.responses[device] = json.dumps(respond(appliance, buffer)).encode()
                self.buffers.pop(device, None)
            else:
                self.buffers[device] = buffer

        @dbus.service.method(gatt + "Characteristic1", in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            data = self.responses.get(str(options["device"]), b'{"message":"Write newline-terminated JSON first"}')
            offset, mtu = int(options.get("offset", 0)), int(options.get("mtu", 23))
            return dbus.Array(data[offset:offset + mtu - 1], signature="y")

    class Application(dbus.service.Object):
        @dbus.service.method("org.freedesktop.DBus.ObjectManager", out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            return {obj.path: {obj.interface: obj.props} for obj in (service, command)}

    class Advertisement(Object):
        @dbus.service.method("org.bluez.LEAdvertisement1", in_signature="", out_signature="")
        def Release(self):
            loop.quit()

    class Agent(dbus.service.Object):
        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, service_uuid):
            if service_uuid != SERVICE_UUID:
                raise dbus.exceptions.DBusException("Unsupported service", name="org.bluez.Error.Rejected")

    try:
        loop = GLib.MainLoop()
        objects = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
        adapters = [path for path, interfaces in objects.items()
                    if "org.bluez.GattManager1" in interfaces and "org.bluez.LEAdvertisingManager1" in interfaces]
        if not adapters:
            raise RuntimeError("No BLE peripheral adapter yet")
        adapter = bus.get_object("org.bluez", adapters[0])
        props = dbus.Interface(adapter, properties)
        for name, value in (("Powered", dbus.Boolean(True)), ("Pairable", dbus.Boolean(True)), ("PairableTimeout", dbus.UInt32(0))):
            props.Set("org.bluez.Adapter1", name, value)
        service = Object(root + "/service", gatt + "Service1", {"UUID": SERVICE_UUID, "Primary": dbus.Boolean(True)})
        command = Command()
        app = Application(bus, root)
        advert = Advertisement(root + "/advertisement", "org.bluez.LEAdvertisement1", {
            "Type": "peripheral", "Discoverable": dbus.Boolean(True),
            "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s"), "LocalName": "Codex setup",
        })
        agent = Agent(bus, root + "/agent")
        manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
        manager.RegisterAgent(root + "/agent", "NoInputNoOutput")
        manager.RequestDefaultAgent(root + "/agent")

        def failure(error):
            print("BLE registration failed:", error, flush=True)
            loop.quit()

        dbus.Interface(adapter, "org.bluez.GattManager1").RegisterApplication(root, {}, reply_handler=lambda: None, error_handler=failure)
        dbus.Interface(adapter, "org.bluez.LEAdvertisingManager1").RegisterAdvertisement(advert.path, {}, reply_handler=lambda: None, error_handler=failure)

        def changed(interface, changed, invalidated, path=None):
            if interface == "org.bluez.Device1" and not changed.get("Connected", True):
                command.buffers.pop(str(path), None)
                command.responses.pop(str(path), None)
        bus.add_signal_receiver(changed, signal_name="PropertiesChanged", dbus_interface=properties, path_keyword="path")
        bus.add_signal_receiver(lambda *args: loop.quit(), signal_name="NameOwnerChanged", dbus_interface="org.freedesktop.DBus", arg0="org.bluez")
        loop.run()
    finally:
        bus.close()


if __name__ == "__main__":
    os.umask(0o077)
    appliance = Appliance()
    # Adapter startup/restarts do not interrupt Wi-Fi or pending device login.
    threading.Thread(target=appliance.maintain_hotspot, daemon=True).start()
    threading.Thread(target=serve_web, args=(appliance,), daemon=True).start()
    while True:
        try:
            serve_ble(appliance)
        except Exception as exc:
            print("Waiting for BLE:", exc, flush=True)
        time.sleep(5)
