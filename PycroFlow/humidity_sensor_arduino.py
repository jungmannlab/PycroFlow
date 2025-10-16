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
        
    def find_arduino_port(self):
        arduino_ports = []
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if any(identifier in port.description.lower() for identifier in 
                   ['arduino', 'uno', 'ch340', 'ftdi', 'usb serial']):
                arduino_ports.append(port.device)
        if arduino_ports:
            print(f"Found potential Arduino ports: {arduino_ports}")
            return arduino_ports[0]
        else:
            print("No Arduino ports automatically detected")
            print("Available ports:")
            for port in ports:
                print(f"  {port.device}: {port.description}")
            return None
    
    def connect(self):
        if not self.port:
            self.port = self.find_arduino_port()
            if not self.port:
                print("Please specify port manually")
                return False
        try:
            print(f"Connecting to {self.port} at {self.baud_rate} baud...")
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )
            print("Waiting for Arduino to initialize...")
            time.sleep(2)
            self.serial_conn.flushInput()
            self.serial_conn.flushOutput()
            if self.handshake():
                self.is_connected = True
                print("✓ Connection established successfully")
                return True
            else:
                print("✗ Handshake failed")
                self.disconnect()
                return False
        except serial.SerialException as e:
            print(f"✗ Serial connection error: {e}")
            return False
        except Exception as e:
            print(f"✗ Unexpected error: {e}")
            return False
    
    def handshake(self, max_attempts=3):
        for attempt in range(max_attempts):
            try:
                print(f"Handshake attempt {attempt + 1}/{max_attempts}")
                self.serial_conn.write(self.HANDSHAKE_CMD)
                self.serial_conn.flush()
                response = self.serial_conn.readline().decode().strip()
                if response == "HANDSHAKE_OK":
                    print("✓ Handshake successful")
                    return True
                else:
                    print(f"Unexpected handshake response: '{response}'")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Handshake error: {e}")
                time.sleep(0.5)
        return False
    
    def poll_sensor(self):
        if not self.is_connected:
            print("Not connected to Arduino")
            return None
        try:
            self.serial_conn.write(self.POLL_CMD)
            self.serial_conn.flush()
            response = self.serial_conn.readline().decode().strip()
            if response in ["WET", "DRY"]:
                return response == "WET"
            else:
                print(f"Unexpected poll response: '{response}'")
                return None
        except Exception as e:
            print(f"Poll error: {e}")
            return None
    
    def start_logging(self):
        if self.logging_active:
            print("Logging already active")
            return
        self.logging_active = True
        self.log_thread = threading.Thread(target=self._logging_worker, daemon=True)
        self.log_thread.start()
        print("✓ Continuous logging started (Ctrl+C to stop)")
    
    def stop_logging(self):
        self.logging_active = False
        if self.log_thread:
            self.log_thread.join(timeout=1)
        print("✓ Logging stopped")
    
    def _logging_worker(self):
        while self.logging_active and self.is_connected:
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode().strip()
                    if line and line.startswith("LOG:"):
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        print(f"[{timestamp}] {line}")
                else:
                    time.sleep(0.1)
            except Exception as e:
                if self.logging_active:
                    print(f"Logging error: {e}")
                break
    
    def disconnect(self):
        self.stop_logging()
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                print("✓ Disconnected from Arduino")
            except:
                pass
        self.is_connected = False
        self.serial_conn = None

def signal_handler(signum, frame):
    print("\n\nShutting down...")
    if 'interface' in globals():
        interface.disconnect()
    sys.exit(0)

def main():
    global interface
    signal.signal(signal.SIGINT, signal_handler)
    print("Arduino Sensor Interface")
    print("=" * 40)
    interface = ArduinoSensorInterface()
    if not interface.connect():
        print("Failed to establish connection. Exiting.")
        return
    print("\nCommands:")
    print("  'p' or 'poll'    - Poll sensor state once")
    print("  'l' or 'log'     - Start continuous logging")
    print("  's' or 'stop'    - Stop continuous logging")
    print("  'q' or 'quit'    - Quit program")
    print("  'h' or 'help'    - Show this help")
    try:
        while interface.is_connected:
            command = input("\n> ").strip().lower()
            if command in ['p', 'poll']:
                state = interface.poll_sensor()
                if state is not None:
                    status = "WET" if state else "DRY"
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] Sensor state: {status}")
                else:
                    print("Failed to poll sensor")
            elif command in ['l', 'log']:
                interface.start_logging()
            elif command in ['s', 'stop']:
                interface.stop_logging()
            elif command in ['q', 'quit']:
                break
            elif command in ['h', 'help']:
                print("\nCommands:")
                print("  'p' or 'poll'    - Poll sensor state once")
                print("  'l' or 'log'     - Start continuous logging")
                print("  's' or 'stop'    - Stop continuous logging")
                print("  'q' or 'quit'    - Quit program")
            elif command == '':
                continue
            else:
                print(f"Unknown command: '{command}'. Type 'h' for help.")
    except KeyboardInterrupt:
        pass
    finally:
        interface.disconnect()

if __name__ == "__main__":
    main()
