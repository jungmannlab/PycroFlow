"""
illumination.py

Provides illumination functionality to be used as a system
in orchestration.

illumination protocol e.g.
protocol_illumination = [
    {'$type': 'power', 'value': 1},
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'power', 'value': 50},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
]
"""
from PycroFlow.orchestration import AbstractSystem


class IlluminationSystem(AbstractSystem):
    def __init__(self, protocol):
        self.protocol = protocol

    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pass

    def pause_execution(self):
        """Pause protocol execution
        """
        pass

    def resume_execution(self):
        """Resume protocol execution after pausing
        """
        pass

    def abort_execution(self):
        """Abort protocol execution
        """
        pass
