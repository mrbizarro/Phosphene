"""Host facts the panel and the warm helper need, on macOS and on Linux.

macOS answers with sysctl / vm_stat / sw_vers, as before. Linux on Apple
Silicon (Omarchy, with MLX on the GPU through Vulkan) answers from /proc and
the device tree. Every reader returns 0 / "" when it cannot tell, the same
fail-open contract the sysctl calls had.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"

# Device-tree SoC -> marketing name, for the chips Linux runs on today.
_APPLE_SOCS = {
    "t8103": "Apple M1", "t6000": "Apple M1 Pro", "t6001": "Apple M1 Max", "t6002": "Apple M1 Ultra",
    "t8112": "Apple M2", "t6020": "Apple M2 Pro", "t6021": "Apple M2 Max", "t6022": "Apple M2 Ultra",
}


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                              errors="replace", timeout=3).stdout.strip()
    except Exception:                                      # noqa: BLE001
        return ""


def _meminfo() -> dict[str, int]:
    """/proc/meminfo in bytes."""
    out: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[key] = int(parts[0]) * (1024 if parts[1:] == ["kB"] else 1)
    except OSError:
        pass
    return out


def _device_tree(name: str) -> list[str]:
    try:
        return [s for s in Path("/proc/device-tree", name).read_bytes().decode(errors="replace").split("\0") if s]
    except OSError:
        return []


def total_ram_bytes() -> int:
    if IS_MAC:
        out = _sysctl("hw.memsize")
        return int(out) if out.isdigit() else 0
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def memory_usage() -> dict:
    """{"total", "used", "swap_used"} in bytes; 0 for anything unreadable.

    macOS "used" is active + wired + compressed pages (vm_stat); Linux "used"
    is MemTotal - MemAvailable, the kernel's own reclaimable-aware number."""
    info = {"total": total_ram_bytes(), "used": 0, "swap_used": 0}
    if IS_MAC:
        try:
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, errors="replace", timeout=1).stdout
            m = re.search(r"page size of (\d+)", vm)
            page = int(m.group(1)) if m else 16384

            def pages(name: str) -> int:
                mm = re.search(rf"{re.escape(name)}:\s+(\d+)", vm)
                return int(mm.group(1)) if mm else 0

            info["used"] = (pages("Pages active") + pages("Pages wired down")
                            + pages("Pages occupied by compressor")) * page
        except Exception:                                  # noqa: BLE001
            pass
        m = re.search(r"used\s*=\s*([\d.]+)([KMG])", _sysctl("vm.swapusage"))
        if m:
            info["swap_used"] = int(float(m.group(1)) * {"K": 2**10, "M": 2**20, "G": 2**30}[m.group(2)])
        return info
    mi = _meminfo()
    if "MemTotal" in mi and "MemAvailable" in mi:
        info["used"] = mi["MemTotal"] - mi["MemAvailable"]
    info["swap_used"] = max(0, mi.get("SwapTotal", 0) - mi.get("SwapFree", 0))
    return info


def pressure_level() -> int:
    """macOS memorystatus scale: 1 normal, 2 warning, 3 critical.

    Linux maps PSI memory stall (avg10, percent of wall time):
    "full" >= 10 -> 3, "some" >= 10 -> 2."""
    if IS_MAC:
        lvl = _sysctl("kern.memorystatus_vm_pressure_level")
        return int(lvl) if lvl.isdigit() else 1
    try:
        psi = Path("/proc/pressure/memory").read_text()
    except OSError:
        return 1
    avg = {m.group(1): float(m.group(2)) for m in re.finditer(r"^(some|full) avg10=([\d.]+)", psi, re.M)}
    if avg.get("full", 0.0) >= 10:
        return 3
    return 2 if avg.get("some", 0.0) >= 10 else 1


def chip_brand() -> str:
    """'Apple M1 Max' style string, or '' when unknown."""
    if IS_MAC:
        return _sysctl("machdep.cpu.brand_string")
    for compat in _device_tree("compatible"):
        vendor, _, soc = compat.partition(",")
        if vendor == "apple" and soc in _APPLE_SOCS:
            return _APPLE_SOCS[soc]
    return ""


def hw_model() -> str:
    if IS_MAC:
        return _sysctl("hw.model")
    names = _device_tree("model")
    return names[0] if names else ""


def os_version() -> str:
    if IS_MAC:
        return platform.mac_ver()[0]
    return f"Linux {platform.release()}"


def keep_awake_prefix() -> list[str]:
    """argv prefix that keeps the machine from idle-sleeping while a child runs.

    Linux gets none: systemd-inhibit needs an interactive polkit grant outside
    a local seat session, and a refused grant would fail the render it wraps."""
    return ["caffeinate", "-i"] if IS_MAC else []


def h3_env_defaults() -> dict[str, str]:
    """Environment the Hailuo H3 runner needs on this system, applied only where the caller has not set it.

    Linux: the MLX Vulkan backend addresses one storage buffer through a 2 GiB binding, and the video VAE's
    default 8-tile batch asks attention for 2.48 GB at 640x384 (H3_VAE_BATCH=1 decodes tile by tile)."""
    return {} if IS_MAC else {"H3_VAE_BATCH": "1"}
