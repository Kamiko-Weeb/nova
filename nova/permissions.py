"""The macOS privacy permissions Nova needs, and where to switch them on.

Accessibility lets Nova read and drive other apps' windows and send mouse and
keyboard input; Screen Recording lets it see the screen. macOS grants each one
per app, and only the user can do it, in System Settings > Privacy & Security.
Other platforms have no equivalent, so PERMISSIONS is empty there.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?{anchor}"

_ax_functions: dict[str, Callable] = {}


def _accessibility(prompt: bool = False) -> bool:
    """Whether this app is trusted for Accessibility. With prompt=True, macOS
    also adds it to the list in System Settings if it is not there yet."""
    if not _ax_functions:
        import objc
        from Foundation import NSBundle

        objc.loadBundleFunctions(
            NSBundle.bundleWithIdentifier_("com.apple.ApplicationServices"),
            _ax_functions,
            [("AXIsProcessTrustedWithOptions", b"Z@")],
        )
    trusted = _ax_functions["AXIsProcessTrustedWithOptions"]
    return bool(trusted({"AXTrustedCheckOptionPrompt": prompt}))


def _screen_recording() -> bool:
    import Quartz

    return bool(Quartz.CGPreflightScreenCaptureAccess())


def _request_screen_recording() -> None:
    import Quartz

    Quartz.CGRequestScreenCaptureAccess()


@dataclass(frozen=True)
class Permission:
    name: str
    purpose: str
    anchor: str  # the page in Privacy & Security
    check: Callable[[], bool]
    request: Callable[[], object]  # adds this app to the page's list
    needs_restart: bool = False  # macOS applies it only after a relaunch

    def granted(self) -> bool:
        try:
            return self.check()
        except Exception:
            return False  # cannot tell; showing it as off at least offers the fix

    def open_settings(self) -> None:
        """Open the page in System Settings where this permission is switched on."""
        try:
            self.request()
        except Exception:
            pass  # the page still opens; the app may just have to be added by hand
        subprocess.run(["open", SETTINGS_URL.format(anchor=self.anchor)], check=False)


PERMISSIONS: list[Permission] = []
if sys.platform == "darwin":
    PERMISSIONS = [
        Permission(
            "Accessibility",
            "control windows, the mouse and the keyboard",
            "Privacy_Accessibility",
            check=_accessibility,
            request=lambda: _accessibility(prompt=True),
        ),
        Permission(
            "Screen Recording",
            "see the screen",
            "Privacy_ScreenCapture",
            check=_screen_recording,
            request=_request_screen_recording,
            needs_restart=True,
        ),
    ]


def missing() -> list[Permission]:
    return [permission for permission in PERMISSIONS if not permission.granted()]
