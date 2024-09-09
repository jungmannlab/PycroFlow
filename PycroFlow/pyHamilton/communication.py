import serial
import time
import logging


logger = logging.getLogger('pyHamilton.communication')

# Global Variables
ser = 0
ComPort = 'COM'

statusBytesInfo = {
    '@': "Pump is busy - no error",
    '`': "Pump is ready - no error",
    'a': "Initialization error – occurs when the pump fails to initialize",
    'b': "Invalid command – occurs when an unrecognized command is used.",
    'c': "Invalid operand – occurs when an invalid parameter is given with a command.",
    'd': "Invalid command sequence – occurs when the command communication protocol is incorrect",
    'f': "EEPROM failure – occurs when the EEPROM is faulty",
    'g': "Syringe not initialized – occurs when the syringe fails to initialize",
    'i': "Syringe overload – occurs when the syringe encounters excessive back pressure.",
    'j': "Valve overload – occurs when the valve drive encounters excessive back pressure.",
    'k': "Syringe move not allowed – when the valve is in the bypass or throughput position, syringe move commands are not allowed.",
    'o': "Pump is busy – occurs when the command buffer is full"
}

def initializeSerial(commPort: int, baudrate: int):
    global ser
    ser = serial.Serial()
    ser.port = ComPort + str(commPort)
    ser.baudrate = baudrate
    ser.bytesize = 8
    ser.parity = 'N'
    ser.stopbits = 1
    ser.xonxoff = False  # disable software flow control
    ser.rtscts = False  # disable hardware (RTS/CTS) flow control
    ser.dsrdtr = False  # disable hardware (DSR/DTR) flow control

    # Specify the TimeOut in seconds, so that SerialPort doesn't hangs
    ser.timeout = 10
    ser.open()  # Opens SerialPort

    # print port open or closed
    if ser.isOpen():
        logger.debug('Open: ' + ser.portstr)

def disconnectSerial():
    if ser.isOpen():
        ser.close()

def encodeCommand(message: str):
    temp = message + '\r\n'
    encoded_temp = str.encode(temp)
    ser.write(encoded_temp)

    respondbytes = ser.readline()  # Read from Serial Port
    decoded_temp = respondbytes.decode()
    logger.debug('Response :' + decoded_temp)

def waitForResponse(header: str, footer: str):
    logger.debug("Waiting for pump status ..")
    while True:
        time.sleep(0.2)
        temp = header + "QR" + footer
        encoded_temp = str.encode(temp)
        ser.write(encoded_temp)
        respond_bytes = ser.readline()
        decoded_temp = respond_bytes.decode()
        time.sleep(0.2)
        responseBit = decoded_temp[2:3]
        logger.debug("Pump status: " + responseBit + ' - ' + statusBytesInfo[responseBit])
        if responseBit == '`':
            break

def sendCommand(pumpAddress: str, message: str, waitForPump=False):
    commandHeader = '/' + pumpAddress
    commandFooter = '\r\n'

    command = commandHeader + message + commandFooter
    logger.debug("Sending command " + command)
    encoded_command = str.encode(command)
    ser.write(encoded_command)
    responseBytes = ser.readline()  # Read from Serial Port
    # response = responseBytes.decode()
    try:
        response = responseBytes.decode()
    except UnicodeDecodeError as e:
        logger.exception(str(e))

    if waitForPump:
        waitForResponse(commandHeader, commandFooter)

    logger.debug('Response :' + response)
    return response