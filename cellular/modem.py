"""Initialize the GPIO UART modem before pppd takes exclusive ownership."""

import argparse
import fcntl
import os
import re
import select
import subprocess
import termios
import time
import tty


class Modem:
    def __init__(self, device='/dev/ttyS2'):
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.original = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[2] &= ~termios.CRTSCTS
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
        os.close(self.fd)

    def command(self, command, timeout=3):
        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, (command + '\r\n').encode('ascii'))
        deadline = time.monotonic() + timeout
        pending = b''
        lines = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            data = os.read(self.fd, 8192)
            if not data:
                raise RuntimeError('Modem UART closed')
            pending += data
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                line = line.strip().decode('ascii', errors='replace')
                if not line or line == command:
                    continue
                if line == 'OK':
                    return lines
                if line == 'ERROR' or line.startswith(('+CME ERROR:', '+CMS ERROR:')):
                    raise RuntimeError(f'{command}: {line}')
                lines.append(line)
        raise TimeoutError(f'Modem timed out on {command}')


def registration(lines):
    for line in lines:
        match = re.match(r'\+CEREG:\s*\d+,\s*(\d+)(?:,|$)', line)
        if match:
            return int(match[1])
    raise RuntimeError('Missing registration response')


def responds(modem):
    for _ in range(4):
        try:
            modem.command('AT', timeout=1.5)
            return True
        except TimeoutError:
            pass
    return False


def prepare(modem, apn, systemctl):
    if not responds(modem):
        # An interrupted PPP process can leave the UART in data mode. Escape
        # with the guard times before assuming that the modem is powered off.
        time.sleep(1.2)
        os.write(modem.fd, b'+++')
        time.sleep(1.2)
        try:
            modem.command('ATH', timeout=5)
        except (RuntimeError, TimeoutError):
            pass
    if not responds(modem):
        subprocess.run([systemctl, 'kill', '--kill-whom=main', '--signal=USR1',
                        'appliance-cellular-power.service'], check=True)
        time.sleep(7)
        if not responds(modem):
            raise RuntimeError('No AT response after one power pulse; check HAT jumpers and power')
    modem.command('ATE0')
    modem.command('AT+CMEE=2')
    if '+CPIN: READY' not in modem.command('AT+CPIN?'):
        raise RuntimeError('SIM is not ready; check SIM seating, activation, or PIN')
    # Only cycle RF when changing RAT, so a reconnect preserves registration.
    change_rat = (modem.command('AT+CNMP?') != ['+CNMP: 38'] or
                  modem.command('AT+CMNB?') != ['+CMNB: 1'])
    modem.command('AT+CGNSPWR=0')
    modem.command('AT+IFC=0,0')
    modem.command(f'AT+CGDCONT=1,"IP","{apn}"')
    if change_rat:
        modem.command('AT+CNMP=38')
        modem.command('AT+CMNB=1')
        modem.command('AT+CFUN=0', timeout=20)
        modem.command('AT+CFUN=1', timeout=20)
    # Preserve an automatic selection already in progress. Reissuing COPS=0
    # during modem/SIM startup can fail even after CPIN reports READY.
    operator = modem.command('AT+COPS?')
    if not any(re.match(r'\+COPS:\s*0(?:,|$)', line) for line in operator):
        modem.command('AT+COPS=0', timeout=120)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = registration(modem.command('AT+CEREG?'))
        if state in (1, 5):
            print('LTE-M registered: ' + '; '.join(modem.command('AT+CPSI?')), flush=True)
            return
        if state == 3:
            raise RuntimeError('Network registration denied; check SIM provisioning/roaming')
        time.sleep(5)
    raise TimeoutError('LTE-M registration timed out; check antenna, coverage, and SIM status')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apn', required=True)
    parser.add_argument('--systemctl', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,99}', args.apn):
        parser.error('Invalid APN')
    modem = Modem()
    try:
        prepare(modem, args.apn, args.systemctl)
    finally:
        modem.close()


if __name__ == '__main__':
    main()
