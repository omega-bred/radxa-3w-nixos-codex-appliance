import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cellular.failover import Policy, Routing, nameservers, probe


class PolicyTests(unittest.TestCase):
    def test_transient_loss_and_partial_recovery_do_not_flap(self):
        policy = Policy()
        for result in [False, False, True, False, False]:
            policy.observe(result)
            self.assertFalse(policy.needs_lte)
        policy.observe(False)
        self.assertTrue(policy.needs_lte)
        policy.mode = 'lte'
        for result in [True] * 5 + [False] + [True] * 5:
            policy.observe(result)
            self.assertFalse(policy.recovered)
        policy.observe(True)
        self.assertTrue(policy.recovered)

    def test_restart_in_lte_requires_sustained_wifi_recovery(self):
        policy = Policy(mode='lte')
        policy.observe(True)
        self.assertFalse(policy.recovered)
        self.assertFalse(policy.needs_lte)


class ProbeTests(unittest.TestCase):
    @patch('cellular.failover.subprocess.run')
    def test_one_provider_failure_does_not_mark_wifi_down(self, execute):
        execute.side_effect = [SimpleNamespace(returncode=28, stdout='000'),
                               SimpleNamespace(returncode=0, stdout='302')]
        self.assertTrue(probe('curl', 'wlan0', ['https://1.1.1.1/', 'https://8.8.8.8/']))
        for call in execute.call_args_list:
            args = call.args[0]
            self.assertEqual(args[args.index('--interface') + 1], 'if!wlan0')
            self.assertIn('--ipv4', args)
            self.assertIn('--noproxy', args)

    @patch('cellular.failover.subprocess.run')
    def test_both_providers_must_fail(self, execute):
        execute.side_effect = [subprocess.TimeoutExpired('curl', 5),
                               SimpleNamespace(returncode=0, stdout='503')]
        self.assertFalse(probe('curl', 'wlan0', ['https://1.1.1.1/', 'https://8.8.8.8/']))


class Network:
    """Model existing NM/PPP routes independently of the failover route owner."""
    def __init__(self):
        self.routes = {
            '-4': [
                {'dst': 'default', 'dev': 'wlan0', 'metric': 600, 'protocol': 'dhcp'},
                {'dst': 'default', 'dev': 'ppp0', 'metric': 700, 'protocol': 'boot'},
                {'dst': '203.0.113.0/24', 'dev': 'wlan0', 'metric': 50, 'protocol': '99'},
            ],
            '-6': [{'dst': 'default', 'dev': 'wlan0', 'metric': 600, 'protocol': 'ra'}],
        }
        self.dns = {'wlan0': 'nameserver 192.168.1.1\n',
                    'ppp0': 'nameserver 8.8.8.8\nnameserver 8.8.4.4\n'}
        self.ready = True
        self.mutations = []
        self.fail_dns_write = False

    def run(self, args, **kwargs):
        if args[0] == 'resolvconf':
            action, name = args[2:4] if args[1] == '-f' else args[1:3]
            if action == '-l':
                if name not in self.dns:
                    raise subprocess.CalledProcessError(2, args, output='')
                return SimpleNamespace(stdout=self.dns[name])
            self.mutations.append(args)
            if action == '-a':
                if self.fail_dns_write:
                    raise subprocess.CalledProcessError(1, args)
                self.dns[name] = kwargs['input']
            else:
                self.dns.pop(name, None)
            return SimpleNamespace(stdout='')
        family = '-4' if '-4' in args else '-6'
        if 'address' in args:
            addresses = [{'addr_info': [{'family': 'inet', 'local': '10.0.0.1'}]}]
            return SimpleNamespace(stdout=json.dumps(addresses if self.ready else []))
        if 'show' in args:
            return SimpleNamespace(stdout=json.dumps([
                route for route in self.routes[family] if route['protocol'] == '99']))
        self.mutations.append(args)
        requested = {'dst': 'default', 'metric': int(args[args.index('metric') + 1]),
                     'protocol': args[args.index('proto') + 1]}
        if 'unreachable' in args:
            requested['type'] = 'unreachable'
        else:
            requested['dev'] = args[args.index('dev') + 1]
        self.routes[family] = [r for r in self.routes[family] if r != requested]
        if 'replace' in args:
            self.routes[family].append(requested)
        return SimpleNamespace(stdout='')


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.network = Network()
        self.routing = Routing('ip', 'resolvconf', 'ppp0', runner=self.network.run)

    def test_promotion_and_cleanup_preserve_nm_and_ppp_routes_and_dns(self):
        original = json.loads(json.dumps(self.network.routes))
        self.assertTrue(self.routing.activate())
        self.assertTrue(self.routing.owned('-4'))
        self.assertTrue(self.routing.owned('-6'))
        add_dns = [a for a in self.network.mutations if a[0:2] == ['resolvconf', '-a']][0]
        self.assertIn('-x', add_dns)
        self.assertEqual(self.network.dns[self.routing.dns_entry], self.network.dns['ppp0'])
        self.routing.cleanup()
        self.assertEqual(self.network.routes, original)
        self.assertEqual(set(self.network.dns), {'wlan0', 'ppp0'})

    def test_reconciliation_does_not_rewrite_routes_or_dns_every_probe(self):
        self.routing.activate()
        before = len(self.network.mutations)
        self.routing.activate()
        self.assertEqual(len(self.network.mutations), before)

    def test_cleanup_before_first_promotion_tolerates_missing_dns(self):
        self.assertEqual(self.routing.current_dns(), '')
        self.routing.cleanup()
        self.assertIn(['resolvconf', '-f', '-d', self.routing.dns_entry],
                      self.network.mutations)

    def test_ppp_reconnect_reinstalls_route_and_refreshes_dns(self):
        self.routing.activate()
        self.network.routes['-4'] = [r for r in self.network.routes['-4']
                                     if r.get('dev') != 'ppp0']
        self.network.dns['ppp0'] = 'nameserver 9.9.9.9\n'
        self.routing.activate()
        self.assertTrue(self.routing.owned('-4'))
        self.assertEqual(self.network.dns[self.routing.dns_entry], 'nameserver 9.9.9.9\n')

    def test_lte_loss_removes_override_and_ipv6_suppression(self):
        self.routing.activate()
        self.network.ready = False
        self.assertFalse(self.routing.activate())
        self.assertFalse(self.routing.owned('-4'))
        self.assertFalse(self.routing.owned('-6'))
        self.assertNotIn(self.routing.dns_entry, self.network.dns)

    def test_failed_dns_update_rolls_back_partial_promotion(self):
        original = json.loads(json.dumps(self.network.routes))
        self.network.fail_dns_write = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.routing.activate()
        self.assertEqual(self.network.routes, original)

    def test_dns_records_are_validated_and_do_not_inject_other_settings(self):
        self.assertEqual(nameservers('# header\nsearch invalid\nnameserver 8.8.8.8\n'),
                         'nameserver 8.8.8.8\n')
        with self.assertRaises(ValueError):
            nameservers('nameserver not-an-address\n')


if __name__ == '__main__':
    unittest.main()
