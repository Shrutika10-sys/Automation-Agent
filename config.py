"""Load and validate configuration for the Gemini Gems extractor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """Raised when .env is missing a required value or a path is invalid."""


@dataclass(frozen=True)
class Config:
    """Runtime settings read from the environment."""

    chrome_user_data_dir: Path
    chrome_profile: str
    gems_url: str
    output_dir: Path
    log_dir: Path
    headless: bool
    debug: bool
    navigation_timeout_ms: int
    element_timeout_ms: int


def load_config() -> Config:
    """Load `.env` from the project folder and validate it.

    Paths in OUTPUT_DIR and LOG_DIR are relative to the project folder
    unless they are already absolute.
    """

    load_dotenv(PROJECT_ROOT / ".env")

    user_data_raw = os.getenv("CHROME_USER_DATA_DIR", "").strip()
    profile = os.getenv("CHROME_PROFILE", "").strip()
    gems_url = os.getenv("GEMS_URL", "https://gemini.google.com/gems/view").strip()
    output_raw = os.getenv("OUTPUT_DIR", "output").strip() or "output"
    log_raw = os.getenv("LOG_DIR", "logs").strip() or "logs"

    missing = [
        name
        for name, value in (
            ("CHROME_USER_DATA_DIR", user_data_raw),
            ("CHROME_PROFILE", profile),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(
            f"Missing required configuration: {joined}. "
            "Copy .env.example to .env and set your Chrome profile paths."
        )

    parsed = urlparse(gems_url)
    if parsed.scheme != "https" or parsed.hostname != "gemini.google.com":
        raise ConfigError(
            "GEMS_URL must be an https://gemini.google.com URL. "
            f"Got: {gems_url}"
        )

    user_data_dir = Path(user_data_raw).expanduser()
    if not user_data_dir.is_dir():
        raise ConfigError(
            "Chrome user data directory does not exist:\n"
            f"  {user_data_dir}\n"
            "Open chrome://version in Chrome and copy the parent folder of "
            "the Profile Path. That parent folder is usually named 'User Data'."
        )

    profile_dir = user_data_dir / profile
    if not profile_dir.is_dir():
        available = _profile_folder_names(user_data_dir)
        available_text = ", ".join(available) if available else "(none found)"
        raise ConfigError(
            "Chrome profile directory does not exist:\n"
            f"  {profile_dir}\n"
            "CHROME_PROFILE must be the profile folder name, such as "
            "'Default' or 'Profile 1', not the full path.\n"
            f"Profile folders in this User Data directory: {available_text}"
        )

    return Config(
        chrome_user_data_dir=user_data_dir,
        chrome_profile=profile,
        gems_url=gems_url,
        output_dir=_resolve_dir(output_raw),
        log_dir=_resolve_dir(log_raw),
        headless=_as_bool(os.getenv("HEADLESS"), default=False),
        debug=_as_bool(os.getenv("DEBUG"), default=False),
        navigation_timeout_ms=_as_int(os.getenv("NAVIGATION_TIMEOUT_MS"), default=60_000),
        element_timeout_ms=_as_int(os.getenv("ELEMENT_TIMEOUT_MS"), default=20_000),
    )


def _resolve_dir(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"Expected an integer configuration value, got: {value}") from exc
    if parsed <= 0:
        raise ConfigError(f"Timeouts must be greater than 0, got: {parsed}")
    return parsed


def _profile_folder_names(user_data_dir: Path) -> list[str]:
    """Return Chrome profile folder names without reading account details."""

    names: list[str] = []
    try:
        children = list(user_data_dir.iterdir())
    except OSError:
        return names
    for child in children:
        if child.is_dir() and (child / "Preferences").is_file():
            names.append(child.name)
    return sorted(names)
