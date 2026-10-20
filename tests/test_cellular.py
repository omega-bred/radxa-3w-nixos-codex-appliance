import os
import pty
import select
import threading
import unittest

from cellular.modem import Modem, prepare, registration


class RegistrationTests(unittest.TestCase):
    def test_registered_and_roaming_are_distinct_from_searching_and_denied(self):
        for state in (0, 1, 2, 3, 5):
            self.assertEqual(registration([f'+CEREG: 2,{state},"3875","2C45904",7']), state)

    def test_unsolicited_report_cannot_masquerade_as_query_response(self):
        with self.assertRaises(RuntimeError):
            registration(['+CEREG: 5', 'OK'])


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.modem = Modem(os.ttyname(self.slave))

    def tearDown(self):
        self.modem.close()
        os.close(self.slave)
        os.close(self.master)

    def exchange(self, response):
        def device():
            ready, _, _ = select.select([self.master], [], [], 2)
            if ready:
                os.read(self.master, 8192)
                for chunk in response:
                    os.write(self.master, chunk)
        thread = threading.Thread(target=device)
        thread.start()
        return thread

    def test_fragmented_reply_with_echo_and_unsolicited_event(self):
        thread = self.exchange([b'AT+CEREG?\r\r\n+APP PDP: 0,ACTIVE\r\n+CERE',
                                b'G: 2,5,"3875","2C45904",7\r\n\r\nO', b'K\r\n'])
        self.assertEqual(registration(self.modem.command('AT+CEREG?')), 5)
        thread.join()

    def test_errors_are_not_successful_at_responses(self):
        thread = self.exchange([b'\r\n+CME ERROR: SIM not inserted\r\n'])
        with self.assertRaisesRegex(RuntimeError, 'SIM not inserted'):
            self.modem.command('AT+CPIN?')
        thread.join()

    def test_silent_modem_times_out(self):
        with self.assertRaises(TimeoutError):
            self.modem.command('AT', timeout=.02)

    def test_second_controller_cannot_open_uart(self):
        with self.assertRaises(BlockingIOError):
            Modem(os.ttyname(self.slave))


class StartupTests(unittest.TestCase):
    def test_registered_modem_preserves_automatic_network_selection(self):
        class ReadyModem:
            def __init__(self):
                self.commands = []

            def command(self, command, **_):
                self.commands.append(command)
                return {
                    'AT+CPIN?': ['+CPIN: READY'],
                    'AT+CNMP?': ['+CNMP: 38'],
                    'AT+CMNB?': ['+CMNB: 1'],
                    'AT+COPS?': ['+COPS: 0,0,"T-Mobile",7'],
                    'AT+CEREG?': ['+CEREG: 0,5'],
                }.get(command, [])

        modem = ReadyModem()
        prepare(modem, 'sensor.net', '/unused-systemctl')
        self.assertNotIn('AT+COPS=0', modem.commands)
        self.assertNotIn('AT+CFUN=0', modem.commands)


if __name__ == '__main__':
    unittest.main()
