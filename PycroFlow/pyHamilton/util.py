from enum import Enum


class QueryCommandsEnumeration(Enum):
    BUFFER_STATUS = 'F'
    FIRMWARE_VERSION = '&'
    FIRMWARE_CHECKSUM = '#'
    PUMP_STATUS = 'Q'
    ABSOLUTE_SYRINGE_POSITION = '?'
    START_VELOCITY = '?1'
    MAXIMUM_VELOCITY = '?2'
    STOP_VELOCITY = '?3'
    ACTUAL_SYRINGE_POSITION = '?4'
    NUMBER_OF_RETURN_STEPS = '?12'
    STATUS_AUXILIARY_INPUT_1 = '?13'
    STATUS_AUXILIARY_INPUT_2 = '?14'
    RETURNS_255 = '?22'
    NUMBER_OF_BACKOFF_STEPS = '?24'
    SYRINGE_STATUS = '?10000'
    SYRINGE_HOME_SENSOR_STATUS = '?10001'
    SYRINGE_MODE = '?11000'
    VALVE_STATUS = '?20000'
    VALVE_TYPE = '?21000'
    VALVE_LOGICAL_POSITION = '?23000'
    VALVE_NUMERICAL_POSITION = '?24000'
    VALVE_ANGLE = '?25000'
    LAST_DIGITAL_OUT_VALUE = '?37000'
    SYRINGE_DIAGNOSTIC_TIMER_VALUE = '?38000'


class PSDTypes(Enum):
    psd4 = '4'
    psd6 = '6'
    psd4SmoothFlow = '4sf'
    psd6SmoothFlow = '6sf'


class SyringeTypes(Enum):
    syringe12uL = '12.5u'
    syringe25uL = '25u'
    syringe50uL = '50u'
    syringe100uL = '100u'
    syringe125uL = '125u'
    syringe250uL = '250u'
    syringe500uL = '500u'
    syringe1mL = '1.0m'
    syringe2mL = '2.5m'
    syringe5mL = '5.0m'
    syringe10mL = '10m'
    syringe25mL = '25m'
    syringe50mL = '50m'


class SyringeMovement(Enum):
    absoluteMovement = 'absolute'
    relativePickup = 'pickup'
    relativeDispense = 'dispense'
    returnSteps = 'return'
    backoffSteps = 'backoff'


class VelocityTypes(Enum):
    maxVelocity = 'max'
    startVelocity = 'start'
    stopVelocity = 'stop'

