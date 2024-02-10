from hhd.controller import Axis, Button, Configuration
from hhd.controller.physical.evdev import B, to_map
from hhd.plugins import gen_gyro_state

DEFAULT_MAPPINGS: dict[str, tuple[Axis, str | None, float, float | None]] = {
    "accel_x": ("accel_z", "accel", 1, 3),
    "accel_y": ("accel_x", "accel", 1, 3),
    "accel_z": ("accel_y", "accel", 1, 3),
    "anglvel_x": ("gyro_z", "anglvel", -1, None),
    "anglvel_y": ("gyro_x", "anglvel", -1, None),
    "anglvel_z": ("gyro_y", "anglvel", -1, None),
    "timestamp": ("gyro_ts", None, 1, None),
}

BTN_MAPPINGS: dict[int, str] = {
    # Volume buttons come from the same keyboard
    B("KEY_F16"): "mode",  # Big Button
    B("KEY_F15"): "share",  # Small Button
    B("KEY_F17"): "extra_l1",  # LC Button
    B("KEY_F18"): "extra_r1",  # RC Button
}

CONFS = {
    "G1621-02": {"name": "OrangePi G1621-02/G1621-02", "hrtimer": True},
    "NEO-01": {"name": "OrangePi NEO-01/NEO-01", "hrtimer": True},
}

PROTO_AXIS_MAP: dict[int, Axis] = to_map(
    {
        # Sticks
        # Values should range from -1 to 1
        "ls_x": [B("ABS_X")],
        "ls_y": [B("ABS_Y")],
        "rs_x": [B("ABS_Z")],
        "rs_y": [B("ABS_RZ")],
        # Triggers
        # Values should range from -1 to 1
        "rt": [B("ABS_BRAKE")],
        "lt": [B("ABS_GAS")],
        # Hat, implemented as axis. Either -1, 0, or 1
        "hat_x": [B("ABS_HAT0X")],
        "hat_y": [B("ABS_HAT0Y")],
    }
)


def get_default_config(product_name: str):
    out = {
        "name": product_name,
        "hrtimer": True,
        "untested": True,
    }

    return out
