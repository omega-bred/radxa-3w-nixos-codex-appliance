"""Prefer Wi-Fi, checking its upstream path even while LTE carries traffic."""

import argparse
from dataclasses import dataclass
import ipaddress
import json
import logging
from pathlib import Path
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit


@dataclass
class Policy:
    failure_threshold: int = 3
    recovery_threshold: int = 6
    mode: str = 'wifi'
    failures: int = 0
    successes: int = 0

    def observe(self, healthy):
        self.failures = 0 if healthy else self.failures + 1
        self.successes = self.successes + 1 if healthy else 0

    @property
    def needs_lte(self):
        return self.mode == 'wifi' and self.failures >= self.failure_threshold

    @property
    def recovered(self):
        return self.mode == 'lte' and self.successes >= self.recovery_threshold


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=10, **kwargs)


def probe(curl, interface, endpoints, timeout=4):
    # if! forces SO_BINDTODEVICE, including when LTE has the preferred route.
    # Literal IP URLs also prevent the recovery probe from using LTE DNS.
    for endpoint in endpoints:
        try:
            result = subprocess.run([
                curl, '--disable', '--ipv4', '--interface', f'if!{interface}',
                '--noproxy', '*', '--connect-timeout', str(min(3, timeout)),
                '--max-time', str(timeout), '--silent', '--output', '/dev/null',
                '--write-out', '%{http_code}', endpoint,
            ], text=True, capture_output=True, timeout=timeout + 1)
            if result.returncode == 0 and 200 <= int(result.stdout) < 400:
                return True
        except (subprocess.TimeoutExpired, ValueError):
            pass
    return False


def nameservers(text):
    servers = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == 'nameserver':
            address = ipaddress.ip_address(fields[1])
            if address.version == 4:
                servers.append(str(address))
    return ''.join(f'nameserver {server}\n' for server in servers)


class Routing:
    # A separate protocol and DNS entry let cleanup preserve NM/PPP state.
    protocol = '99'
    dns_entry = 'appliance-cellular-failover'

    def __init__(self, ip, resolvconf, interface, metric=50, runner=run):
        self.ip = ip
        self.resolvconf = resolvconf
        self.interface = interface
        self.metric = metric
        self.run = runner

    def ready(self):
        try:
            addresses = json.loads(self.run([self.ip, '-j', '-4', 'address', 'show',
                                             'dev', self.interface]).stdout)
            return any(a.get('family') == 'inet' for link in addresses
                       for a in link.get('addr_info', []))
        except subprocess.CalledProcessError:
            return False

    def owned(self, family):
        routes = json.loads(self.run([self.ip, '-j', family, 'route', 'show',
                                      'table', 'main', 'proto', self.protocol]).stdout)
        return any(r.get('dst') == 'default' and r.get('metric', 0) == self.metric
                   and ((family == '-4' and r.get('dev') == self.interface)
                        or (family == '-6' and r.get('type') == 'unreachable'))
                   for r in routes)

    def route_args(self, family, operation):
        route = ([self.ip, family, 'route', operation] +
                 (['unreachable'] if family == '-6' else []) + ['default'])
        if family == '-4':
            route += ['dev', self.interface]
        return route + ['table', 'main', 'metric', str(self.metric), 'proto', self.protocol]

    def cleanup(self):
        for family in ('-4', '-6'):
            if self.owned(family):
                self.run(self.route_args(family, 'del'))
        self.run([self.resolvconf, '-f', '-d', self.dns_entry])

    def current_dns(self):
        try:
            return nameservers(self.run([self.resolvconf, '-l', self.dns_entry]).stdout)
        except subprocess.CalledProcessError as error:
            # openresolv -l returns 2 for a key that has not been created yet.
            if error.returncode == 2 and not error.stdout:
                return ''
            raise

    def activate(self):
        if not self.ready():
            self.cleanup()
            return False
        servers = nameservers(self.run([self.resolvconf, '-l', self.interface]).stdout)
        if not servers:
            raise RuntimeError('PPP DNS servers are not available yet')
        try:
            for family in ('-4', '-6'):
                if not self.owned(family):
                    self.run(self.route_args(family, 'replace'))
            current = self.current_dns()
            if current != servers:
                self.run([self.resolvconf, '-a', self.dns_entry, '-m', '0', '-x'], input=servers)
        except Exception:
            self.cleanup()
            raise
        return True


def load_mode(path):
    try:
        mode = json.loads(path.read_text()).get('mode')
        return mode if mode in ('wifi', 'lte') else 'wifi'
    except (OSError, ValueError):
        return 'wifi'


def status(path, policy, healthy, active):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({
        'mode': policy.mode, 'wifi_healthy': healthy, 'lte_route_active': active,
        'failures': policy.failures, 'successes': policy.successes,
        'updated': int(time.time()),
    }) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wifi', default='wlan0')
    parser.add_argument('--lte', default='ppp0')
    parser.add_argument('--ip', required=True)
    parser.add_argument('--curl', required=True)
    parser.add_argument('--resolvconf', required=True)
    parser.add_argument('--interval', type=int, default=10)
    parser.add_argument('--failures', type=int, default=3)
    parser.add_argument('--recoveries', type=int, default=6)
    parser.add_argument('--metric', type=int, default=50)
    parser.add_argument('--endpoint', action='append', required=True)
    parser.add_argument('--status', type=Path,
                        default=Path('/run/appliance-cellular-failover/status.json'))
    args = parser.parse_args()
    if min(args.interval, args.failures, args.recoveries, args.metric) < 1:
        parser.error('Intervals, thresholds, and metric must be positive')
    for endpoint in args.endpoint:
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or ipaddress.ip_address(parsed.hostname).version != 4:
            parser.error('Probe endpoints must use HTTPS with literal IPv4 addresses')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    routing = Routing(args.ip, args.resolvconf, args.lte, args.metric)
    mode = ('lte' if routing.owned('-4') or routing.owned('-6')
            else load_mode(args.status))
    policy = Policy(args.failures, args.recoveries, mode=mode)
    try:
        while not stopped.is_set():
            started = time.monotonic()
            try:
                healthy = probe(args.curl, args.wifi, args.endpoint)
                policy.observe(healthy)
                active = False
                if policy.recovered:
                    routing.cleanup()
                    policy.mode = 'wifi'
                    logging.info('Wi-Fi upstream recovered; restored Wi-Fi routing and DNS')
                elif policy.mode == 'lte':
                    # Reconcile routes/DNS after a PPP reconnect without issuing
                    # netlink changes on every healthy iteration.
                    active = routing.activate()
                elif policy.needs_lte and routing.ready():
                    # Only probe LTE before promotion, conserving prepaid data.
                    if probe(args.curl, args.lte, args.endpoint) and routing.activate():
                        policy.mode = 'lte'
                        active = True
                        logging.info('Wi-Fi upstream failed; selected LTE routing and DNS')
                status(args.status, policy, healthy, active)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                logging.exception('Failover check failed; retrying')
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    logging.error('%s', error.stderr.strip())
            stopped.wait(max(0, args.interval - (time.monotonic() - started)))
    finally:
        routing.cleanup()


if __name__ == '__main__':
    main()
