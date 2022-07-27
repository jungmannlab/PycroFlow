#!/usr/bin/env python
"""
    PycroFlow/arduino_connection.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    do the arduino connection. simple functions to begin with.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import time

from Arduino import Arduino


class AriaTrigger():
    def __init__(self, pulse_pin=13, pulse_duration=.2):
        self.pulse_pin = 13
        self.pulse_duration = pulse_duration

        self.board = Arduino()
        self.board.pinMode(pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "LOW")

    def send_pulse(self):
        self.board.pinMode(pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "HIGH")
        time.sleep(self.pulse_duration)
        self.board.digitalWrite(self.pulse_pin, "LOW")

    def sense_pulse(self, timeout=10):
        self.board.pinMode(pulse_pin, "INPUT")
        triggered = False
        tic = time.time()
        while not triggered:
            triggered = self.board.digitalRead(self.pulse_pin)
            if triggered:
                break
            if time.time()-tic > timeout:
                print('Sensing TTL pulse timed out after {:.1f}s.'.format(timeout))
                break
            time.sleep(.05)
        return triggered


    def close(self):
        self.board.close()

    def __del__(self):
        self.close()
