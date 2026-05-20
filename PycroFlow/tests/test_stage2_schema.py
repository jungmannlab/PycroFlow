"""Tests for the pydantic protocol schema added in Stage 2."""
import unittest

from PycroFlow.schemas import (
    Protocol,
    SchemaValidationError,
    SubsystemProtocol,
    validate_protocol,
)


class TestProtocolSchema(unittest.TestCase):

    def test_demo_protocol_validates(self):
        from PycroFlow.examples.demo_protocols import protocol
        validate_protocol(protocol)

    def test_unknown_type_raises(self):
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_protocol(
                {'fluid': {'protocol_entries': [{'$type': 'made-up'}]}})
        self.assertIn('made-up', str(ctx.exception))

    def test_missing_required_field_raises(self):
        # 'inject' requires reservoir_id and volume
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_protocol(
                {'fluid': {'protocol_entries': [{'$type': 'inject'}]}})
        msg = str(ctx.exception)
        self.assertIn('reservoir_id', msg)
        self.assertIn('volume', msg)

    def test_extra_fields_allowed(self):
        # Existing protocols carry extra fields (wait_time, round, ...).
        # extra='allow' must keep working so we don't break them.
        validate_protocol({
            'fluid': {'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 500,
                 'wait_time': 5, 'velocity': 200, 'delay': 0},
            ]},
            'img': {'protocol_entries': [
                {'$type': 'acquire', 'frames': 100, 't_exp': 75,
                 'round': 3, 'message': 'R4'},
            ]},
        })

    def test_incubate_accepts_string_duration(self):
        # orchestration.run_protocol coerces via float(), so the wire format
        # has historically been lax. test_protocols.test_06 asserts a string.
        validate_protocol(
            {'fluid': {'protocol_entries': [
                {'$type': 'incubate', 'duration': '120'},
            ]}})

    def test_pump_out_validates(self):
        validate_protocol(
            {'fluid': {'protocol_entries': [
                {'$type': 'pump_out', 'volume': 500},
                {'$type': 'pump_out', 'volume': 100, 'extractionfactor': 2.0},
            ]}})

    def test_wait_for_signal_timeout_optional(self):
        # The Stage 1 wait_xchange timeout key is optional in the wire format.
        validate_protocol(
            {'fluid': {'protocol_entries': [
                {'$type': 'wait for signal', 'target': 'img',
                 'value': 'round 1 done', 'timeout': 600},
                {'$type': 'wait for signal', 'target': 'img',
                 'value': 'round 2 done'},
            ]}})

    def test_subsystems_independent(self):
        # A protocol may include only some subsystems.
        validate_protocol(
            {'img': {'protocol_entries': [
                {'$type': 'acquire', 'frames': 10, 't_exp': 100},
            ]}})

    def test_create_protocol_invokes_validation(self):
        # End-to-end: ProtocolBuilder.create_protocol must call the validator
        # on the produced dict.
        import os
        import tempfile
        from PycroFlow.tests.fixtures.configs.exchange_basic import CONFIG
        from PycroFlow.protocols import ProtocolBuilder
        cfg = dict(CONFIG)
        cfg['save_dir'] = tempfile.mkdtemp(prefix='schema-test-')
        try:
            pb = ProtocolBuilder()
            fname, _ = pb.create_protocol(cfg)
            self.assertTrue(fname.endswith('.yaml'))
        finally:
            import shutil
            shutil.rmtree(cfg['save_dir'], ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
