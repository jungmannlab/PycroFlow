from .command import *


class CommandPSD6SmoothFlow(Command):
    def __init__(self, type):
        super().__init__(type)

    def absolutePosition(self, value):
        # absolute position x where 0 ≤ x ≤ 384000
        cmd: str = 'A'
        if 0 <= value <= 384000:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow absolute position command!")
            cmd = 'cmdError'
        return cmd

    def relativePickup(self, value: int):
        # number of steps x where 0 ≤ x ≤ 384.000
        cmd: str = 'P'
        if 0 <= value <= 384000:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow relative pickup command!")
            cmd = 'cmdError'
        return cmd

    def relativeDispense(self, value: int):
        # number of steps x where 0 ≤ x ≤ 384.000
        cmd: str = 'D'
        if 0 <= value <= 384000:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow relative dispense command!")
            cmd = 'cmdError'
        return cmd

    def returnSteps(self, value: int):
        # Return Steps x where 0 ≤ x ≤ 6.400
        cmd: str = 'K'
        if 0 <= value <= 6400:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow return steps command!")
            cmd = 'cmdError'
        return cmd

    def backoffSteps(self, value: int):
        # Back-off Steps x where 0 ≤ x ≤ 12.800
        cmd: str = 'k'
        if 0 <= value <= 12800:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow backoff steps command!")
            cmd = 'cmdError'
        return cmd

    """
        Motor Commands
    """

    def setStartVelocity(self, value: int):
        cmd: str = 'v'
        if 50 <= value <= 800:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow start velocity command!")
            cmd = 'cmdError'
        return cmd

    def setMaximumVelocity(self, value: int):
        cmd: str = 'V'
        if 2 <= value <= 3400:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow set maximum velocity command!")
            cmd = 'cmdError'
        return cmd

    def stopVelocity(self, value: int):
        cmd: str = 'c'
        if 50 <= value <= 1700:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow stop velocity command!")
            cmd = 'cmdError'
        return cmd

    # sets the maximum velocity in μsteps/minute.
    def setMaximumMicroStepVelocity(self, value):
        cmd: str = 'u'
        if 400 <= value <= 816000:
            cmd += str(value)
        else:
            print("Invalid value " + str(value) + " for PSD6 smooth flow set maximum microstep velocity command!")
            cmd = 'cmdError'
        return cmd

    # sets the maximum velocity in μsteps/minute using as parameter microliters.
    def setMaximumMicroStepVelocityPerMinute(self, flowrate: float):
        cmd: str = 'u'
        calculatedValue: int = self.calculateParameterU(flowrate)
        if 400 <= calculatedValue <= 816000:
            cmd += str(calculatedValue)
        else:
            print("Invalid value \"" + str(calculatedValue) + "\" for PSD6 smooth flow set maximum microstep velocity command!")
            cmd = 'cmdError'
        return cmd

    # test with a ps6 sf pump
    def stopCommandBuffer(self):
        return 't'