#!/usr/bin/env python
"""
    PycroFlow/peristaltic_drifton.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Implements communication with the Drifton peristaltic pump.
"""
import serial
import time
import logging
import numpy as np


logger = logging.getLogger('drifton')


class RS485Comm():
    """
    Drifton uses RS485 for communication. Initially, the idea was to
    use the same RS485 bus as for Hamilton devices. However, it turns
    out that while most frame parameters are the same, Drifton uses
    even parity while hamilton uses no parity; thus they need differnt
    buses. We cannot import import PycroFlow.pyHamilton.communication.ser
    and use its encode function, but have to use a parallel bus.
    """
    ser = None
    address = 0

    def __init__(self, port="COM1", baudrate=9600, address=0):
        """
        Args:
            port : str
                the COM port for communication
            baudrate : int
                1200 or 9600
            address : int
                the RS485 device address
        """
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baudrate
        self.ser.bytesize = 8
        self.ser.parity = 'E'
        self.ser.stopbits = 1
        self.ser.xonxoff = False  # disable software flow control
        self.ser.rtscts = False  # disable hardware (RTS/CTS) flow control
        self.ser.dsrdtr = False  # disable hardware (DSR/DTR) flow control

        # Specify the TimeOut in seconds, so that SerialPort doesn't hangs
        self.ser.timeout = 10
        self.ser.open()  # Opens SerialPort

        # print port open or closed
        if self.ser.isOpen():
            logger.debug('Open: ' + self.ser.portstr)

        self.address = address

    def disconnect(self):
        if self.ser.isOpen():
            self.ser.close()

    def encode_command(self, message: str):
        temp = message + '\r\n'
        encoded_temp = str.encode(temp)
        self.ser.write(encoded_temp)

        respondbytes = self.ser.readline()  # Read from Serial Port
        decoded_temp = respondbytes.decode()
        logger.debug('Response :' + decoded_temp)
        return decoded_temp

    def _create_message(self, pdu):
        """
        Create a message to communicate with a device.

        The message format is:
        flag (1 byte) + addr (1 byte) + len (1 byte)
        + pdu (variable length) + fcs (1 byte)

        Args:
            pdu (bytes): The payload (Protocol Data Unit)
                in hexadecimal format.
            addr (int): The address of the device
                (1-30 for specific devices, 31 for broadcast).

        Returns:
            bytes: The constructed message in the specified format.

        Raises:
            ValueError: If the address is not in the valid range (1-31).
            TypeError: If the PDU is not of type bytes.
        """
        # Validate inputs
        if not isinstance(pdu, bytes):
            raise TypeError("PDU must be of type 'bytes'.")
        if not (1 <= self.address <= 31):
            raise ValueError("Address must be in the range 1-31.")

        # Define the fixed message head (flag)
        flag = 0xE9

        # Calculate the length of the PDU
        length = len(pdu)

        # Compute the checksum (FCS) as XOR of addr, length,
        # and each byte in the PDU
        fcs = self.address ^ length
        for byte in pdu:
            fcs ^= byte

        # Construct the message
        message = bytes([flag, self.address, length]) + pdu + bytes([fcs])

        return message

    def _send_and_receive(self, message):
        # Send the message and receive the response
        try:
            # Ensure the serial connection is open
            if not self.ser.is_open:
                self.ser.open()

            # Send the message
            print('sending [hex]', message.hex())
            self.ser.write(message)

            print("successfully sent")

            # Read the response
            # first, read header and message length
            hdr = self.ser.read(3)
            print('header', hdr)
            if hdr == b"":
                raise ValueError(
                    "No response from pump. Is it connected and powered?")
            if hdr[0] != b"\xe9"[0]:
                raise ValueError("Invalid header")
            if hdr[1] != self.address:
                raise ValueError("Invalid RS485 address")
            msglen = hdr[2]
            response = self.ser.read(msglen + 1)
            print('response [hex]', response.hex())

            # Check if a response was received
            if not response:
                raise TimeoutError(
                    "No response received from the device within the "
                    + "timeout period.")
            if len(response) < msglen + 1:  # PDU should be at least 6 bytes
                raise ValueError(
                    "Invalid response length. Expected at least 6 bytes.")

            # Parse the response
            # Response format: flag + addr + len + pdu + fcs
            # Skip the flag (1 byte), addr (1 byte), and len (1 byte)
            response = response[:-1]  # Extract the PDU (excluding FCS)
            return response

        except serial.SerialException as e:
            raise serial.SerialException(f"Serial communication error: {e}")
        finally:
            # Optional: Close the serial connection if you want to
            # release the resource
            # ser.close()
            pass

    def send_message(self, pdu):
        message = self._create_message(pdu)
        response = self._send_and_receive(message)
        return response


class Pump():
    """There should be an abstract pump class; for now this and
    hamilton_components.pump just have the same crucial functions.

    The system was designed for syringe pumps. Therefore, the
    corresponding methods do not completely make sense. For
    integration into the system, however, use the same and configure
    them for desired behaviour:

    The peristaltic pump itself can:
        * set_velocity
        * set_direction
        * start (if calibrated: for given volume?)
        * stop

    Matching syringe pump vocabulary
        * pickup: if role out: start else: nothing
        * dispense: if role out: nothing else: start

    """
    ul_per_rotation = None
    clockwise = True
    default_velocity = None
    calibrated = False
    address = None
    role = "out"
    pump_start_time = 0
    pump_stop_time = 0

    def __init__(
        self, port, baud, address, ul_per_rotation=None,
        role="out", clockwise=True
    ):
        self.rs485 = RS485Comm(port, baud, address)

        if ul_per_rotation is not None:
            self.ul_per_rotation = ul_per_rotation
            self.calibrated = True

        self.role = role
        self.clockwise = clockwise

    def __del__(self):
        self.rs485.disconnect()

    def pickup(self, vol, velocity=None, waitForPump=False):
        """vol and waitForPump are only for consistency with hamilton
        """
        if self.role != "out":
            logger.debug(
                f"Peristaltic pump role {self.role}. "
                + "Not acting on pickup command.")
        else:
            self.start_pump(vol, velocity)
            if waitForPump and (velocity is not None) and self.calibrated:
                self.wait_until_done()

    def dispense(self, vol, velocity=None, waitForPump=False):
        """vol and waitForPump are only for consistency with hamilton
        """
        if self.role == "out":
            logger.debug(
                f"Peristaltic pump role {self.role}. "
                + "Not acting on dispense command.")
        else:
            self.start_pump(vol, velocity)
            if waitForPump and (velocity is not None) and self.calibrated:
                self.wait_until_done()

    def start_pump(self, vol, velocity=None):
        """
        Args:
            vol : float
                volume in ul
            velocity : float
                flow velocity in ul/min
        """
        if velocity is None and self.default_velocity is not None:
            velocity = self.default_velocity

        if self.calibrated and velocity is not None:
            speed = velocity / self.ul_per_rotation  # in rpm
            speed = int(np.round(10 * speed))
            fullspeed = False
        else:
            speed = 0
            fullspeed = True
        self.set_pump_state(
            speed, self.clockwise, run=True, fullspeed=fullspeed)

        if self.calibrated and (velocity is not None):
            pump_duration_s = (vol / velocity) # * 60
            print(f"waiting for {pump_duration_s} s to reach a volume of {vol}")
            self.pump_stop_time = self.pump_start_time + pump_duration_s

    def wait_until_done(self):
        """Wait until the (calibrated)
        """
        if self.calibrated:
            time.sleep(self.pump_stop_time - time.time())
            self.set_pump_state(0, run=False)

    def stop_current_move(self):
        self.set_pump_state(0, run=False)

    def resume_current_move(self):
        raise NotImplementedError()

    def get_current_volume(self):
        return 0

    def set_valve(self, pos, move_now=True):
        pass

    def set_velocity(self, start_velocity, max_velocity, stop_velocity):
        self.default_velocity = max_velocity

    def get_status(self):
        # Test connection
        _ = self.query_pump_state()
        # return OK in accordance with Hamilton
        return ""

    def set_pump_state(self, speed, clockwise=True, run=True, fullspeed=False):
        """
        Set the parameters for the pump and create the corresponding PDU.

        Args:
            speed (int): Desired pump speed in decimal RPM. Must be between
                0 and 65535 (16-bit unsigned integer).
            clockwise (bool, optional): Direction of rotation.
                True for clockwise, False for counterclockwise.
                Defaults to True.
            run (bool, optional): Pump operation state.
                True to run, False to stop.
                Defaults to True.
            fullspeed (bool, optional): Whether the pump should run at
                full speed. True for full speed, False for normal speed.
                Defaults to False.

        Returns:
            bytes: The constructed message ready to be sent to the device.

        Raises:
            ValueError: If the speed is out of range (not between 0 and 65535).
        """
        # Validate speed
        if not (0 <= speed <= 65535):
            raise ValueError(
                "Speed must be between 0 and 65535 (16-bit unsigned integer).")

        # Convert speed to 2 bytes (big endian)
        speed_bytes = speed.to_bytes(2, byteorder="big")

        # Construct the full speed and start/stop byte (1 byte)
        full_speed_start_stop = 0
        if run:
            full_speed_start_stop |= 0b00000001  # Set Bit 0 for run
        if fullspeed:
            full_speed_start_stop |= 0b00000010  # Set Bit 1 for full speed

        # Construct the direction byte (1 byte)
        direction = 1 if clockwise else 0

        # Construct the PDU
        # "WJ" in ASCII: 0x57 0x4A
        pdu = b"WJ" + speed_bytes + bytes([full_speed_start_stop, direction])

        response = self.rs485.send_message(pdu)

        if response != b"WJ":
            raise ValueError(
                f"Pump responded {response.decode()} instead of WJ")
        self.pump_start_time = time.time()

    def query_pump_state(self):
        """
        Query the pump to get its current running state.

        The query PDU is "RJ" in ASCII (0x52 0x4A). The response is expected
        in the same format as the write command:
            - "RJ" in ASCII
            - speed (2 bytes): hex number in decimal RPM
            - full speed and start/stop (1 byte):
                * Bit 0: 1 = run, 0 = stop
                * Bit 1: 1 = full speed, 0 = normal speed
            - direction (1 byte): 1 = clockwise, 0 = counterclockwise

        Returns:
            dict: A dictionary containing the pump's state:
                - speed (int): Current speed in decimal RPM.
                - run (bool): True if the pump is running, False if stopped.
                - fullspeed (bool): True if the pump is running at full speed,
                    False otherwise.
                - clockwise (bool): True if the pump is running clockwise,
                    False otherwise.

        Raises:
            ValueError: If the response format is invalid or incomplete.
            TimeoutError: If no response is received from the device.
        """
        # Construct the query PDU
        pdu = b"RJ"

        response_pdu = self.rs485.send_message(pdu)

        if len(response_pdu) != 6:
            raise ValueError("Invalid PDU in response. Expected 6 bytes.")

        # Extract fields from the PDU
        # "RJ" in ASCII should match the first 2 bytes
        if response_pdu[:2] != b"RJ":
            raise ValueError("Invalid PDU header. Expected 'RJ'.")

        # Extract speed (2 bytes, big-endian)
        speed = int.from_bytes(response_pdu[2:4], byteorder="big")

        # Extract full speed and start/stop byte
        full_speed_start_stop = response_pdu[4]
        run = bool(full_speed_start_stop & 0b00000001)  # Bit 0
        fullspeed = bool(full_speed_start_stop & 0b00000010)  # Bit 1

        # Extract direction byte
        direction = response_pdu[5]
        clockwise = bool(direction)

        # Return the parsed state as a dictionary
        return {
            "speed": speed,
            "run": run,
            "fullspeed": fullspeed,
            "clockwise": clockwise,
        }


if __name__ == "__main__":
    p = Pump("COM5", 1200, 1)
    # To set a pump (addr: 01) to run CW at speed of 50rpm. The message should be:
    p.rs485._send_and_receive(
            bytes([0xE9, 0x01, 0x06, 0x57, 0x4A, 0x01, 0xF4, 0x01, 0x01, 0xEF]))


    # msg = p.rs845._create_message()
