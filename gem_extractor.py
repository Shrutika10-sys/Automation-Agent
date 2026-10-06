"""Discover My Gems and Shared with me, then read each editor.

The Gem Manager card only shows a short name and description. The script
clicks the Edit control that belongs to that card, then reads the three
fields from the editor. It never clicks Save, Update, Share, More, or Delete.

DOM contract, from the current Gemini Gems manager:

- My Gems are inside [data-test-id="your-gems-list"].
- Shared with me is the next section. Its rows are bot-list-row elements too.
- Each Gem is a bot-list-row.
- The card link is an anchor whose href contains /gem/{id}.
- The visible card title is .title-container. The card blurb is .bot-desc.
- Share, Edit, and More live in that same row and appear on hover.
- Premade by Google gems come after those sections and are ignored.
- The editor exposes Name, Description, and Instructions as separate fields.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from browser import LoginRequiredError
from config import Config

logger = logging.getLogger(__name__)

ButtonKind = Literal["edit", "share", "more", "dangerous", "ignore", "other"]

_MY_GEMS_LIST = '[data-test-id="your-gems-list"]'
_MAX_SCROLL_ROUNDS = 60
_SCROLL_PAUSE_MS = 500
_ALLOWED_HOSTS = {"gemini.google.com", "www.gemini.google.com"}

_DISCOVER_JS = """
() => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();

  const parseId = (href) => {
    try {
      const url = new URL(href, location.origin);
      const match = url.pathname.match(/^\\/(?:u\\/\\d+\\/)?gem\\/([^/?#]+)/);
      return match ? match[1] : "";
    } catch (error) {
      return "";
    }
  };

  const readRow = (row) => {
    const anchor = row.querySelector('a.bot-row, a[href*="/gem/"]');
    if (!anchor) return null;
    const id = parseId(anchor.getAttribute("href") || "");
    if (!id) return null;
    const title = anchor.querySelector(".title-container, .bot-title-inner");
    const description = anchor.querySelector(".bot-desc");
    const name = clean(title ? title.textContent : "") || clean(anchor.innerText).slice(0, 120);
    if (!name) return null;
    return {
      id,
      name,
      description: clean(description ? description.textContent : ""),
    };
  };

  const findHeading = (pattern, maxLength) =>
    [...document.querySelectorAll("body *")].find((el) => {
      const text = clean(el.innerText || el.textContent || "");
      if (!pattern.test(text) || text.length > maxLength) return false;
      return ![...el.children].some((child) =>
        pattern.test(clean(child.innerText || child.textContent || ""))
      );
    });

  const follows = (heading, row) =>
    Boolean(heading) &&
    Boolean(heading.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING);

  const myGems = findHeading(/^my gems\\b/i, 40);
  const shared = findHeading(/^shared with me\\b/i, 40);
  const premade = findHeading(/^premade\\b/i, 80);
  const mineList = document.querySelector('[data-test-id="your-gems-list"]');
  const sharedLists = [...document.querySelectorAll("[data-test-id]")].filter((el) =>
    /shared/i.test(el.getAttribute("data-test-id") || "")
  );

  const closestSection = (row) => {
    const candidates = [
      ["My Gems", myGems],
      ["Shared with me", shared],
      ["Premade", premade],
    ].filter(([, heading]) => follows(heading, row));
    candidates.sort((left, right) => {
      const position = left[1].compareDocumentPosition(right[1]);
      if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
      if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
      return 0;
    });
    return candidates.length ? candidates[candidates.length - 1][0] : "";
  };

  const gems = [];
  const seen = new Set();
  for (const row of document.querySelectorAll("bot-list-row")) {
    let section = "";
    if (mineList && mineList.contains(row)) {
      section = "My Gems";
    } else if (sharedLists.some((list) => list.contains(row))) {
      section = "Shared with me";
    } else {
      section = closestSection(row);
    }
    if (!section || section === "Premade") continue;
    const item = readRow(row);
    if (!item || seen.has(item.id)) continue;
    seen.add(item.id);
    item.section = section;
    gems.push(item);
  }

  const strategy = mineList
    ? "gem-sections"
    : myGems || shared
      ? "section-headings"
      : "not-found";
  return {
    strategy,
    myGemsVisible: Boolean(myGems || mineList),
    sharedVisible: Boolean(shared || sharedLists.length),
    premadeVisible: Boolean(premade),
    gems,
  };
}
"""

_SCROLL_JS = """
() => {
  const list = document.querySelector('[data-test-id="your-gems-list"]');
  const findScrollable = (start) => {
    let node = start;
    while (node && node !== document.body) {
      const style = window.getComputedStyle(node);
      const overflow = `${style.overflowY} ${style.overflow}`;
      if (/(auto|scroll)/.test(overflow) && node.scrollHeight > node.clientHeight + 20) {
        return node;
      }
      node = node.parentElement;
    }
    return document.scrollingElement || document.documentElement;
  };

  const scroller = findScrollable(list || document.body);
  const before = scroller.scrollTop;
  const step = Math.max(320, Math.floor(scroller.clientHeight * 0.8));
  scroller.scrollBy(0, step);
  window.scrollBy(0, Math.max(320, Math.floor(window.innerHeight * 0.8)));

  const rows = document.querySelectorAll("bot-list-row");
  if (rows.length) rows[rows.length - 1].scrollIntoView({ block: "end" });
  return {
    before,
    after: scroller.scrollTop,
    moved: scroller.scrollTop > before + 5,
  };
}
"""

_FIELD_JS = """
(label) => {
  const normalize = (value) =>
    (value || "").replace(/[:*]/g, "").replace(/\\s+/g, " ").trim().toLowerCase();
  const wanted = normalize(label);

  const matches = (text) => normalize(text) === wanted;

  const isTextControl = (el) => {
    if (!el) return false;
    if (el.isContentEditable || el.getAttribute("role") === "textbox") return true;
    const tag = el.tagName.toLowerCase();
    if (tag === "textarea") return true;
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      return type === "text" || type === "search" || type === "";
    }
    return false;
  };

  const readControl = (el) => {
    if (!el) return null;
    const inner = el.matches("input, textarea") ? el : el.querySelector("textarea, input");
    const target = inner && isTextControl(inner) ? inner : el;
    if (!isTextControl(target)) return null;
    let value = "";
    if (target.matches("input, textarea")) value = target.value || "";
    else value = target.innerText || "";
    const placeholder = (
      target.getAttribute("placeholder") ||
      target.getAttribute("data-placeholder") ||
      target.getAttribute("aria-placeholder") ||
      ""
    ).trim();
    if (placeholder && value.trim() === placeholder) return "";
    return value;
  };

  const controls = [...document.querySelectorAll("input, textarea, [contenteditable='true'], [role='textbox']")];

  for (const el of controls) {
    if ((el.getAttribute("aria-label") || "").trim() && matches(el.getAttribute("aria-label"))) {
      const value = readControl(el);
      if (value !== null) return { found: true, value, method: "aria-label" };
    }
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\\s+/)
        .map((id) => document.getElementById(id)?.innerText || "")
        .join(" ");
      if (matches(text)) {
        const value = readControl(el);
        if (value !== null) return { found: true, value, method: "aria-labelledby" };
      }
    }
  }

  const labelNodes = [...document.querySelectorAll("label, mat-label, span, div, p, h1, h2, h3, h4")];
  const candidates = labelNodes.filter((el) => matches(el.innerText || el.textContent || ""));
  candidates.sort((a, b) => {
    const depth = (node) => {
      let count = 0;
      let current = node;
      while (current.parentElement) {
        count += 1;
        current = current.parentElement;
      }
      return count;
    };
    return depth(b) - depth(a);
  });

  for (const labelNode of candidates) {
    if (labelNode.tagName.toLowerCase() === "label") {
      const forId = labelNode.getAttribute("for");
      if (forId) {
        const value = readControl(document.getElementById(forId));
        if (value !== null) return { found: true, value, method: "label-for" };
      }
      const nested = labelNode.querySelector("input, textarea, [contenteditable='true'], [role='textbox']");
      const nestedValue = readControl(nested);
      if (nestedValue !== null) return { found: true, value, method: "nested-label" };
    }

    const field = labelNode.closest("mat-form-field, .mat-mdc-form-field, .mat-form-field");
    if (field) {
      const value = readControl(
        field.querySelector("input, textarea, [contenteditable='true'], [role='textbox']")
      );
      if (value !== null) return { found: true, value, method: "form-field" };
    }

    let parent = labelNode.parentElement;
    for (let depth = 0; depth < 6 && parent; depth += 1) {
      const following = [...parent.querySelectorAll("input, textarea, [contenteditable='true'], [role='textbox']")]
        .find((control) => {
          if (!isTextControl(control)) return false;
          const aria = (control.getAttribute("aria-label") || "").trim();
          const promptEditor =
            wanted === "instructions" &&
            /prompt/i.test(aria) &&
            control.classList.contains("ql-editor");
          if (aria && !matches(aria) && !promptEditor) return false;
          return Boolean(labelNode.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING);
        });
      const value = readControl(following);
      if (value !== null) return { found: true, value, method: "following-control" };
      parent = parent.parentElement;
    }
  }

  return { found: false, value: "", method: "missing" };
}
"""

_SNAPSHOT_JS = """
() => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim().slice(0, 160);
  const interesting = [...document.querySelectorAll(
    "button, [role='button'], [role='textbox'], input, textarea, [contenteditable='true'], [data-test-id], h1, h2, h3"
  )].slice(0, 80).map((el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role") || "",
    aria: el.getAttribute("aria-label") || "",
    testId: el.getAttribute("data-test-id") || "",
    text: clean(el.innerText || ""),
  }));
  return {
    url: location.href,
    title: document.title,
    hasMyGemsList: Boolean(document.querySelector('[data-test-id="your-gems-list"]')),
    rowCount: document.querySelectorAll('[data-test-id="your-gems-list"] bot-list-row').length,
    elements: interesting,
  };
}
"""


class ExtractionError(Exception):
    """Raised when one Gem cannot be read. Other Gems should still continue."""


@dataclass(frozen=True)
class GemCard:
    """A Gem card from My Gems or Shared with me."""

    gem_id: str
    name: str
    description: str
    section: str


@dataclass(frozen=True)
class GemRecord:
    """Fields read from the Gem editor. These are the values that get saved."""

    gem_id: str
    name: str
    description: str
    instructions: str


class GemExtractor:
    """Read-only automation for the Gemini Gem Manager."""

    def __init__(self, page: Page, config: Config) -> None:
        self.page = page
        self.config = config
        self.page.on("dialog", self._on_dialog)

    def open_manager(self) -> None:
        """Open the Gem Manager and wait until My Gems can be inspected."""

        logger.info("Opening %s", self.config.gems_url)
        self.page.goto(self.config.gems_url, wait_until="domcontentloaded")
        self._raise_if_signed_out()
        self.wait_for_manager()
        logger.info("Gemini opened")

    def wait_for_manager(self) -> None:
        """Wait until the My Gems list, or the My Gems heading, is visible."""

        self._raise_if_signed_out()
        list_locator = self.page.locator(_MY_GEMS_LIST)
        heading = self.page.get_by_text(re.compile(r"^\s*my gems\s*$", re.I))
        try:
            list_locator.first.wait_for(state="visible", timeout=self.config.element_timeout_ms)
        except PlaywrightTimeoutError:
            try:
                heading.first.wait_for(state="visible", timeout=5_000)
            except PlaywrightTimeoutError as exc:
                self._write_snapshot("manager_not_found")
                raise ExtractionError(
                    "The Gem Manager did not show My Gems. The profile may be signed "
                    "out, or the page layout changed. A structural snapshot was saved "
                    "under the logs folder."
                ) from exc
        if heading.count() > 0:
            logger.info("My Gems section confirmed")
        else:
            logger.info("My Gems list found by data-test-id=your-gems-list")

    def discover(self) -> list[GemCard]:
        """Scroll until My Gems and Shared with me stop growing.

        Premade by Google gems stay excluded.
        """

        found: dict[str, GemCard] = {}
        strategy = "not-found"
        stable_rounds = 0

        for _ in range(1, _MAX_SCROLL_ROUNDS + 1):
            payload = self.page.evaluate(_DISCOVER_JS)
            strategy = str(payload.get("strategy", "not-found"))
            before = len(found)
            for item in payload.get("gems", []):
                gem_id = str(item.get("id", "")).strip()
                name = _clean_text(str(item.get("name", "")))
                if not gem_id or not name or gem_id in found:
                    continue
                found[gem_id] = GemCard(
                    gem_id=gem_id,
                    name=name,
                    description=_clean_text(str(item.get("description", ""))),
                    section=_clean_text(str(item.get("section", ""))) or "My Gems",
                )
            movement = self.page.evaluate(_SCROLL_JS)
            self.page.wait_for_timeout(_SCROLL_PAUSE_MS)
            grew = len(found) > before
            moved = bool(movement.get("moved"))
            if not grew and not moved:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= 2:
                break

        if strategy == "not-found" and not found:
            self._write_snapshot("discovery_failed")
            raise ExtractionError(
                "Could not find the My Gems list. A structural snapshot was saved "
                "under the logs folder."
            )

        mine_count = sum(1 for card in found.values() if card.section == "My Gems")
        shared_count = sum(1 for card in found.values() if card.section == "Shared with me")
        logger.info("Discovery strategy: %s", strategy)
        logger.info("Number of Gems found: %s", len(found))
        logger.info("My Gems: %s", mine_count)
        logger.info("Shared with me: %s", shared_count)
        logger.info("Premade by Google gems are not included")
        if not found:
            self._write_snapshot("no_gems")
            logger.warning(
                "No Gems were found under My Gems or Shared with me. If that is "
                "unexpected, confirm CHROME_PROFILE and set DEBUG=true."
            )
        return list(found.values())

    def open_editor(self, card: GemCard) -> None:
        """Hover one Gem card and click its own Edit control."""

        row = self._row_for(card)
        row.scroll_into_view_if_needed()
        row.hover()
        scope = self._action_scope(row)
        try:
            scope.locator("button, [role='button']").first.wait_for(state="visible", timeout=5_000)
        except PlaywrightTimeoutError:
            logger.debug("Action buttons were not visible after hover for %s", card.name)

        button, strategy = self._pick_edit_button(scope)
        button.scroll_into_view_if_needed()
        button.hover()
        try:
            button.click(timeout=5_000)
        except PlaywrightTimeoutError:
            logger.warning("Edit button was covered; clicking that same Edit element directly")
            button.click(force=True, timeout=5_000)
        logger.info("Edit button clicked")
        logger.debug("Edit button matched by %s", strategy)
        self._wait_for_editor()

    def read_record(self, card: GemCard) -> GemRecord:
        """Read the three editor fields without changing them."""

        name = self._read_field("Name")
        if name is None:
            raise ExtractionError("Name field was not found on the Gem editor")
        if not name:
            raise ExtractionError("Name field was empty on the Gem editor")
        logger.info("Name extracted")

        description = self._read_field("Description")
        if description is None:
            raise ExtractionError("Description field was not found on the Gem editor")
        logger.info("Description extracted")

        instructions = self._read_field("Instructions")
        if instructions is None:
            raise ExtractionError("Instructions field was not found on the Gem editor")
        logger.info("Instructions extracted")

        return GemRecord(
            gem_id=card.gem_id,
            name=name,
            description=description,
            instructions=instructions,
        )

    def return_to_manager(self) -> None:
        """Reload the Gem Manager. This does not click Save or Update."""

        self.page.goto(self.config.gems_url, wait_until="domcontentloaded")
        self._dismiss_leave_dialog()
        self._raise_if_signed_out()
        self.wait_for_manager()

    def return_quietly(self) -> None:
        """Return to the Gem Manager without hiding an extraction result."""

        try:
            self.return_to_manager()
        except Exception:
            logger.exception("Could not return to the Gem Manager")

    def capture_debug(self, card: GemCard) -> None:
        """Save a screenshot and a structural snapshot. Cookies are not saved."""

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.config.log_dir / "failures"
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"\W+", "_", card.name).strip("_")[:40] or card.gem_id[:12]
        screenshot = folder / f"{stamp}_{stem}.png"
        snapshot = folder / f"{stamp}_{stem}.json"
        try:
            self.page.screenshot(path=str(screenshot), full_page=False)
            logger.info("Failure screenshot saved: %s", screenshot.name)
        except Exception:
            logger.exception("Could not save a failure screenshot")
        self._write_snapshot(snapshot.name, folder)

    def _wait_for_editor(self) -> None:
        instructions = self.page.get_by_text(re.compile(r"^\s*instructions\s*$", re.I))
        name = self.page.get_by_text(re.compile(r"^\s*name\s*$", re.I))
        try:
            instructions.first.wait_for(state="visible", timeout=self.config.element_timeout_ms)
            name.first.wait_for(state="visible", timeout=self.config.element_timeout_ms)
        except PlaywrightTimeoutError as exc:
            inventory = self._textbox_inventory()
            raise ExtractionError(
                "The Gem editor did not open after the Edit button was clicked. "
                f"Textboxes on the page: {inventory}"
            ) from exc
        self._raise_if_unexpected_site()

    def _row_for(self, card: GemCard) -> Locator:
        selector = f'bot-list-row:has(a[href*="/gem/{card.gem_id}"])'
        row = self.page.locator(selector)

        for _ in range(30):
            if row.count() > 0:
                return row.first
            self.page.evaluate(_SCROLL_JS)
            self.page.wait_for_timeout(_SCROLL_PAUSE_MS)
        raise ExtractionError(f"Could not find the Gem card for '{card.name}'")

    def _action_scope(self, row: Locator) -> Locator:
        """Prefer buttons inside the card. Widen by one parent only when needed."""

        buttons = row.locator("button, [role='button']")
        if buttons.count() > 0:
            return row
        parent = row.locator("..")
        if parent.locator("bot-list-row").count() == 1:
            return parent
        return row

    def _pick_edit_button(self, scope: Locator) -> tuple[Locator, str]:
        controls = scope.locator("button, [role='button']")
        classified: list[tuple[ButtonKind, Locator, str]] = []
        for index in range(controls.count()):
            control = controls.nth(index)
            kind, detail = self._classify(control)
            # Ignore the card link itself. An Edit button nested in that link
            # is still eligible; Share and More inside it are not.
            if self._is_card_link(control) and kind != "edit":
                continue
            if kind in {"dangerous", "ignore"}:
                continue
            classified.append((kind, control, detail))

        edits = [item for item in classified if item[0] == "edit"]
        if len(edits) == 1:
            return edits[0][1], "label or icon"
        if len(edits) > 1:
            raise ExtractionError(
                "More than one Edit control was found on this Gem card. "
                f"Controls: {self._format_controls(classified)}"
            )

        share_at = [index for index, item in enumerate(classified) if item[0] == "share"]
        more_at = [index for index, item in enumerate(classified) if item[0] == "more"]
        if len(share_at) == 1 and len(more_at) == 1 and share_at[0] < more_at[0]:
            between = [
                item
                for item in classified[share_at[0] + 1 : more_at[0]]
                if item[0] == "other"
            ]
            if len(between) == 1:
                return between[0][1], "control between Share and More"

        raise ExtractionError(
            "Could not find the Edit button on this Gem card. "
            f"Controls: {self._format_controls(classified) or 'none'}"
        )

    def _is_card_link(self, control: Locator) -> bool:
        return bool(
            control.evaluate(
                """(el) => {
                    const link = el.closest("a");
                    if (!link) return false;
                    const href = link.getAttribute("href") || "";
                    return /\\/gem\\//.test(href) && !/edit/i.test(href);
                }"""
            )
        )

    def _classify(self, control: Locator) -> tuple[ButtonKind, str]:
        aria = control.get_attribute("aria-label") or ""
        title = control.get_attribute("title") or ""
        test_id = control.get_attribute("data-test-id") or ""
        icon = str(
            control.evaluate(
                """(el) => {
                    const nodes = el.querySelectorAll(
                        "mat-icon, .google-symbols, .material-symbols-outlined, .material-icons, svg[aria-label]"
                    );
                    return [...nodes].map((node) => (node.getAttribute("aria-label") || node.textContent || "").trim()).join(" ");
                }"""
            )
        )
        detail = (aria or title or icon or test_id or "unlabeled").strip()
        blob = " ".join([aria, title, test_id, icon]).lower()
        if re.search(r"delete|remove|trash", blob):
            return "dangerous", detail
        if re.search(r"rewrite|use gemini", blob):
            return "ignore", detail
        if re.search(r"\bshare\b|ios_share", blob):
            return "share", detail
        if re.search(r"\bmore\b|more_vert|more_horiz|\boptions\b|overflow", blob):
            return "more", detail
        if re.search(r"\bedit\b|edit_square|mode_edit|\bpencil\b", blob):
            return "edit", detail
        return "other", detail

    def _format_controls(self, classified: list[tuple[ButtonKind, Locator, str]]) -> str:
        parts = [f"{kind}:{label or 'unlabeled'}" for kind, _, label in classified[:12]]
        return ", ".join(parts)

    def _read_field(self, label: str) -> str | None:
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
        found_empty = False
        for candidate in (
            self.page.get_by_role("textbox", name=pattern),
            self.page.get_by_label(pattern),
        ):
            visible = self._first_visible(candidate)
            if visible is None:
                continue
            value = self._read_control(visible)
            if value:
                logger.debug("%s read using an accessible locator", label)
                return value
            found_empty = True

        payload = self.page.evaluate(_FIELD_JS, label)
        if payload.get("found"):
            value = _clean_text(str(payload.get("value", "")))
            if value:
                logger.debug("%s read using %s", label, payload.get("method"))
                return value
            found_empty = True

        if label.lower() == "instructions":
            prompt = self._read_instruction_editor()
            if prompt is not None:
                logger.debug("Instructions read from the prompt editor")
                return prompt

        if found_empty:
            return ""
        return None

    def _read_instruction_editor(self) -> str | None:
        """Read the Gem instruction box.

        Gemini labels that box "Enter a prompt for Gemini", not "Instructions".
        The visible Instructions heading sits beside an empty control.
        """

        editors = self.page.locator(
            "div.ql-editor[contenteditable='true'][aria-label*='prompt' i]"
        )
        visible = self._first_visible(editors)
        if visible is None:
            visible = self._first_visible(
                self.page.get_by_role("textbox", name=re.compile(r"enter a prompt", re.I))
            )
        if visible is None:
            return None
        return self._read_control(visible)

    def _first_visible(self, locator: Locator) -> Locator | None:
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except PlaywrightTimeoutError:
                continue
        return None

    def _read_control(self, control: Locator) -> str:
        tag = str(control.evaluate("el => el.tagName.toLowerCase()"))
        if tag in {"input", "textarea"}:
            return _clean_text(control.input_value())

        text = _clean_text(control.inner_text())
        nested = control.locator("textarea, input")
        if nested.count() > 0 and not text:
            try:
                return _clean_text(nested.first.input_value())
            except Exception:
                logger.debug("Nested input_value failed", exc_info=True)
        return text

    def _textbox_inventory(self) -> str:
        boxes = self.page.get_by_role("textbox")
        parts: list[str] = []
        for index in range(min(boxes.count(), 12)):
            box = boxes.nth(index)
            aria = box.get_attribute("aria-label") or ""
            placeholder = box.get_attribute("placeholder") or ""
            parts.append(aria or placeholder or "unlabeled")
        return ", ".join(parts) or "none"

    def _dismiss_leave_dialog(self) -> None:
        """Close an unsaved-changes dialog by leaving. Never click Save or Update."""

        dialog = self.page.get_by_role("alertdialog").or_(self.page.get_by_role("dialog"))
        try:
            if dialog.count() == 0 or not dialog.first.is_visible():
                return
        except PlaywrightTimeoutError:
            return
        leave = dialog.get_by_role(
            "button",
            name=re.compile(r"^(discard|leave|don't save|dont save)$", re.I),
        )
        if leave.count() == 0 or not leave.first.is_visible():
            return
        logger.info("Leaving the Gem editor without saving")
        leave.first.click()

    def _raise_if_signed_out(self) -> None:
        host = urlparse(self.page.url).hostname or ""
        if host == "accounts.google.com" or "ServiceLogin" in self.page.url:
            raise LoginRequiredError(
                "Gemini opened a Google sign-in page. This profile is not signed in. "
                "Sign in to Gemini manually in this Chrome profile, close Chrome, and run the script again."
            )

    def _raise_if_unexpected_site(self) -> None:
        host = urlparse(self.page.url).hostname or ""
        if host == "accounts.google.com":
            self._raise_if_signed_out()
        if host not in _ALLOWED_HOSTS:
            raise ExtractionError(f"Navigation left Gemini and opened {host or 'an unknown site'}")

    def _on_dialog(self, dialog) -> None:
        message = dialog.message.lower()
        if dialog.type == "beforeunload" or any(
            word in message for word in ("leave", "discard", "unsaved", "changes")
        ):
            logger.info("Accepting a leave-page dialog without saving")
            dialog.accept()
            return
        logger.warning("Dismissed an unexpected browser dialog of type %s", dialog.type)
        dialog.dismiss()

    def _write_snapshot(self, name: str, folder: Path | None = None) -> None:
        destination_dir = folder or self.config.log_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / (name if name.endswith(".json") else f"{name}.json")
        try:
            payload = self.page.evaluate(_SNAPSHOT_JS)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Structural snapshot saved: %s", path.name)
        except Exception:
            logger.exception("Could not save a structural snapshot")


def _clean_text(value: str) -> str:
    """Trim the ends of a field and keep internal line breaks."""

    return value.replace("\r\n", "\n").replace("\r", "\n").strip()
