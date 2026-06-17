"""Tests for the Stage 4 typed-entry / signal-registry / ThreadExchange work."""
import threading
import time
import unittest

from PycroFlow.orchestration import (
    ProtocolOrchestrator,
    SignalRegistry,
    ThreadExchange,
    WaitForSignalTimeout,
)
from PycroFlow.orchestration.core import dispatch_entry
from PycroFlow.protocol_entries import (
    IncubateEntry,
    InjectEntry,
    SignalEntry,
    WaitForSignalEntry,
    parse_entry,
)


# ---------- SignalRegistry -----------------------------------------------

class TestSignalRegistry(unittest.TestCase):

    def test_wait_returns_true_after_fire(self):
        reg = SignalRegistry()
        reg.fire('img', 'round 1 done')
        self.assertTrue(reg.wait('img', 'round 1 done', timeout=0.5))

    def test_wait_blocks_until_fire(self):
        reg = SignalRegistry()
        t0 = time.monotonic()

        def producer():
            time.sleep(0.05)
            reg.fire('img', 'round 1 done')

        threading.Thread(target=producer, daemon=True).start()
        self.assertTrue(reg.wait('img', 'round 1 done', timeout=2.0))
        elapsed = time.monotonic() - t0
        # No busy-poll — should fire shortly after the 50 ms sleep, not
        # the previous 50 ms granularity.
        self.assertLess(elapsed, 0.5)

    def test_wait_times_out(self):
        reg = SignalRegistry()
        self.assertFalse(reg.wait('img', 'never', timeout=0.05))

    def test_fire_before_register_is_observed(self):
        reg = SignalRegistry()
        reg.fire('img', 'early')
        # New consumer that didn't pre-register still sees the signal
        # because _get_or_create reuses the existing Event.
        self.assertTrue(reg.is_set('img', 'early'))

    def test_reset_drops_signals(self):
        reg = SignalRegistry()
        reg.fire('img', 'one')
        reg.reset()
        self.assertFalse(reg.is_set('img', 'one'))


# ---------- ThreadExchange ----------------------------------------------

class TestThreadExchange(unittest.TestCase):

    def test_create_has_all_expected_keys(self):
        tx = ThreadExchange.create()
        expected = {
            'fluid_lock', 'fluid', 'fluid_finished', 'fluid_queue',
            'img_lock', 'img', 'img_finished',
            'illu_lock', 'illu', 'illu_finished',
            'start_protocol_flag', 'pause_protocol_flag',
            'abort_protocol_flag', 'abort_flag', 'graceful_stop_flag',
            'signal_registry',
        }
        self.assertEqual(set(tx.keys()), expected)

    def test_instances_do_not_alias(self):
        # Pre-Stage-4 bug: ProtocolOrchestrator.threadexchange was a class
        # attribute, so two orchestrators shared one set of locks/events.
        a = ThreadExchange.create()
        b = ThreadExchange.create()
        self.assertIsNot(a['fluid_lock'], b['fluid_lock'])
        self.assertIsNot(a['abort_flag'], b['abort_flag'])
        self.assertIsNot(a['signal_registry'], b['signal_registry'])

    def test_dict_access_still_works(self):
        # All existing self.txchange['fluid_lock'] sites must keep working.
        tx = ThreadExchange.create()
        self.assertIsInstance(tx['fluid_lock'], type(threading.Lock()))
        self.assertEqual(tx['fluid'], [])
        tx['fluid'].append('hello')
        self.assertEqual(tx['fluid'], ['hello'])

    def test_typed_accessors(self):
        tx = ThreadExchange.create()
        self.assertIs(tx.fluid_lock, tx['fluid_lock'])
        self.assertIs(tx.signal_registry, tx['signal_registry'])
        self.assertIs(tx.abort_flag, tx['abort_flag'])

    def test_orchestrator_uses_per_instance_threadexchange(self):
        from PycroFlow.examples.demo_protocols import protocol
        a = ProtocolOrchestrator(protocol)
        b = ProtocolOrchestrator(protocol)
        self.assertIsNot(a.threadexchange['fluid_lock'],
                         b.threadexchange['fluid_lock'])


# ---------- parse_entry (typed coercion) --------------------------------

class TestParseEntry(unittest.TestCase):

    def test_inject(self):
        e = parse_entry({'$type': 'inject', 'reservoir_id': 1, 'volume': 500})
        self.assertIsInstance(e, InjectEntry)
        self.assertEqual(e.reservoir_id, 1)
        self.assertEqual(e.volume, 500)

    def test_case_insensitive_dispatch(self):
        # Old code did step['$type'].lower(); typed-entry path must match.
        e = parse_entry({'$type': 'Inject', 'reservoir_id': 1, 'volume': 100})
        self.assertIsInstance(e, InjectEntry)

    def test_wait_for_signal_optional_timeout(self):
        e = parse_entry({
            '$type': 'wait for signal',
            'target': 'img', 'value': 'round 1 done', 'timeout': 600,
        })
        self.assertEqual(e.timeout, 600)
        e2 = parse_entry({
            '$type': 'wait for signal', 'target': 'img', 'value': 'round 1 done',
        })
        self.assertIsNone(e2.timeout)

    def test_unknown_type_raises(self):
        with self.assertRaises(KeyError):
            parse_entry({'$type': 'totally-fake'})


# ---------- Stage-4 typed dispatch in run_protocol ----------------------

class _FakeHandler:
    """Stub matching the AbstractSystemHandler surface dispatch_entry uses."""
    def __init__(self):
        self.tx_messages = []
        self.tx_waits = []
        self.exec_calls = []
        self.protocol_iter = 0
        self.txchange = {
            'abort_flag': threading.Event(),
            'abort_protocol_flag': threading.Event(),
        }
    def send_message(self, msg):
        self.tx_messages.append(msg)
    def wait_xchange(self, target, value, timeout=None):
        self.tx_waits.append((target, value, timeout))
    def execute_protocol_entry(self, i):
        self.exec_calls.append(i)


class TestDispatchEntry(unittest.TestCase):

    def test_signal_entry_calls_send_message(self):
        h = _FakeHandler()
        dispatch_entry(SignalEntry(**{'$type': 'signal', 'value': 'fluid round 1 done'}), h)
        self.assertEqual(h.tx_messages, ['fluid round 1 done'])

    def test_wait_for_signal_calls_wait_xchange(self):
        h = _FakeHandler()
        entry = WaitForSignalEntry(**{
            '$type': 'wait for signal', 'target': 'img', 'value': 'round 1 done',
            'timeout': 30.0,
        })
        dispatch_entry(entry, h)
        self.assertEqual(h.tx_waits, [('img', 'round 1 done', 30.0)])

    def test_incubate_busy_waits_then_returns(self):
        h = _FakeHandler()
        entry = IncubateEntry(**{'$type': 'incubate', 'duration': 0.05})
        t0 = time.monotonic()
        dispatch_entry(entry, h)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.05)
        # No exec or message side effects.
        self.assertEqual(h.exec_calls, [])
        self.assertEqual(h.tx_messages, [])

    def test_incubate_aborts_early(self):
        h = _FakeHandler()
        h.txchange['abort_flag'].set()
        entry = IncubateEntry(**{'$type': 'incubate', 'duration': 100.0})
        t0 = time.monotonic()
        dispatch_entry(entry, h)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, "abort flag should short-circuit")

    def test_unknown_typed_entry_falls_through_to_execute(self):
        # Anything not registered uses the default dispatcher, which
        # delegates to execute_protocol_entry. Sanity check with an
        # InjectEntry (no specific handler registered).
        h = _FakeHandler()
        dispatch_entry(
            InjectEntry(**{'$type': 'inject', 'reservoir_id': 1, 'volume': 500}),
            h)
        self.assertEqual(h.exec_calls, [0])


# ---------- send_message integration: fires SignalRegistry --------------

class TestSendMessageFiresRegistry(unittest.TestCase):

    def test_send_message_fires_matching_registry_signal(self):
        # End-to-end: AbstractSystemHandler.send_message must update both
        # the legacy list AND the SignalRegistry so a Stage-4 wait wakes up.
        from PycroFlow.orchestration.core import AbstractSystemHandler

        class _Stub(AbstractSystemHandler):
            target = 'fluid'
            def execute_protocol_entry(self, i):
                pass
            def work_queue(self):
                pass

        tx = ThreadExchange.create()
        handler = _Stub(protocol={'protocol_entries': []}, threadexchange=tx)
        handler.send_message('round 1 done')
        self.assertIn('round 1 done', tx['fluid'])
        self.assertTrue(tx.signal_registry.is_set('fluid', 'round 1 done'))

    def test_prefixed_send_fires_stripped_value_too(self):
        from PycroFlow.orchestration.core import AbstractSystemHandler

        class _Stub(AbstractSystemHandler):
            target = 'fluid'
            def execute_protocol_entry(self, i): pass
            def work_queue(self): pass

        tx = ThreadExchange.create()
        handler = _Stub(protocol={'protocol_entries': []}, threadexchange=tx)
        # Legacy convention: 'fluid round 1 done' should be observable as
        # both the full string and the stripped 'round 1 done' so existing
        # wait_xchange callers that strip the prefix wake up correctly.
        handler.send_message('fluid round 1 done')
        self.assertTrue(tx.signal_registry.is_set('fluid', 'fluid round 1 done'))
        self.assertTrue(tx.signal_registry.is_set('fluid', 'round 1 done'))


if __name__ == '__main__':
    unittest.main()
