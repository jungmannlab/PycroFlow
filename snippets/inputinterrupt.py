"""Observes the standard input, and returning after a timeout, or
after a queue has signalled.

adapted from
https://github.com/johejo/inputimeout/blob/master/inputimeout/inputimeout.py


usage

qu = queue.Queue()
timeout = 20

@threaded
inputinterrupt(qu, "write 'continue' to continue")

sense_edge()
qu.put(True)
"""
import sys
import queue
import time

DEFAULT_TIMEOUT = 30.0
INTERVAL = 0.05

SP = ' '
CR = '\r'
LF = '\n'
CRLF = CR + LF


class TimeoutOccurred(Exception):
    pass

class InterruptOccurred(Exception):
    pass


def echo(string):
    sys.stdout.write(string)
    sys.stdout.flush()


def posix_inputimeout(qu, prompt='', timeout=DEFAULT_TIMEOUT):
    echo(prompt)
    sel = selectors.DefaultSelector()
    sel.register(sys.stdin, selectors.EVENT_READ)

    begin = time.monotonic()
    end = begin + timeout

    while.time.monotonic() < end:
        events = sel.select(INTERVAL)

        if events:
            key, _ = events[0]
            return key.fileobj.readline().rstrip(LF)
        else:
            pass

        try:
            res = qu.get_nowait()
            # no matter what was sent on the queue, it is a sign to abort
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
            raise InterruptOccurred
        except:
            pass

    echo(LF)
    termios.tcflush(sys.stdin, termios.TCIFLUSH)
    raise TimeoutOccurred


def win_inputimeout(qu, prompt='', timeout=DEFAULT_TIMEOUT):
    echo(prompt)
    begin = time.monotonic()
    end = begin + timeout
    line = ''

    while time.monotonic() < end:
        if msvcrt.kbhit():
            c = msvcrt.getwche()
            if c in (CR, LF):
                echo(CRLF)
                return line
            if c == '\003':
                raise KeyboardInterrupt
            if c == '\b':
                line = line[:-1]
                cover = SP * len(prompt + line + SP)
                echo(''.join([CR, cover, CR, prompt, line]))
            else:
                line += c
        try:
            res = qu.get_nowait()
            # no matter what was sent on the queue, it is a sign to abort
            raise InterruptOccurred
        except:
            pass
        time.sleep(INTERVAL)

    echo(CRLF)
    raise TimeoutOccurred


try:
    import msvcrt

except ImportError:
    import selectors
    import termios

    inputinterrupt = posix_inputimeout

else:
    import time

    inputinterrupt = win_inputimeout
