from .commandPSD4 import *
from .commandPSD6 import *
from .commandPSD4SmoothFlow import *
from .commandPSD6SmoothFlow import *


class PSD:
    # default address in case that psd has address pin set on 0
    asciiAddress = "1"
    # standard resolution has value 0 = 3000 steps
    resolutionMode = 0

    def __init__(self, address: str, type: PSDTypes):
        self.setAddress(address)
        self.type = type
        self.setCommandObj()

    def calculateSteps(self):
        if self.type == PSDTypes.psd4SmoothFlow.value:
            self.command.steps = 192000
        elif self.type == PSDTypes.psd6SmoothFlow.value:
            self.command.steps = 384000
        elif self.type == PSDTypes.psd4.value:
            if self.resolutionMode == 0:
                self.command.steps = 3000
            else:
                self.command.steps = 24000
        elif self.type == PSDTypes.psd6.value:
            if self.resolutionMode == 0:
                self.command.steps = 6000
            else:
                self.command.steps = 48000
        else:
            print("Error! Undetermined number of steps!")
            self.command.steps = 0

    def calculateSyringeStroke(self):
        if self.type == PSDTypes.psd4SmoothFlow.value:
            self.command.syringeStroke = 192000
        elif self.type == PSDTypes.psd6SmoothFlow.value:
            self.command.syringeStroke = 384000
        elif self.type == PSDTypes.psd4.value:
            self.command.syringeStroke = 6000
        elif self.type == PSDTypes.psd6.value:
            self.command.syringeStroke = 12000
        else:
            print("Error! Undetermined number of syringe strokes!")
            self.command.syringeStroke = 0

    def setVolume(self, syringeType: str):
        if syringeType == SyringeTypes.syringe12uL.value:
            self.command.maxVolum = 12.5
        elif syringeType == SyringeTypes.syringe25uL.value:
            self.command.maxVolum = 25.0
        elif syringeType == SyringeTypes.syringe50uL.value:
            self.command.maxVolum = 50.0
        elif syringeType == SyringeTypes.syringe100uL.value:
            self.command.maxVolum = 100.0
        elif syringeType == SyringeTypes.syringe125uL.value:
            self.command.maxVolum = 125.0
        elif syringeType == SyringeTypes.syringe250uL.value:
            self.command.maxVolum = 250.0
        elif syringeType == SyringeTypes.syringe500uL.value:
            self.command.maxVolum = 500.0
        elif syringeType == SyringeTypes.syringe1mL.value:
            self.command.maxVolum = 1000.0
        elif syringeType == SyringeTypes.syringe2mL.value:
            self.command.maxVolum = 2500.0
        elif syringeType == SyringeTypes.syringe5mL.value:
            self.command.maxVolum = 5000.0
        elif syringeType == SyringeTypes.syringe10mL.value:
            self.command.maxVolum = 10000.0
        elif syringeType == SyringeTypes.syringe25mL.value:
            self.command.maxVolum = 25000.0
        elif syringeType == SyringeTypes.syringe50mL.value:
            self.command.maxVolum = 50000.0
        else:
            print("Error! A syringe type is need it to be available!")
            self.command.maxVolum = 0.0

    def setCommandObj(self):
        if self.type == PSDTypes.psd6.value:
            self.command = CommandPSD6(type)
        elif self.type == PSDTypes.psd4.value:
            self.command = CommandPSD4(type)
        elif self.type == PSDTypes.psd4SmoothFlow.value:
            self.command = CommandPSD4SmoothFlow(type)
        elif self.type == PSDTypes.psd6SmoothFlow.value:
            self.command = CommandPSD6SmoothFlow(type)
        else:
            self.command = Command(type)

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

    def setResolution(self, newResolutionMode: int):
        self.resolutionMode = newResolutionMode
        self.command.resolution_mode = newResolutionMode

    def print(self):
        print("Address: " + self.asciiAddress)
        print("Type: " + str(self.type))
        print("Resolution mode: " + str(self.resolutionMode))
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