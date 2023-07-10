from pyHamiltonPSD.util import *

class MVP:
    # default address in case that psd has address pin set on 0
    asciiAddress = "1"
    # standard resolution has value 0 = 3000 steps
    resolutionMode = 0

    def __init__(self, address: str, type='MVP'):
        self.setAddress(address)
        self.type = type
        self.setCommandObj()

    def setCommandObj(self):
        if self.type == 'MVP':
            self.command = MVPCommand(type)
        else:
            raise NotImplmentedError('Type {:s} not implemented.'.format(type))

    def setAddress(self, address):
        translateAddress = {
            '0': "1",
            '1': "2",
            '2': "3",
            '3': "4",
            '4': "5",
            '5': "6",
            '6': "7",
            '7': "8",
            '8': "9",
            '9': ":",
            'A': ";",
            'B': "<",
            'C': "=",
            'D': ">",
            'E': "?",
            'F': "@",
        }
        self.asciiAddress = translateAddress.get(address)

    def print(self):
        print("Address: " + self.asciiAddress)
        print("Type: " + str(self.type))
        print("Command object: " + str(self.command))

    def getTypeOfCommand(self, command: str):
        type = 0
        if len(command) > 0:
            if "h" in command:
                print("h factor command")
                type = type | 0b0010
            if "?" in command or "F" in command or "&" in command or \
                "#" in command or "Q" in command:
                print("query command")
                type = type | 0b0100
            if "z" in command or "Z" in command or "Y" in command or "W" in command or \
                "A" in command or "a" in command or "P" in command or "p" in command or \
                "D" in command or "d" in command or "K" in command or "k" in command or \
                "I" in command or "O" in command or "B" in command or "E" in command or \
                "g" in command or "G" in command or "M" in command or "H" in command or \
                "J" in command or "s" in command or "e" in command or "^" in command or \
                "N" in command or "L" in command or "v" in command or "V" in command or \
                "S" in command or "c" in command or "C" in command or "T" in command or \
                "t" in command or "u" in command or "R" in command or "X" in command:
                print("basic command")
                type = type | 0b0001

        if type == 0b0111 or type == 0b0011 or type == 0b0110 or type == 0b0101:
            print("Error! Composed commands are not allowed!")

        return type

    def checkValidity(self, command: str):
        result: bool = False
        countg = command.count("g")
        countG = command.count("G")

        if "cmdError" not in command:
            print("Command " + command + " validity is checked...")
            commandType = self.getTypeOfCommand(command)
            if (commandType == 0b0010 or commandType == 0b0100 or commandType == 0b0001) and \
                    self.checkGgPairs(countg, countG):
                result = True
            else:
                print("The current command is not valid! Please check the manual!")
        else:
            print("The current command is wrong! Please check the manual!")

        return result

    def checkGgPairs(self, command_g, command_G):
        result: bool = False
        g_count = command_g
        G_count = command_G
        temp = g_count
        if G_count == g_count or G_count == temp + 1:
            result = True
        else:
            print("Error! Inconsistent pair of g-G!")

        return result


class MVPCommand:

    def __init__(self, type: str):
        self.type = type

    def initialize(self):
        return self.initializeValve()

    """
    R - Execute Command Buffer
    X - Execute Command Buffer from Beginning
    """
    def executeCommandBuffer(self, type='R'):
        cmd: str = ''
        if type == 'R' or type == 'X':
            cmd += type
        else:
            print("Error! Incorrect type!")
            cmd = 'cmdError'
        return cmd

    '''
        Valve Commands
    '''

# ??? Do I set Input and Output positions for MVP? What about bypass and extra?
    # Ix - Move Valve to Input Position
    def moveValveToInputPosition(self, value=0):
        cmd: str = 'I'
        if value == 0:
            pass
        elif 1 <= value <= 8:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Move valve to input position command!")
            cmd = 'cmdError'
        return cmd

    # Ox - Move Valve to Output Position
    def moveValveToOutputPosition(self, value=0):
        cmd: str = 'O'
        if value == 0:
            pass
        elif 1 <= value <= 8:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Move valve to output position command!")
            cmd = 'cmdError'
        return cmd

    # B - Move Valve to Bypass (Throughput Position)
    def moveValveToBypass(self):
        return 'B'

    # E - Move Valve to Extra Position
    def moveValveToExtraPosition(self):
        return 'E'

    """
        Action commands
    """

    # g - Define a Position in a Command String
    def definePositionInCommandString(self):
        return 'g'

    # Gx - Repeat Commands
    def repeatCommands(self, value=0):
        cmd: str = 'G'
        if value == 0:
            pass
        elif 1 <= value <= 65535:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Repeat Commands command!")
            cmd = 'cmdError'
        return cmd

    # Mx - Delay - performs a delay of x milliseconds.where 5 ≤ x ≤ 30,000 milliseconds.
    def delay(self, value: int):
        cmd: str = 'M'
        if 5 <= value <= 30000:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Delay command!")
            cmd = 'cmdError'
        return cmd

    # Hx - Halt Command Execution
    def halt(self, value: int):
        cmd: str = 'H'
        if 0 <= value <= 2:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Halt command!")
            cmd = 'cmdError'
        return cmd

    # Jx - Auxiliary Outputs
    def auxiliaryOutputs(self, value: int):
        cmd: str = 'J'
        if 0 <= value <= 7:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for Auxiliary Outputs command!")
            cmd = 'cmdError'
        return cmd

    # sx - Store Command String
    def storeCommandString(self, location: int, command: str):
        cmd: str = 's'
        if 0 <= location <= 14:
            cmd += str(location)
            cmd += command
        else:
            print("Invalid value " + str(location) + " for Store Command String command!")
            cmd = 'cmdError'
        return cmd

    # ex - Execute Command String in EEPROM Location
    def executeCommandStringInEEPROMLocation(self, location: int):
        cmd: str = 'e'
        if 0 <= location <= 14:
            cmd += str(location)
        else:
            print("Invalid value " + str(location) + " for Execute Command String in EEPROM Location command!")
            cmd = 'cmdError'
        return cmd

    def terminateCommandBuffer(self):
        return 'T'

    """
        h30001 - Enable h Factor Commands and Queries
        h30000 - Disable h Factor Commands and Queries
    """

    def enableHFactorCommandsAndQueries(self):
        return "h30001"

    def disableHFactorCommandsAndQueries(self):
        return "h30000"

    def reset(self):
        return "h30003"

    def initializeValve(self):
        return "h20000"

    def enableValveMovement(self):
        return "h20001"

    def disableValveMovement(self):
        return "h20002"

    def setValveType(self, type: int):
        cmd: str = 'h2100'
        # permitted values between 0-6
        if 0 <= type <= 6:
            cmd += str(type)
        else:
            print("Invalid valve type " + str(type) + " for Set Valve Type command!")
            cmd = 'cmdError'
        return cmd

    def moveValveToInputPositionInShortestDirection(self):
        return "h23001"

    def moveValveToOutputPositionInShortestDirection(self):
        return "h23002"

    def moveValveToWashPositionInShortestDirection(self):
        return "h23003"

    def moveValveToReturnPositionInShortestDirection(self):
        return "h23004"

    def moveValveToBypassPositionInShortestDirection(self):
        return "h23005"

    def moveValveToExtraPositionInShortestDirection(self):
        return "h23006"

    def moveValveClockwiseDirection(self, position: int):
        cmd: str = 'h2400'
        # permitted values between 1-8
        if 1 <= position <= 8:
            cmd += str(position)
        else:
            print("Invalid position " + str(position) + " for Move Valve in Clockwise Direction command!")
            cmd = 'cmdError'
        return cmd

    def moveValveCounterclockwiseDirection(self, position: int):
        cmd: str = 'h2500'
        # permitted values between 1-8
        if 1 <= position <= 8:
            cmd += str(position)
        else:
            print("Invalid position " + str(position) + " for Move Valve in Counterclockwise Direction command!")
            cmd = 'cmdError'
        return cmd

    def moveValveInShortestDirection(self, position: int):
        cmd: str = 'h2600'
        # permitted values between 1-8
        if 1 <= position <= 8:
            cmd += str(position)
        else:
            print("Invalid position " + str(position) + " for Move Valve in Shortest Direction command!")
            cmd = 'cmdError'
        return cmd

    def angularValveMoveCommandCtr(self, cmdValue: int, incrementWith: int):
        cmd: str = 'h'
        # permitted values between 0-345 incremented by 15
        if 345 >= incrementWith >= 0 == incrementWith % 15:
            cmdValue += incrementWith
            cmd += str(cmdValue)
        else:
            print("Invalid angle value " + str(incrementWith) + " for Angular Valve Move command!")
            cmd = 'cmdError'
        return cmd

    def clockwiseAngularValveMove(self, position: int):
        return self.angularValveMoveCommandCtr(27000, position)

    def counterclockwiseAngularValveMove(self, position: int):
        return self.angularValveMoveCommandCtr(28000, position)

    def shortestDirectAngularValveMove(self, position: int):
        return self.angularValveMoveCommandCtr(29000, position)

    """
        Query Commands
    """

    def commandBufferStatusQuery(self):
        return QueryCommandsEnumeration.BUFFER_STATUS.value

    def pumpStatusQuery(self):
        return QueryCommandsEnumeration.PUMP_STATUS.value

    def firmwareVersionQuery(self):
        return QueryCommandsEnumeration.FIRMWARE_VERSION.value

    def firmwareChecksumQuery(self):
        return QueryCommandsEnumeration.FIRMWARE_CHECKSUM.value

    def statusOfAuxiliaryInput1Query(self):
        return QueryCommandsEnumeration.STATUS_AUXILIARY_INPUT_1.value

    def statusOfAuxiliaryInput2Query(self):
        return QueryCommandsEnumeration.STATUS_AUXILIARY_INPUT_2.value

    def returns255Query(self):
        return QueryCommandsEnumeration.RETURNS_255.value

    def valveStatusQuery(self):
        return QueryCommandsEnumeration.VALVE_STATUS.value

    def valveTypeQuery(self):
        return QueryCommandsEnumeration.VALVE_TYPE.value

    def valveLogicalPositionQuery(self):
        return QueryCommandsEnumeration.VALVE_LOGICAL_POSITION.value

    def valveNumericalPositionQuery(self):
        return QueryCommandsEnumeration.VALVE_NUMERICAL_POSITION.value

    def valveAngleQuery(self):
        return QueryCommandsEnumeration.VALVE_ANGLE.value

    def lastDigitalOutValueQuery(self):
        return QueryCommandsEnumeration.LAST_DIGITAL_OUT_VALUE.value