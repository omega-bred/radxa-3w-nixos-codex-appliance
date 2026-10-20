"""Keep the Waveshare PWRKEY driver released; SIGUSR1 presses it once."""

import fcntl
import os
import signal
import socket
import struct
import time


def main():
    # Physical pin 7 (BCM4 on a Pi) is GPIO3_C4 on the ZERO 3W.
    # Linux gpio.h v1 line-handle ABI: offsets[64], flags, values[64],
    # consumer[32], lines, fd. Keep the handle open to retain the output.
    chip = os.open('/dev/gpiochip3', os.O_RDONLY)
    request = bytearray(struct.pack('=64II64B32sIi',
        *([20] + [0] * 63), 2, *([0] * 64), b'sim7080-pwrkey', 1, 0))
    fcntl.ioctl(chip, 0xC16CB403, request, True)
    line = struct.unpack_from('=i', request, 360)[0]
    os.close(chip)

    def pulse(*_):
        fcntl.ioctl(line, 0xC040B409, bytes([1] + [0] * 63))
        try:
            time.sleep(1.2)
        finally:
            fcntl.ioctl(line, 0xC040B409, bytes(64))
        print('PWRKEY released after power pulse', flush=True)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGUSR1, pulse)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    notify = os.environ.get('NOTIFY_SOCKET')
    if notify:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect('\0' + notify[1:] if notify.startswith('@') else notify)
            sock.sendall(b'READY=1')
    print('Holding physical pin 7 low (PWRKEY released)', flush=True)
    try:
        while not stopping:
            time.sleep(1)
    finally:
        fcntl.ioctl(line, 0xC040B409, bytes(64))
        os.close(line)


if __name__ == '__main__':
    main()
