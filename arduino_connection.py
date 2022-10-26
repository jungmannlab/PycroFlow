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
import numpy as np
import itertools

from Arduino import Arduino


class AriaTrigger():
    def __init__(self, parameters={}):
        """
        Args:
            paramters : dict
                aria connection parameters
                keys:
                    pulse_pin : int, default 13
                    sense_pin : int, default 12
                    TTL_duration : float, default 0.3 (both directions)
                    max_flowstep : float, default 30 min, timeout for aria TTL
        """
        self.pulse_pin = parameters.get('pulse_pin', 13)
        self.sense_pin = parameters.get('sense_pin', 12)
        self.pulse_duration = parameters.get('TTL_duration', .3)
        self.pulse_timeout = parameters.get('max_flowstep', 30*60)

        self.board = Arduino()
        self.board.pinMode(self.pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "LOW")
        self.board.pinMode(self.sense_pin, "INPUT")

    def send_pulse(self):
        self.board.pinMode(self.pulse_pin, "OUTPUT")
        self.board.digitalWrite(self.pulse_pin, "HIGH")
        time.sleep(self.pulse_duration)
        self.board.digitalWrite(self.pulse_pin, "LOW")

    def sense_pulse(self, timeout=None, baseline=False, refresh_rate=.01,
                    min_duration=None, max_duration=None):
        """
        TODO: in a thread, read input and return sense_pulse if 'continue'
        is entered..
        
        Args:
            timeout : int
                timeout in seconds, default: max_flowstep from config
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
        if timeout is None:
            timeout = self.pulse_timeout
        if min_duration is None:
            min_duration = max([self.pulse_duration - .1, .02])
        if max_duration is None:
            max_duration = self.pulse_duration + .1
        tstart = time.time()
        triggered = False
        edge_times_rising, edge_times_falling = [], []
        while time.time()-tstart < timeout:
            tleft = timeout - (time.time()-tstart)
            triggered, edge_detected = self.sense_edge(
                tleft, 'both', refresh_rate)
            tic = time.time()
            if edge_detected=='falling':
                edge_times_falling.append(tic)
            elif edge_detected=='rising':
                edge_times_rising.append(tic)
            elif edge_detected=='none':
                # timeout
                break
            # print('edge_times_rising', edge_times_rising)
            # print('edge_times_falling', edge_times_falling)
            # find a pulse
            time_deltas = np.fromiter(
                (f-r
                 for f, r in
                 itertools.product(edge_times_falling, edge_times_rising)),
                dtype=np.float64)
            if baseline==True:
                time_deltas = - time_deltas
            if np.any((time_deltas>=min_duration) &
                      (time_deltas<=max_duration)):
                triggered = True
                break
        # print('sense pulse time deltas:', time_deltas)
        return triggered

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
