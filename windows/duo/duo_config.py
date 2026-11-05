"""What the Python tools share: the installed Duo's config and Windows process checks.

scripts/install.ps1 writes duo.json next to the copied app
(%LOCALAPPDATA%\\Programs\\ClaudeDuo\\duo.json); everything here only reads it.
The scripts run with `py -3 -I`, which leaves their folder off sys.path, so
they load this module by its path.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess

HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData/Local")
INSTALL_DIR = LOCAL / "Programs/ClaudeDuo"
CONFIG = INSTALL_DIR / "duo.json"
# Loops and other state of the Python tools. Not LOCAL / "claude-duo": NTFS ignores
# case, so that is the Desktop profile (Claude-Duo), which -RemoveProfiles recycles.
DATA = LOCAL / "claude-duo-tools"
DEFAULT_CODE_DIR = HOME / ".claude-duo"
DEFAULT_USER_DATA = LOCAL / "Claude-Duo"


class Refused(Exception):
    """A request the tool does not start or cannot continue."""


def read_config(path=None):
    """duo.json as a dict, or {} when the Duo is not installed or the file is unreadable."""
    path = Path(path) if path else CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _folder(value, fallback):
    if isinstance(value, str) and value.strip():
        return Path(os.path.expandvars(value.strip()))
    return fallback


def code_config_dir(config=None):
    """The Duo's CLAUDE_CONFIG_DIR; ~/.claude-duo when the config says nothing."""
    config = read_config() if config is None else config
    return _folder(config.get("codeConfigDir"), DEFAULT_CODE_DIR)


def user_data_dir(config=None):
    """The Duo's Desktop profile (--user-data-dir)."""
    config = read_config() if config is None else config
    return _folder(config.get("userDataDir"), DEFAULT_USER_DATA)


def process_alive(pid):
    """True while the process runs. os.kill(pid, 0) would terminate it on Windows, so ask the kernel."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists
    try:
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def exe_name(config=None):
    """The file name of the Duo's executable: install.ps1 renames claude.exe to "<Name>.exe"."""
    config = read_config() if config is None else config
    exe = config.get("exe")
    name = PureWindowsPath(exe.strip()).name if isinstance(exe, str) and exe.strip() else ""
    return name if re.fullmatch(r"[\w .-]{1,40}\.exe", name) else "Claude Duo.exe"


def duo_running(config=None):
    """True when a Claude process runs on the Duo's Desktop profile."""
    config = read_config() if config is None else config
    folder = str(user_data_dir(config)).rstrip("\\").lower()
    script = (f"Get-CimInstance Win32_Process -Filter \"Name='claude.exe' OR Name='{exe_name(config)}'\" | "
              "ForEach-Object { $_.CommandLine }")
    try:
        listing = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("não consegui listar os processos do Claude")
    return any(f'--user-data-dir="{folder}"' in line.lower() or f"--user-data-dir={folder}" in line.lower()
               for line in listing.splitlines())
