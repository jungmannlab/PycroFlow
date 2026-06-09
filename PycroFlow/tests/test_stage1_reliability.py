"""Tests for Stage 1 reliability fixes.

Covers:
    * ``wait_xchange`` raises ``WaitForSignalTimeout`` on expiry instead of
      hanging.
    * The MM Core lockfile (``mm_lock.MmCoreLock``) refuses double-acquire.
    * The PFS health predicate identifies bad states by set comparison.
"""
import os
import tempfile
import threading
import time
import unittest

from PycroFlow.orchestration import (
    AbstractSystemHandler,
    WaitForSignalTimeout,
    WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT,
)
from PycroFlow.mm_lock import MmCoreLock, MmLockHeld


class _StubHandler(AbstractSystemHandler):
    """Minimal AbstractSystemHandler subclass used to exercise wait_xchange.

    Skips the threading.Thread.start() machinery entirely — we only need
    ``wait_xchange`` to be callable with a populated ``txchange``.
    """
    target = 'fluid'

    def execute_protocol_entry(self, i):
        pass

    def work_queue(self):
        pass


def _make_txchange():
    """Build a minimal threadexchange dict for the stub handler."""
    return {
        'fluid_lock': threading.Lock(),
        'fluid': [],
        'fluid_finished': threading.Event(),
        'img_lock': threading.Lock(),
        'img': [],
        'img_finished': threading.Event(),
        'illu_lock': threading.Lock(),
        'illu': [],
        'illu_finished': threading.Event(),
        'abort_flag': threading.Event(),
        'abort_protocol_flag': threading.Event(),
        'pause_protocol_flag': threading.Event(),
        'start_protocol_flag': threading.Event(),
        'graceful_stop_flag': threading.Event(),
    }


class TestWaitXchangeTimeout(unittest.TestCase):

    def test_raises_on_timeout(self):
        """Typo'd signal value used to hang forever — now raises promptly."""
        txch = _make_txchange()
        handler = _StubHandler(
            protocol={'protocol_entries': []}, threadexchange=txch)
        t0 = time.monotonic()
        with self.assertRaises(WaitForSignalTimeout) as ctx:
            handler.wait_xchange('img', 'never-arrives', timeout=0.2)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, "timeout should fire within 1s")
        self.assertIn('never-arrives', str(ctx.exception))

    def test_signal_arrival_returns_silently(self):
        """When the signal arrives, the call returns without raising."""
        txch = _make_txchange()
        handler = _StubHandler(
            protocol={'protocol_entries': []}, threadexchange=txch)

        def deliver():
            time.sleep(0.05)
            with txch['img_lock']:
                txch['img'].append('round 1 done')

        threading.Thread(target=deliver, daemon=True).start()
        handler.wait_xchange('img', 'round 1 done', timeout=2.0)

    def test_abort_flag_returns_silently(self):
        """Abort flag short-circuits the wait — no exception, no hang."""
        txch = _make_txchange()
        handler = _StubHandler(
            protocol={'protocol_entries': []}, threadexchange=txch)

        def abort():
            time.sleep(0.05)
            txch['abort_flag'].set()

        threading.Thread(target=abort, daemon=True).start()
        handler.wait_xchange('img', 'never-arrives', timeout=10.0)

    def test_default_timeout_is_sane(self):
        """Default (4 hours) covers our longest acquisition."""
        self.assertGreaterEqual(WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT, 3600)
        self.assertLessEqual(WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT, 24 * 3600)


class TestMmCoreLock(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='mmlock-')
        self.path = os.path.join(self.tmpdir, 'mm.lock')

    def tearDown(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        os.rmdir(self.tmpdir)

    def test_acquire_release(self):
        lock = MmCoreLock(path=self.path)
        lock.acquire()
        self.assertTrue(os.path.exists(self.path))
        lock.release()
        self.assertFalse(os.path.exists(self.path))

    def test_double_acquire_raises(self):
        first = MmCoreLock(path=self.path)
        first.acquire()
        try:
            second = MmCoreLock(path=self.path)
            with self.assertRaises(MmLockHeld):
                second.acquire()
        finally:
            first.release()

    def test_context_manager(self):
        with MmCoreLock(path=self.path):
            self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_lockfile_contains_pid(self):
        lock = MmCoreLock(path=self.path)
        lock.acquire()
        try:
            with open(self.path) as f:
                contents = f.read().strip()
            self.assertEqual(contents, str(os.getpid()))
        finally:
            lock.release()

    def test_stale_lock_is_reclaimed(self):
        # A crashed process leaves a lockfile with a now-dead PID. acquire()
        # must reclaim it instead of forcing a manual delete.
        import subprocess
        import sys
        proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        proc.wait()
        with open(self.path, 'w') as f:
            f.write(str(proc.pid))   # PID guaranteed no longer running
        lock = MmCoreLock(path=self.path)
        lock.acquire()               # reclaims, does not raise
        try:
            with open(self.path) as f:
                self.assertEqual(f.read().strip(), str(os.getpid()))
        finally:
            lock.release()

    def test_corrupt_lock_is_reclaimed(self):
        # A garbage/empty lockfile (no readable PID) is treated as stale.
        with open(self.path, 'w') as f:
            f.write('not-a-pid')
        lock = MmCoreLock(path=self.path)
        lock.acquire()
        try:
            self.assertTrue(os.path.exists(self.path))
        finally:
            lock.release()

    def test_live_holder_is_refused(self):
        # A lockfile owned by a live process (here, ourselves) must refuse.
        with open(self.path, 'w') as f:
            f.write(str(os.getpid()))
        lock = MmCoreLock(path=self.path)
        with self.assertRaises(MmLockHeld):
            lock.acquire()


class TestPfsHealthCheck(unittest.TestCase):

    def test_known_bad_status(self):
        from PycroFlow.imaging import _pfs_is_unhealthy
        self.assertTrue(_pfs_is_unhealthy('Failed Focus'))
        self.assertTrue(_pfs_is_unhealthy('Out of Range'))
        self.assertTrue(_pfs_is_unhealthy('failed focus'))  # case-insensitive

    def test_known_good_status(self):
        from PycroFlow.imaging import _pfs_is_unhealthy
        self.assertFalse(_pfs_is_unhealthy('Locked in Focus'))
        self.assertFalse(_pfs_is_unhealthy('Within Range'))

    def test_empty_status(self):
        from PycroFlow.imaging import _pfs_is_unhealthy
        self.assertFalse(_pfs_is_unhealthy(''))
        self.assertFalse(_pfs_is_unhealthy(None))

    def test_unknown_status_falls_back_to_substring(self):
        from PycroFlow.imaging import _pfs_is_unhealthy
        # Unknown status with 'fail' substring should be reported unhealthy
        self.assertTrue(_pfs_is_unhealthy('Custom failure mode 42'))
        # Unknown status without 'fail' should be reported healthy
        self.assertFalse(_pfs_is_unhealthy('Some Unknown Status'))


if __name__ == '__main__':
    unittest.main()
