"""A monitor's EDID: from the kernel, or straight from the monitor over the HDMI cable's I2C line.

The kernel reads EDID once when a monitor appears. If the monitor was off or slow at
that moment, Linux falls back to safe modes up to 1024x768. The monitor still answers
on the cable's I2C line (the same one DDC/CI uses), so Dot can read it again itself.
"""

import glob
import os

from . import system

I2C_SLAVE = 0x0703
EDID_ADDRESS = 0x50
HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"

try:
    import fcntl
except ImportError:  # dev mode on Windows
    fcntl = None


def connector_dir(connector):
    paths = glob.glob(f"/sys/class/drm/card*-{connector}")
    return paths[0] if paths else None


def i2c_bus(connector):
    """The I2C bus number behind an HDMI port, from the kernel's own link (…/HDMI-A-1/ddc -> i2c-13)."""
    d = connector_dir(connector)
    if not d or not os.path.exists(f"{d}/ddc"):
        return None
    name = os.path.basename(os.path.realpath(f"{d}/ddc"))
    return name.split("-", 1)[1] if name.startswith("i2c-") else None


def from_kernel(connector):
    d = connector_dir(connector)
    try:
        with open(f"{d}/edid", "rb") as f:
            data = f.read()
        return data if data[:8] == HEADER else None
    except (OSError, TypeError):
        return None


def from_monitor(connector):
    """Read the first 128 bytes of EDID over I2C, bypassing the kernel."""
    bus = i2c_bus(connector)
    if bus is None or fcntl is None:
        return None
    try:
        fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.ioctl(fd, I2C_SLAVE, EDID_ADDRESS)
        os.write(fd, b"\x00")  # start at offset 0
        data = os.read(fd, 128)
        return data if data[:8] == HEADER and sum(data) % 256 == 0 else None
    except OSError:
        return None
    finally:
        os.close(fd)


def reprobe(connector):
    """Ask the kernel to look at the port again (the EDID is often there by now)."""
    d = connector_dir(connector)
    if d and not system.DEV:
        try:
            with open(f"{d}/status", "w") as f:
                f.write("detect")
        except OSError:
            pass


def read(connector):
    return from_kernel(connector) or from_monitor(connector)


def preferred_mode(data):
    """(width, height, refresh Hz) of the monitor's own preferred timing, or None."""
    if not data or len(data) < 72:
        return None
    d = data[54:72]
    clock = (d[0] | d[1] << 8) * 10_000  # Hz
    if not clock:
        return None
    h_active = d[2] | (d[4] & 0xF0) << 4
    h_blank = d[3] | (d[4] & 0x0F) << 8
    v_active = d[5] | (d[7] & 0xF0) << 4
    v_blank = d[6] | (d[7] & 0x0F) << 8
    total = (h_active + h_blank) * (v_active + v_blank)
    if not (h_active and v_active and total):
        return None
    return h_active, v_active, round(clock / total)


def physical_mm(data):
    """(width, height) in mm: the detailed timing has millimetres, bytes 21-22 only centimetres."""
    if not data or len(data) < 72:
        return None
    d = data[54:72]
    w = d[12] | ((d[14] & 0xF0) << 4)
    h = d[13] | ((d[14] & 0x0F) << 8)
    if w > 50 and h > 50:
        return w, h
    if data[21] and data[22]:
        return data[21] * 10, data[22] * 10
    return None
