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
    def __init__(self, pulse_pin=13, sense_pin=12, pulse_duration=.2):
        self.pulse_pin = pulse_pin
        self.sense_pin = sense_pin
        self.pulse_duration = pulse_duration

        self.board = Arduino()
        self.board.pinMode(self.pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "LOW")
        self.board.pinMode(self.sense_pin, "INPUT")

    def send_pulse(self):
        self.board.pinMode(self.pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "HIGH")
        time.sleep(self.pulse_duration)
        self.board.digitalWrite(self.pulse_pin, "LOW")

    def sense_pulse(self, timeout=10, baseline=False, refresh_rate=.01,
                    min_duration=.05, max_duration=.6):
        """
        Args:
            timeout : int
                timeout in seconds
            baseline : bool
                TTL level of baseline (0V, with upward pulse,
                    or 5V with downward pulse)
            refresh_rate : float
                refresh rate of pin sensing
            min_duration : float
                minimum duration of pulse
        Returns:
            triggered : bool
                whether the pulse was detected
        """
        # find first edge
        if baseline:
            edge = 'falling'
        else:
            edge = 'rising'
        triggered, edge_detected = self.sense_edge(timeout, edge, refresh_rate)
        if not triggered:
            # timeout
            return False
        tic = time.time()
        # find second edge
        if baseline:
            edge = 'rising'
        else:
            edge = 'falling'
        while True:
            triggered, edge_detected = self.sense_edge(max_duration, edge, refresh_rate)
            toc = time.time()
            if toc-tic < min_duration:
                # this was a mismeasurement; wait for the 'real' second edge
                # of course also the first edge could have been mismeasured
                continue
            elif toc-tic >= min_duration and toc-tic <= max_duration:
                return True
            elif toc-tic > max_duration:
                return False

    def sense_edge(self, timeout=10, edge='rising', refresh_rate=.01):
        """
        Args:
            timeout : int
                timeout in seconds
            edge : str
                which edge to detect: one of rising, falling, both
        """
        self.board.pinMode(self.sense_pin, "INPUT")
        triggered = False
        tic = time.time()
        previous_state = self.board.digitalRead(self.sense_pin)
        while not triggered:
            state = self.board.digitalRead(self.sense_pin)
            if edge=='rising' or edge=='both':
                if (not previous_state) and state:
                    triggered = True
                    edge_detected = 'rising'
            if edge=='falling' or edge=='both':
                if previous_state and (not state):
                    triggered = True
                    edge_detected = 'falling'
            if triggered:
                break
            if time.time()-tic > timeout:
                print('Sensing TTL pulse timed out after {:.1f}s.'.format(timeout))
                edge_detected = 'none'
                break
            time.sleep(refresh_rate)
            previous_state = state
        return triggered, edge_detected


    def close(self):
        self.board.close()

    def __del__(self):
        self.close()
