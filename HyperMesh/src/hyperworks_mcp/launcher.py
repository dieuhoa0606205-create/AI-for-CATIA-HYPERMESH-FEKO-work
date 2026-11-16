"""The HyperWorks launch selector window.

``runhwx.exe`` can either start the requested application directly or show a
launch selector first.  The selector waits for a product choice and a
``Create Session`` click; until someone clicks it, the application owns a main
window that looks perfectly healthy while no session exists, and the live
bridge stays unreachable.  That looks exactly like a broken extension, so the
server has to know about this window instead of leaving it to the caller.

The selector is controlled by ``launcher_show_on_startup``, stored in the user
settings in three places.  This module reads them, can flip them with a backup,
and can see whether the selector window is on screen.  Nothing here changes a
setting on its own; the caller has to ask for it.
"""

from __future__ import annotations

import ctypes
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# matches  name="launcher_show_on_startup" value="1"  and its @-prefixed form
ATTRIBUTE = re.compile(rb'name="(@?(?:launcher_)?show_on_startup)" value="([01])"')
LAUNCHER_TITLE_PREFIX = "Altair HyperWorks"


def setting_files(home: Path | None = None) -> list[Path]:
    """Every user settings file that carries the launcher visibility flag."""
    root = home or Path.home()
    candidates: list[Path] = []
    for pattern in (".altair/*/hwx/settings.xml", ".altair/*/hwx/profiles/*.xml"):
        candidates.extend(sorted(root.glob(pattern)))
    candidates.append(root / "Documents" / "hwsettings.xml")

    found: list[Path] = []
    for path in dict.fromkeys(candidates):
        try:
            if path.is_file() and ATTRIBUTE.search(path.read_bytes()):
                found.append(path)
        except OSError:
            continue
    return found


def read_setting(home: Path | None = None) -> dict:
    """Report whether the launch selector will appear, and where that is set."""
    files = []
    for path in setting_files(home):
        try:
            match = ATTRIBUTE.search(path.read_bytes())
        except OSError:
            continue
        if match:
            files.append({"path": str(path), "value": int(match.group(2))})
    will_show = any(item["value"] == 1 for item in files)
    return {
        "will_show_launcher": will_show,
        "files": files,
        "consequence": (
            "The launch selector appears and waits for a product and Create Session. "
            "The live bridge does not answer until that session is created, which looks "
            "like an extension failure but is not one."
            if will_show
            else "The application starts directly; no selector window is expected."
        ),
    }


def set_setting(show: bool, home: Path | None = None) -> dict:
    """Write the flag into every settings file that carries it, with backups."""
    target = b"1" if show else b"0"
    changed: list[str] = []
    backups: list[str] = []
    for path in setting_files(home):
        raw = path.read_bytes()
        updated = ATTRIBUTE.sub(
            lambda match: b'name="' + match.group(1) + b'" value="' + target + b'"',
            raw,
        )
        if updated == raw:
            continue
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.{stamp}-launcher-backup")
        shutil.copy2(path, backup)
        path.write_bytes(updated)
        changed.append(str(path))
        backups.append(str(backup))
    return {
        "show_launcher": bool(show),
        "changed": changed,
        "backups": backups,
        "now": read_setting(home),
    }


def _visible_windows() -> list[str]:
    """Titles of visible top-level windows. Windows only; empty elsewhere."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles: list[str] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            titles.append(buffer.value)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return titles


def launcher_windows() -> list[str]:
    """Visible launch selector windows, if any are on screen."""
    return [
        title for title in _visible_windows()
        if title.startswith(LAUNCHER_TITLE_PREFIX)
    ]


def wait_for_launcher(timeout: float = 5.0, interval: float = 0.25) -> list[str]:
    """Wait briefly for the selector to appear; return its titles."""
    if sys.platform != "win32":
        return []
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        titles = launcher_windows()
        if titles or time.monotonic() >= deadline:
            return titles
        time.sleep(interval)
