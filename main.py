"""Extract every user-created Gemini Gem into one CSV for the run.

This script only reads Gem fields. It does not create, edit, share, or delete Gems,
and it does not sign in to Google.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browser import BrowserError, LoginRequiredError, confirm_profile, launch_chrome, open_page, start_playwright
from config import Config, ConfigError, load_config
from file_manager import FileManager, FileManagerError
from gem_extractor import ExtractionError, GemCard, GemExtractor


def configure_stdio() -> None:
    """Allow Unicode Gem names to print in a Windows console."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def setup_logging(log_dir: Path, debug: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "automation.log"
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


@dataclass
class GemFailure:
    name: str
    reason: str


def run(config: Config) -> int:
    logger = logging.getLogger("main")
    logger.info("Automation started")
    files = FileManager(config.output_dir)
    files.prepare()

    playwright = start_playwright()
    session = None
    successes: list[str] = []
    failures: list[GemFailure] = []
    total = 0

    try:
        session = launch_chrome(playwright, config)
        page = open_page(session)
        confirm_profile(page, config)
        extractor = GemExtractor(page, config)
        extractor.open_manager()
        cards = extractor.discover()
        total = len(cards)

        for card in cards:
            logger.info("Processing Gem: %s", card.name)
            try:
                saved_name = _process_gem(extractor, files, card, logger)
                successes.append(saved_name)
            except LoginRequiredError as exc:
                logger.error("Gem failed: %s", card.name)
                logger.error("%s", exc)
                failures.append(GemFailure(card.name, str(exc)))
                break
            except Exception as exc:
                reason = _short_reason(exc)
                logger.exception("Gem failed: %s", card.name)
                if config.debug:
                    extractor.capture_debug(card)
                failures.append(GemFailure(card.name or card.gem_id, reason))
                extractor.return_quietly()
    finally:
        if session is not None:
            session.close()
        playwright.stop()

    _print_summary(total, successes, failures, files.csv_path, files.saved_count)
    logger.info("Automation completed")
    return 1 if failures else 0


def _process_gem(
    extractor: GemExtractor,
    files: FileManager,
    card: GemCard,
    logger: logging.Logger,
) -> str:
    """Read one Gem, save it, then return to the manager.

    The manager is reopened only after a successful save so a failure
    screenshot still shows the page that failed.
    """

    extractor.wait_for_manager()
    extractor.open_editor(card)
    record = extractor.read_record(card)
    files.save(record.name, record.description, record.instructions)
    logger.info("Gem completed: %s", record.name)
    print(
        "\n".join(
            [
                "",
                "Name:",
                record.name,
                "",
                "Description:",
                record.description,
                "",
                "Instructions:",
                record.instructions,
                "",
            ]
        )
    )
    extractor.return_quietly()
    return record.name


def _print_summary(
    total: int,
    successes: list[str],
    failures: list[GemFailure],
    csv_path: Path,
    saved_count: int,
) -> None:
    lines = [
        f"Saved to: {csv_path}",
        f"Total Gems: {total}",
        f"Successful: {len(successes)}",
        f"Failed: {len(failures)}",
        f"Cumulative rows in CSV: {saved_count}",
    ]
    if failures:
        lines.extend(["", "Failed Gems:"])
        lines.extend(f"- {failure.name}: {failure.reason}" for failure in failures)
    text = "\n".join(lines)
    print(text)
    logging.getLogger("main").info("\n%s", text)


def _short_reason(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message.splitlines()[0]


def main() -> int:
    configure_stdio()
    try:
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    setup_logging(config.log_dir, config.debug)
    try:
        return run(config)
    except (BrowserError, LoginRequiredError, ExtractionError, FileManagerError) as exc:
        logging.getLogger("main").error("%s", exc)
        print(exc, file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger("main").exception("Automation failed")
        print("Automation failed. See logs/automation.log for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
