#!/usr/bin/env python3
"""
Arduino Sensor Communication Interface
State-of-the-art Python script for communicating with Arduino sensor system
"""

import serial
import serial.tools.list_ports
import time
import threading
import sys
import signal
from datetime import datetime
import logging
import threading
from functools import wraps


logger = logging.getLogger(__name__)


def run_threaded(attr_name="_last_thread"):
    def decorator(fn):
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            thread = threading.Thread(target=fn, args=(self, *args), kwargs=kwargs)
            setattr(self, attr_name, thread)
            thread.start()
        return wrapper
    return decorator


class ArduinoSensorInterface:
    def __init__(self, port=None, baud_rate=115200, timeout=2):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial_conn = None
        self.is_connected = False
        self.logging_active = False
        self.log_thread = None
        self.HANDSHAKE_CMD = b'H'
        self.POLL_CMD = b'P'
        self.RESET_CMD = b'R'

        self.sensor_wet_flag = threading.Event()
        self.monitor_abort_flag = threading.Event()
        
    def find_arduino_port(self):
        arduino_ports = []
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if any(identifier in port.description.lower() for identifier in 
                   ['arduino', 'uno', 'ch340', 'ftdi', 'usb serial']):
                arduino_ports.append(port.device)
        if arduino_ports:
            logger.debug(f"Found potential Arduino ports: {arduino_ports}")
            return arduino_ports[0]
        else:
            logger.debug("No Arduino ports automatically detected")
            logger.debug("Available ports:")
            for port in ports:
                logger.debug(f"  {port.device}: {port.description}")
            return None
    
    def connect(self):
        if not self.port:
            self.port = self.find_arduino_port()
            if not self.port:
                logger.debug("Please specify port manually")
                return False
        try:
            logger.debug(f"Connecting to {self.port} at {self.baud_rate} baud...")
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )
            logger.debug("Waiting for Arduino to initialize...")
            time.sleep(2)
            self.serial_conn.flushInput()
            self.serial_conn.flushOutput()
            if self.handshake():
                self.is_connected = True
                logger.debug("✓ Connection established successfully")
                return True
            else:
                logger.debug("✗ Handshake failed")
                self.disconnect()
                return False
        except serial.SerialException as e:
            logger.debug(f"✗ Serial connection error: {e}")
            return False
        except Exception as e:
            logger.debug(f"✗ Unexpected error: {e}")
            return False
    
    def handshake(self, max_attempts=3):
        for attempt in range(max_attempts):
            try:
                logger.debug(f"Handshake attempt {attempt + 1}/{max_attempts}")
                self.serial_conn.write(self.HANDSHAKE_CMD)
                self.serial_conn.flush()
                response = self.serial_conn.readline().decode().strip()
                if response == "HANDSHAKE_OK":
                    logger.debug("✓ Handshake successful")
                    return True
                else:
                    logger.debug(f"Unexpected handshake response: '{response}'")
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Handshake error: {e}")
                time.sleep(0.5)
        return False
    
    def poll_sensor(self):
        if not self.is_connected:
            logger.debug("Not connected to Arduino")
            return None
        try:
            self.serial_conn.write(self.POLL_CMD)
            self.serial_conn.flush()
            response = self.serial_conn.readline().decode().strip()
            if response in ["WET", "DRY"]:
                return response == "WET"
            else:
                logger.debug(f"Unexpected poll response: '{response}'")
                return None
        except Exception as e:
            logger.debug(f"Poll error: {e}")
            return None

    @run_threaded(attr_name="_monitor_thread")
    def monitor_sensor(self, fn_on_wet=None):
        while not self.monitor_abort_flag.is_set():
            if not self.is_connected:
                logger.debug("Not connected to Arduino")
                return None
            try:
                self.serial_conn.write(self.POLL_CMD)
                self.serial_conn.flush()
                response = self.serial_conn.readline().decode().strip()
                if response in ["WET", "DRY"]:
                    if response == "WET":
                        self.sensor_wet_flag.set()
                        logger.debug("Spill sensor is wet!")
                        if fn_on_wet is not None:
                            fn_on_wet()
                        return
                else:
                    logger.debug(f"Unexpected poll response: '{response}'")
                    # return None
            except Exception as e:
                logger.debug(f"Poll error: {e}")
                # return None
            time.sleep(.2)

    def stop_monitoring(self):
        self.monitor_abort_flag.set()
        try:
            self._monitor_thread.join()
        except:
            pass
    
    def start_logging(self):
        if self.logging_active:
            logger.debug("Logging already active")
            return
        self.logging_active = True
        self.log_thread = threading.Thread(target=self._logging_worker, daemon=True)
        self.log_thread.start()
        logger.debug("✓ Continuous logging started (Ctrl+C to stop)")
    
    def stop_logging(self):
        self.logging_active = False
        if self.log_thread:
            self.log_thread.join(timeout=1)
        logger.debug("✓ Logging stopped")
    
    def _logging_worker(self):
        while self.logging_active and self.is_connected:
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode().strip()
                    if line and line.startswith("LOG:"):
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        logger.debug(f"[{timestamp}] {line}")
                else:
                    time.sleep(0.1)
            except Exception as e:
                if self.logging_active:
                    logger.debug(f"Logging error: {e}")
                break
    
    def disconnect(self):
        self.stop_logging()
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                logger.debug("✓ Disconnected from Arduino")
            except:
                pass
        self.is_connected = False
        self.serial_conn = None


def signal_handler(signum, frame):
    logger.debug("\n\nShutting down...")
    if 'interface' in globals():
        interface.disconnect()
    sys.exit(0)


def main():
    global interface
    signal.signal(signal.SIGINT, signal_handler)
    logger.debug("Arduino Sensor Interface")
    logger.debug("=" * 40)
    interface = ArduinoSensorInterface()
    if not interface.connect():
        logger.debug("Failed to establish connection. Exiting.")
        return
    logger.debug("\nCommands:")
    logger.debug("  'p' or 'poll'    - Poll sensor state once")
    logger.debug("  'l' or 'log'     - Start continuous logging")
    logger.debug("  's' or 'stop'    - Stop continuous logging")
    logger.debug("  'q' or 'quit'    - Quit program")
    logger.debug("  'h' or 'help'    - Show this help")
    try:
        while interface.is_connected:
            command = input("\n> ").strip().lower()
            if command in ['p', 'poll']:
                state = interface.poll_sensor()
                if state is not None:
                    status = "WET" if state else "DRY"
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    logger.debug(f"[{timestamp}] Sensor state: {status}")
                else:
                    logger.debug("Failed to poll sensor")
            elif command in ['l', 'log']:
                interface.start_logging()
            elif command in ['s', 'stop']:
                interface.stop_logging()
            elif command in ['q', 'quit']:
                break
            elif command in ['h', 'help']:
                logger.debug("\nCommands:")
                logger.debug("  'p' or 'poll'    - Poll sensor state once")
                logger.debug("  'l' or 'log'     - Start continuous logging")
                logger.debug("  's' or 'stop'    - Stop continuous logging")
                logger.debug("  'q' or 'quit'    - Quit program")
            elif command == '':
                continue
            else:
                logger.debug(f"Unknown command: '{command}'. Type 'h' for help.")
    except KeyboardInterrupt:
        pass
    finally:
        interface.disconnect()


if __name__ == "__main__":
    main()
