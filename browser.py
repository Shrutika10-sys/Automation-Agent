"""Attach Playwright to the Chrome window that is already signed in."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from config import Config

logger = logging.getLogger(__name__)

_REMOTE_DEBUGGING_HELP = (
    "Chrome blocks a second window from using the Gemini login that already works "
    "in your normal Chrome window. Leave that window open and turn on remote debugging:\n"
    "  1. Open chrome://inspect/#remote-debugging\n"
    "  2. Turn remote debugging on\n"
    "  3. Leave Chrome open and run this script again\n"
    "  4. If Chrome asks to allow debugging, click Allow"
)


class BrowserError(Exception):
    """Raised when Chrome cannot be attached with the configured profile."""


class LoginRequiredError(Exception):
    """Raised when Gemini redirects to a Google sign-in page."""


@dataclass
class ChromeSession:
    """An attached Chrome window. Closing it disconnects without quitting Chrome."""

    context: BrowserContext
    browser: Browser
    page: Page

    def close(self) -> None:
        try:
            self.page.close()
        except Exception:
            logger.debug("Could not close the automation tab", exc_info=True)
        try:
            self.browser.close()
        except Exception:
            logger.debug("Could not disconnect from Chrome", exc_info=True)


def chrome_is_running() -> bool:
    """Return True when a chrome.exe process is already running on Windows."""

    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BrowserError(f"Could not check whether Chrome is running: {exc}") from exc
    return "chrome.exe" in completed.stdout.lower()


def launch_chrome(playwright: Playwright, config: Config) -> ChromeSession:
    """Attach to the running Chrome profile instead of starting a second one.

    A second Chrome process can show the account name and still be signed out
    of Gemini, because Chrome encrypts that login for the original window only.
    """

    if not chrome_is_running():
        raise BrowserError("Chrome is not open.\n" + _REMOTE_DEBUGGING_HELP)

    endpoint = _debugging_endpoint(config.chrome_user_data_dir)
    logger.info("Attaching to the open Chrome profile %s", config.chrome_profile)
    try:
        browser = playwright.chromium.connect_over_cdp(
            endpoint,
            timeout=60_000,
            is_local=True,
            no_defaults=True,
        )
    except Exception as exc:
        raise BrowserError(
            "Could not attach to the open Chrome window.\n" + _REMOTE_DEBUGGING_HELP
        ) from exc

    context, page = _page_for_profile(browser, config)
    context.set_default_timeout(config.element_timeout_ms)
    context.set_default_navigation_timeout(config.navigation_timeout_ms)
    logger.info("Chrome profile loaded: %s", config.chrome_profile)
    return ChromeSession(context=context, browser=browser, page=page)


def _debugging_endpoint(user_data_dir: Path) -> str:
    """Read the WebSocket address Chrome writes after remote debugging is enabled."""

    port_file = user_data_dir / "DevToolsActivePort"
    if not port_file.is_file():
        raise BrowserError(_REMOTE_DEBUGGING_HELP)
    lines = [line.strip() for line in port_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit():
        raise BrowserError(_REMOTE_DEBUGGING_HELP)
    path = lines[1] if lines[1].startswith("/") else f"/{lines[1]}"
    return f"ws://127.0.0.1:{lines[0]}{path}"


def _page_for_profile(browser: Browser, config: Config) -> tuple[BrowserContext, Page]:
    """Open one tab in the configured profile and leave other profiles alone."""

    contexts = list(browser.contexts)
    if not contexts:
        raise BrowserError(
            f"Chrome is open, but profile '{config.chrome_profile}' has no window.\n"
            + _REMOTE_DEBUGGING_HELP
        )

    for context in contexts:
        page = context.new_page()
        folder = _profile_folder_name(page)
        if folder.lower() == config.chrome_profile.lower():
            return context, page
        page.close()

    raise BrowserError(
        f"Chrome is open, but profile '{config.chrome_profile}' was not found. "
        "Switch to that profile, turn remote debugging on, and run the script again."
    )


def _profile_folder_name(page: Page) -> str:
    page.goto("chrome://version", wait_until="domcontentloaded")
    profile_path = page.evaluate(
        """() => {
            const text = document.body ? document.body.innerText : "";
            const match = text.match(/Profile Path\\s*([^\\n]+)/i);
            return match ? match[1].trim() : "";
        }"""
    )
    if not profile_path:
        return ""
    return Path(str(profile_path)).name


def open_page(session: ChromeSession) -> Page:
    """Return the tab already opened in the configured Chrome profile."""

    return session.page


def confirm_profile(page: Page, config: Config) -> None:
    """Check chrome://version and log the profile folder that actually opened.

    Only the profile folder name is logged. The full path is not written.
    """

    try:
        page.goto("chrome://version", wait_until="domcontentloaded")
        profile_path = page.evaluate(
            """() => {
                const text = document.body ? document.body.innerText : "";
                const match = text.match(/Profile Path\\s*([^\\n]+)/i);
                return match ? match[1].trim() : "";
            }"""
        )
    except Exception:
        logger.warning(
            "Could not read chrome://version. Continuing with the configured profile %s.",
            config.chrome_profile,
        )
        logger.info("Chrome profile loaded: %s", config.chrome_profile)
        return

    if not profile_path:
        logger.warning("chrome://version did not show a Profile Path")
        logger.info("Chrome profile loaded")
        return

    actual = Path(profile_path).name
    if actual.lower() != config.chrome_profile.lower():
        raise BrowserError(
            f"Chrome opened profile '{actual}' instead of '{config.chrome_profile}'. "
            "Check CHROME_PROFILE in .env. Use the last folder of the Profile Path "
            "shown on chrome://version, for example 'Default' or 'Profile 1'."
        )
    logger.info("Chrome profile loaded: %s", actual)


def start_playwright() -> Playwright:
    """Start the Playwright driver. The caller must stop it."""

    return sync_playwright().start()
