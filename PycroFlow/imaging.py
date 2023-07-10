"""
imaging.py

Provides imaging functionality to be used as a system
in orchestration.
"""
from orchestration import AbstractSystem


class ImagingSystem(AbstractSystem):
    def __init__(self):
        pass

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
