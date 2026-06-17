"""
imaging.py

Provides imaging functionality to be used as a system
in orchestration.

imaging config e.g.
flow_acq_config = {
    'save_dir': r'Z://users//grabmayr//microscopy_data',
    'base_name': 'AutomationTest_R2R4',
    'imaging_settings': {
        'frames': 50000,
        't_exp': 100,  # in ms
        'ROI': [512, 512, 512, 512],},
    'mm_parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
}

imaging protocol e.g.
protocol_imaging = [
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'acquire', 'frames': 10000, 't_exp': 100, 'message': 'round_1'},
    {'$type': 'signal', 'value': 'imaging round 1 done'},
]

"""

import os
import threading
import time

# import ic
from datetime import datetime

import pandas as pd

# import logging
from loguru import logger
from pycromanager import Acquisition, multi_d_acquisition_events

from PycroFlow.mm_lock import MmCoreLock
from PycroFlow.orchestration import AbstractSystem

# services.mm_core supersedes PyMgrSingleton — Stage 5's in-process Qt GUI
# needs a single shared Core that monet can also see. Use the new accessor
# transparently here so the old singleton stays valid as a fallback.
from PycroFlow.services import mm_core as _mm_core
from PycroFlow.util import (
    ProgressBar,  # PyMgrSingleton kept for back-compat
    PyMgrSingleton,
)

# Nikon PFS statuses that mean focus is lost or unrecoverable. Comparison is
# case-insensitive. If the vendor returns a status not in either set we fall
# back to a substring 'fail' check and emit a WARNING so the new string gets
# noticed.
_PFS_BAD_STATUSES = frozenset(
    s.lower()
    for s in (
        "Failed Focus",
        "Out of Range",
        "Searching",
        "No IR Signal",
        "Defocus",
    )
)
_PFS_OK_STATUSES = frozenset(
    s.lower()
    for s in (
        "Locked in Focus",
        "Within Range",
        "Focusing",
        "Stationary",
    )
)


def _pfs_is_unhealthy(pfs_status):
    """Return True iff ``pfs_status`` indicates focus is lost.

    Replaces the previous ``"failed" in pfs_status.lower()`` substring check
    that misses other bad states. Unknown statuses fall back to the substring
    rule + WARNING so the situation surfaces in logs.
    """
    if not pfs_status:
        return False
    s = pfs_status.lower()
    if s in _PFS_BAD_STATUSES:
        return True
    if s in _PFS_OK_STATUSES:
        return False
    logger.warning(
        "PFS returned an unrecognized status {!r}; falling back to "
        "'fail' substring check. Consider adding it to _PFS_BAD_STATUSES "
        "or _PFS_OK_STATUSES.".format(pfs_status)
    )
    return "fail" in s


# logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


class ImagingSystem(AbstractSystem):
    def __init__(self, config):
        self.config = config

        # Acquire the single-process MM Core lock BEFORE touching Core/Studio.
        # If a separately-run monet GUI (or another PycroFlow instance) is
        # already attached, MmLockHeld is raised here with a clear message
        # instead of producing a silently broken second connection. Stage 5's
        # in-process Qt GUI removes the need for this guard by sharing the
        # Core within one process; the lock is still useful in the two-
        # process world.
        self._mm_lock = MmCoreLock()
        self._mm_lock.acquire()

        self.core = _mm_core.get_core()
        self.studio = _mm_core.get_studio()

        self.handler_ref = None

        # Within-acquisition progress (for the GUI step bar): frames acquired
        # so far / total, and whether an acquisition is currently running.
        self.curr_frame = 0
        self.curr_n_frames = 0
        self.acquiring = False

        # PFS logging
        # self.pfs_pars = {  # for Mercury
        #     'tag_pfs': 'TIPFSOffset',
        #     'tag_zdrive': 'TIZDrive',
        #     'tag_status': 'TIPFSStatus',
        #     'prop_state': 'State',
        #     'prop_status': 'Status',
        #     'deltat': 10}
        # self.pfs_pars = {  # for Crick
        #     'tag_pfs': 'PFS',
        #     'tag_zdrive': 'ZDrive',
        #     'tag_status': 'PFS',
        #     'prop_state': 'PFS in Range',
        #     'prop_status': 'PFS Status',
        #     'deltat': 10}

        self.pfs_pars = config["pfs_pars"]
        self.pfs_log = pd.DataFrame(
            {
                "datetime": [datetime.now()],
                "frame": [0],
                # 'pfs': [self.core.get_position(self.pfs_pars['tag_pfs'])],
                "zdrive": [
                    self.core.get_position(self.pfs_pars["tag_zdrive"])
                ],
                "status": [
                    self.core.get_property(
                        self.pfs_pars["tag_status"],
                        self.pfs_pars["prop_status"],
                    )
                ],
                "state": [
                    self.core.get_property(
                        self.pfs_pars["tag_status"],
                        self.pfs_pars["prop_state"],
                    )
                ],
            }
        )

        self.create_savedir()
        self.create_starttime()

        # test whether all is set up correctly
        self.test_acquisition()

        self.acq_lock = threading.Lock()
        self.acq_pause = threading.Event()
        self.acq_abort = threading.Event()

        logger.debug("Imaging system is set up and ready.")

    def create_savedir(self):
        # Target folder = save_dir / base_name. If it already exists, append
        # the first free "_1", "_2", … suffix so a re-run never collides with
        # (or writes into) a previous run's folder, and never raises.
        base = os.path.join(self.config["save_dir"], self.config["base_name"])
        sdir = base
        n = 0
        while os.path.exists(sdir):
            n += 1
            sdir = "{}_{:d}".format(base, n)
        os.makedirs(sdir)
        self.config["save_dir"] = sdir

    def create_starttime(self):
        self.starttime_str = datetime.now().strftime("_%y-%m-%d_%H%M")

    def _assign_protocol(self, protocol):
        self.protocol = protocol

    def execute_protocol_entry(self, i):
        """execute protocol entry i"""
        pentry = self.protocol["protocol_entries"][i]
        if pentry["$type"] == "acquire":
            logger.debug(
                "executing protocol entry {:d}: {:s}".format(i, str(pentry))
            )
            acquisition_config = self.config.copy()
            if pentry.get("frames"):
                acquisition_config["frames"] = pentry["frames"]
            if pentry.get("t_exp"):
                acquisition_config["t_exp"] = pentry["t_exp"]
            # acq_name = (
            #     acquisition_config['base_name']
            #     + self.starttime_str
            #     + '_prtclstep{:d}_{:s}'.format(
            #         i, pentry['message']))
            acq_name = "prtclstep{:d}_{:s}".format(i, pentry["message"])

            # self.record_movie(acq_name, acquisition_config)
            self.do_all_recodrings(acq_name, acquisition_config)

            logger.debug("done executing protocol entry {:d}".format(i))

    def do_all_recodrings(self, acq_name, acquisition_config):
        """Do all the recordings between liquid exchanges.

        For now, this may mean moving between different positions, if the
        ``use_positions`` flag in the acquisition config is set. A DNA-PAINT
        movie is recorded in each iteration.

        Parameters
        ----------
        acq_name : str
            The name of the acquisition.
        acquisition_config : dict
            The parameters for the acquisition.
        """
        if not self.config.get("use_positions", False):
            self.record_movie(acq_name, acquisition_config)
        else:
            pos_list = (
                self.studio.get_position_list_manager().get_position_list()
            )
            for i in range(pos_list.get_number_of_positions()):
                pos = pos_list.get_position(i)
                logger.debug("moving to position {:d}".format(i))
                pos.go_to_position(pos, self.core)
                self.core.set_property(
                    self.pfs_pars["tag_status"],
                    self.pfs_pars["prop_state"],
                    "On",
                )
                acq_name_p = acq_name + "_pos{:d}".format(i)  # + str(pos)
                self.record_movie(acq_name_p, acquisition_config)

    def pause_execution(self):
        """Pause protocol execution.

        Sets the shared ``acq_pause`` event consumed by the per-frame callback
        ``image_process_fn`` so the next frame loop iteration parks until the
        flag clears.
        """
        logger.debug("Imaging system sets its own pause flag")
        self.acq_pause.set()

    def resume_execution(self):
        """Resume protocol execution after pausing.

        Returns True so :meth:`AbstractSystemHandler.housekeeping` exits the
        pause loop. Returning None (the previous behavior) was a bug: the
        housekeeping loop treats falsy returns as "still paused" and re-enters
        the pause branch forever.
        """
        logger.debug("Imaging system clears its own pause flag")
        self.acq_pause.clear()
        return True

    def abort_execution(self):
        """Abort protocol execution"""
        logger.debug("setting abort flag")
        self.acq_abort.set()

    def close(self):
        """Release the MM Core lock and any other process-level resources.

        Call this when the imaging system is no longer needed (e.g. on CLI
        exit) so a subsequent monet session can attach. The lock also
        releases on GC via :meth:`MmCoreLock.__del__`, but explicit cleanup
        is preferred.
        """
        try:
            self._mm_lock.release()
        except Exception as exc:
            logger.warning("MM Core lock release failed: {!r}".format(exc))

    def check_finished(self):
        pass
        # if self.acq_lock:
        #     if self.acq_th.i == self.acq_th.n - 1:
        #         self.acq_th.join()

    def test_acquisition(self):
        # test the possibility to acquire (fail early)
        if self.studio.live().is_live_mode_on():
            self.studio.live().set_live_mode_on(False)
        events = multi_d_acquisition_events(
            num_time_points=10, time_interval_s=0, channel_exposures_ms=[100]
        )
        with Acquisition(
            directory=self.config["save_dir"],
            name="testacquisition",
            show_display=False,
            debug=False,
        ) as acq:
            acq.acquire(events)

    def record_movie(self, acq_name, acquisition_config):
        """Record a movie via pycromanager.

        Parameters
        ----------
        acq_name : str
            The name of the acquisition; pycromanager creates the
            respective directory.
        acquisition_config : dict
            The acquisition configuration, comprising the following keys:

            ``save_dir``
                The directory to save the acquisition in.
            ``frames``
                The number of frames to acquire.
            ``t_exp``
                The exposure time.
        """
        acq_dir = acquisition_config["save_dir"]
        n_frames = acquisition_config["frames"]
        t_exp = acquisition_config["t_exp"]
        # chan_group = self.protocol['parameters']['channel_group']
        # filter = self.protocol['parameters']['filter']
        # roi = self.protocol['parameters']['ROI']

        # record PFS locations
        self.pfs_log = pd.DataFrame(
            columns=["datetime", "frame", "pfs", "state", "status", "zdrive"],
            index=range(int(acquisition_config["frames"] / 100)),
        )
        self.curr_frame = 0
        self.curr_n_frames = n_frames
        self.acquiring = True
        self.is_out_of_focus = False

        self.core.set_exposure(t_exp)
        self.studio.get_application().refresh_gui()
        if self.protocol["parameters"].get("show_progress"):
            self.probar = ProgressBar("Acquisition", n_frames)
        with Acquisition(
            directory=acq_dir,
            name=acq_name,
            show_display=self.protocol["parameters"].get("show_display", True),
            image_process_fn=self.image_process_fn,
        ) as acq:
            events = multi_d_acquisition_events(
                num_time_points=n_frames,
                time_interval_s=0,  # t_exp/1000,
                # channel_group=chan_group, channels=[filter],
                channel_exposures_ms=[t_exp],
                order="tcpz",
            )
            acq.acquire(events)
            if self.protocol["parameters"].get("show_display", True):
                try:
                    viewer = acq.get_viewer()
                except Exception:
                    viewer = None
                    pass
        time.sleep(0.2)
        if viewer is not None and self.protocol["parameters"].get(
            "close_display_after_acquisition", True
        ):
            viewer.close()
        self.acquiring = False
        self.pfs_log.to_excel(os.path.join(acq_dir, acq_name + "_pfs.xlsx"))
        if self.protocol["parameters"].get("show_progress"):
            self.probar.end_progress()
        logger.debug("acquired all images of {:s}".format(acq_name))

    def get_step_progress(self):
        """Frames acquired so far within the current acquisition.

        Returns
        -------
        tuple or None
            ``(current_frame, total_frames, 'frames')`` while acquiring,
            else ``None``.
        """
        if self.acquiring and self.curr_n_frames:
            return (self.curr_frame, self.curr_n_frames, "frames")
        return None

    def image_process_fn(self, img, meta, event_queue):
        if self.protocol["parameters"].get("show_progress"):
            try:
                self.probar.progress_increment()
            except Exception as e:
                print(e)
        # log PFS position
        if self.curr_frame % 100 == 0:
            pfs_state = self.core.get_property(
                self.pfs_pars["tag_status"], self.pfs_pars["prop_state"]
            )
            pfs_status = self.core.get_property(
                self.pfs_pars["tag_status"], self.pfs_pars["prop_status"]
            )
            self.pfs_log.loc[int(self.curr_frame / 100)] = {
                "datetime": datetime.now(),
                "frame": self.curr_frame,
                # 'pfs': self.core.get_position(self.pfs_pars['tag_pfs']),
                "zdrive": self.core.get_position(self.pfs_pars["tag_zdrive"]),
                "state": pfs_state,
                "status": pfs_status,
            }
            if (
                _pfs_is_unhealthy(pfs_status)
                and (self.handler_ref is not None)
                and (not self.is_out_of_focus)
            ):
                self.is_out_of_focus = (
                    True  # make sure to only pause and rewind once
                )
                self.handler_ref.pause_protocol()
                self.handler_ref.change_protocol_iteration(-1)
                print(
                    "Pausing protocol because PFS is off. Rewinding protocol \
                    for img to re-acquire, but this acquisition will continue \
                    to the end. Execute 'resume_protocol' when ready."
                )
                # abort the acquisition
                event_queue.put(None)

        # should the acquisition be aborted?
        if self.acq_abort.is_set():
            # abort the acquisition
            event_queue.put(None)
        # if the acquisition is paused, go into a loop
        t_pause_start = time.time()
        i_pause = 0
        while self.acq_pause.is_set():
            if i_pause == 0:
                print("Pausing acquisition.")
            i_pause += 1
            # abort the acquisition if it additionally gets aborted
            if self.acq_abort.is_set():
                # abort the acquisition
                event_queue.put(None)
            time.sleep(0.05)
        if i_pause > 0:
            t_pause = time.time() - t_pause_start
            print(f"resuming after {t_pause:.1f} s of pausing.")

        self.curr_frame += 1

        return (img, meta)

    def record_movie_in_thread(self, acq_name, acquisition_config):
        """Record a movie via pycromanager in a separate thread.

        Parameters
        ----------
        acq_name : str
            The name of the acquisition; pycromanager creates the
            respective directory.
        acquisition_config : dict
            The acquisition configuration, comprising the following keys:

            ``save_dir``
                The directory to save the acquisition in.
            ``frames``
                The number of frames to acquire.
            ``t_exp``
                The exposure time.
        """
        acq_dir = acquisition_config["save_dir"]
        n_frames = acquisition_config["frames"]
        t_exp = acquisition_config["t_exp"]
        # chan_group = acquisition_config['mm_parameters']['channel_group']
        # filter = acquisition_config['mm_parameters']['filter']
        # roi = acquisition_config['ROI']

        acq_th = AcquisitionThread(
            self.acq_lock,
            self.acq_pause,
            self.acq_abort,
            acq_dir,
            acq_name,
            n_frames,
            t_exp,
        )
        acq_th.run()


class AcquisitionThread(threading.Thread):
    def __init__(
        self,
        acq_lock,
        acq_pause,
        acq_abort,
        acq_dir,
        acq_name,
        n_frames,
        t_exp,
    ):
        self.lock = acq_abort
        self.ev_pause = acq_pause
        self.ev_abort = acq_abort
        self.acq_dir = acq_dir
        self.acq_name = acq_name
        self.n_frames = n_frames
        self.t_exp = t_exp
        self.i = 0
        self.n = 0
        self.core = PyMgrSingleton.get_core()

    def run(self):
        self.core.set_exposure(self.t_exp)
        self.studio.get_application().refresh_gui()
        with Acquisition(
            directory=self.acq_dir,
            name=self.acq_name,
            show_display=True,
            pre_hardware_hook_fn=self.hook_fn,
        ) as acq:
            events = multi_d_acquisition_events(
                num_time_points=self.n_frames,
                time_interval_s=0,  # t_exp/1000,
                # channel_group=chan_group, channels=[filter],
                channel_exposures_ms=[self.t_exp],
                order="tcpz",
            )
            self.n = len(events)
            acq.acquire(events)

    def hook_fn(self):
        self.abort_on_event()
        self.pause_on_event()
        self.i += 1

    def pause_on_event(self):
        while self.ev_pause.is_set():
            time.sleep(0.02)
            self.abort_on_event()

    def abort_on_event(self):
        if self.ev_abort.is_set():
            raise Exception("Aborting acquisition due to abort hook")
