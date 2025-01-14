import logging
import os
import select
import time
from threading import Event as TEvent

import evdev

from hhd.controller import Multiplexer
from hhd.controller.physical.evdev import B as EC
from hhd.controller.physical.evdev import GenericGamepadEvdev
from hhd.controller.physical.imu import CombinedImu, HrtimerTrigger
from hhd.controller.physical.rgb import LedDevice
from hhd.controller.virtual.uinput import UInputDevice
from hhd.plugins import Config, Context, Emitter, get_outputs, get_gyro_state

from .const import (
    BTN_MAPPINGS,
    DEFAULT_MAPPINGS,
)

ERROR_DELAY = 1
SELECT_TIMEOUT = 1

logger = logging.getLogger(__name__)


GAMEPAD_VID = 0x045E
GAMEPAD_PID = 0x028E

KBD_VID = 0x0001
KBD_PID = 0x0001

BACK_BUTTON_DELAY = 0.1


def plugin_run(
    conf: Config,
    emit: Emitter,
    context: Context,
    should_exit: TEvent,
    updated: TEvent,
    dconf: dict,
):
    first = True
    while not should_exit.is_set():
        if conf["controller_mode.mode"].to(str) == "disabled":
            time.sleep(ERROR_DELAY)
            continue

        found_gamepad = False
        try:
            for d in evdev.list_devices():
                dev = evdev.InputDevice(d)
                if dev.info.vendor == GAMEPAD_VID and dev.info.product == GAMEPAD_PID:
                    found_gamepad = True
                    break
        except Exception:
            logger.warning("Failed finding device, skipping check.")
            found_gamepad = True

        if not found_gamepad:
            if first:
                logger.info("Controller not found. Waiting...")
            time.sleep(ERROR_DELAY)
            first = False
            continue

        # Use the oxp-platform driver if available
        if os.path.exists("/sys/devices/platform/oxp-platform/tt_toggle"):
            try:
                with open("/sys/devices/platform/oxp-platform/tt_toggle", "w") as f:
                    f.write("1")
                logger.info(f"Turbo button takeover enabled")
            except Exception:
                logger.warn(
                    f"Turbo takeover failed. Ensure you have the latest oxp-sensors driver installed."
                )

        try:
            logger.info("Launching emulated controller.")
            updated.clear()
            controller_loop(conf.copy(), should_exit, updated, dconf)
        except Exception as e:
            logger.error(f"Received the following error:\n{type(e)}: {e}")
            logger.error(
                f"Assuming controllers disconnected, restarting after {ERROR_DELAY}s."
            )
            first = True
            # Raise exception
            if conf.get("debug", False):
                raise e
            time.sleep(ERROR_DELAY)


def controller_loop(conf: Config, should_exit: TEvent, updated: TEvent, dconf: dict):
    debug = conf.get("debug", False)

    # Output
    d_producers, d_outs, d_params = get_outputs(
        conf["controller_mode"],
        None,
        conf["imu"].to(bool),
    )

    # Imu
    d_imu = CombinedImu(
        conf["imu_hz"].to(int),
        get_gyro_state(conf["imu_axis"], dconf.get("mapping", DEFAULT_MAPPINGS)),
    )
    d_timer = HrtimerTrigger(conf["imu_hz"].to(int), [HrtimerTrigger.IMU_NAMES])

    # Inputs
    d_xinput = GenericGamepadEvdev(
        vid=[GAMEPAD_VID],
        pid=[GAMEPAD_PID],
        # name=["Generic X-Box pad"],
        capabilities={EC("EV_KEY"): [EC("BTN_A")]},
        required=True,
        hide=True,
    )

    d_kbd_1 = GenericGamepadEvdev(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=dconf.get("btn_mapping", BTN_MAPPINGS),
    )

    multiplexer = Multiplexer(
        trigger="analog_to_discrete",
        dpad="analog_to_discrete",
        share_to_qam=conf["share_to_qam"].to(bool),
        nintendo_mode=conf["nintendo_mode"].to(bool),
    )

    d_volume_btn = UInputDevice(
        name="Handheld Daemon Volume Keyboard",
        phys="phys-hhd-vbtn",
        capabilities={EC("EV_KEY"): [EC("KEY_VOLUMEUP"), EC("KEY_VOLUMEDOWN")]},
        btn_map={
            "key_volumeup": EC("KEY_VOLUMEUP"),
            "key_volumedown": EC("KEY_VOLUMEDOWN"),
        },
        pid=KBD_PID,
        vid=KBD_VID,
        output_timestamps=True,
    )

    d_rgb = LedDevice()
    if d_rgb.supported:
        logger.info(f"RGB Support activated through kernel driver.")

    REPORT_FREQ_MIN = 25
    REPORT_FREQ_MAX = 400

    if conf["imu"].to(bool):
        REPORT_FREQ_MAX = max(REPORT_FREQ_MAX, conf["imu_hz"].to(float))

    REPORT_DELAY_MAX = 1 / REPORT_FREQ_MIN
    REPORT_DELAY_MIN = 1 / REPORT_FREQ_MAX

    fds = []
    devs = []
    fd_to_dev = {}

    def prepare(m):
        devs.append(m)
        fs = m.open()
        fds.extend(fs)
        for f in fs:
            fd_to_dev[f] = m

    try:
        # d_vend.open()
        prepare(d_xinput)
        if conf.get("imu", False):
            start_imu = True
            if dconf.get("hrtimer", False):
                start_imu = d_timer.open()
            if start_imu:
                prepare(d_imu)
        prepare(d_volume_btn)
        prepare(d_kbd_1)
        for d in d_producers:
            prepare(d)

        logger.info("Emulated controller launched, have fun!")
        while not should_exit.is_set() and not updated.is_set():
            start = time.perf_counter()
            # Add timeout to call consumers a minimum amount of times per second
            r, _, _ = select.select(fds, [], [], REPORT_DELAY_MAX)
            evs = []
            to_run = set()
            for f in r:
                to_run.add(id(fd_to_dev[f]))

            for d in devs:
                if id(d) in to_run:
                    evs.extend(d.produce(r))

            evs = multiplexer.process(evs)
            if evs:
                if debug:
                    logger.info(evs)

                d_volume_btn.consume(evs)
                d_xinput.consume(evs)

            d_rgb.consume(evs)
            for d in d_outs:
                d.consume(evs)

            # If unbounded, the total number of events per second is the sum of all
            # events generated by the producers.
            # For Legion go, that would be 100 + 100 + 500 + 30 = 730
            # Since the controllers of the legion go only update at 500hz, this is
            # wasteful.
            # By setting a target refresh rate for the report and sleeping at the
            # end, we ensure that even if multiple fds become ready close to each other
            # they are combined to the same report, limiting resource use.
            # Ideally, this rate is smaller than the report rate of the hardware controller
            # to ensure there is always a report from that ready during refresh
            t = time.perf_counter()
            elapsed = t - start
            if elapsed < REPORT_DELAY_MIN:
                time.sleep(REPORT_DELAY_MIN - elapsed)

    except KeyboardInterrupt:
        raise
    finally:
        # d_vend.close(True)
        d_timer.close()
        for d in reversed(devs):
            d.close(True)
