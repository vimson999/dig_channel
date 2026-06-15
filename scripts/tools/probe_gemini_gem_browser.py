#!/usr/bin/env python3
"""Probe a Gemini Gem page with Playwright.

The script opens the target URL, records basic page state, and saves a
screenshot. It does not submit text unless --prompt and --submit are both set.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_URL = "https://gemini.google.com/gem/099a92ae7888"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def visible_locator(page, selectors: Iterable[str], last: bool = False):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except PlaywrightError:
            continue

        indexes = range(count - 1, -1, -1) if last else range(count)
        for index in indexes:
            candidate = locator.nth(index)
            try:
                if candidate.is_visible(timeout=700):
                    return candidate
            except PlaywrightError:
                continue
    return None


def collect_page_state(page) -> dict[str, Any]:
    visible_text = ""
    try:
        visible_text = page.locator("body").inner_text(timeout=5_000)
    except PlaywrightError:
        pass

    lowered = visible_text.lower()
    textbox = visible_locator(
        page,
        [
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
            "div.ql-editor",
            "rich-textarea textarea",
        ],
        last=True,
    )

    return {
        "title": safe_call(page.title),
        "url": page.url,
        "has_textbox": textbox is not None,
        "requires_login": any(
            marker in lowered
            for marker in (
                "sign in",
                "sign into",
                "log in",
                "login",
                "登录",
                "登入",
                "使用 google 账号",
            )
        ),
        "captcha_or_verification": any(
            marker in lowered
            for marker in (
                "captcha",
                "verify",
                "verification",
                "安全验证",
                "验证身份",
                "验证码",
            )
        ),
        "text_sample": visible_text[:1200],
    }


def safe_call(fn, default: Optional[str] = "") -> Optional[str]:
    try:
        return fn()
    except PlaywrightError:
        return default


def maybe_submit_prompt(page, prompt: str) -> dict[str, Any]:
    textbox = visible_locator(
        page,
        [
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
            "div.ql-editor",
            "rich-textarea textarea",
        ],
        last=True,
    )
    if textbox is None:
        return {"submitted": False, "reason": "no visible prompt textbox"}

    textbox.click(timeout=10_000)
    try:
        textbox.fill(prompt, timeout=10_000)
    except PlaywrightError:
        textbox.evaluate(
            """(node, value) => {
                node.focus();
                if ('value' in node) {
                    node.value = value;
                } else {
                    node.textContent = value;
                }
                node.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'insertText',
                    data: value
                }));
            }""",
            prompt,
        )

    send_button = visible_locator(
        page,
        [
            "button[aria-label*='Send']",
            "button[aria-label*='Submit']",
            "button[aria-label*='发送']",
            "button[title*='Send']",
            "button[title*='发送']",
            "[data-testid*='send']",
        ],
        last=True,
    )
    if send_button is not None:
        send_button.click(timeout=10_000)
        return {"submitted": True, "method": "send_button"}

    textbox.press("Enter", timeout=10_000)
    return {"submitted": True, "method": "enter_key"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("GEMINI_BROWSER_URL", DEFAULT_URL))
    parser.add_argument(
        "--user-data-dir",
        default=(
            os.getenv("REPORT_PDF_GEMINI_USER_DATA_DIR")
            or os.getenv("GEMINI_BROWSER_USER_DATA_DIR")
            or str(project_root() / "runtime" / "browser_profiles" / "report_pdf_gemini")
        ),
        help="Persistent browser profile. Log in once here if Gemini asks.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv(
            "GEMINI_BROWSER_OUTPUT_DIR",
            str(project_root() / "logs" / "gemini_browser_probe"),
        ),
    )
    parser.add_argument("--chrome-path", default=os.getenv("GEMINI_BROWSER_CHROME_PATH", ""))
    parser.add_argument("--channel", default=os.getenv("GEMINI_BROWSER_CHANNEL", ""))
    parser.add_argument("--headless", action="store_true", default=env_bool("GEMINI_BROWSER_HEADLESS", False))
    parser.add_argument("--hold-seconds", type=int, default=env_int("GEMINI_BROWSER_HOLD_SECONDS", 20))
    parser.add_argument("--prompt", default=os.getenv("GEMINI_BROWSER_PROMPT", ""))
    parser.add_argument(
        "--submit",
        action="store_true",
        default=env_bool("GEMINI_BROWSER_SUBMIT", False),
        help="Send --prompt to Gemini. Without this flag, no text is submitted.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    screenshot_path = output_dir / f"gemini_probe_{timestamp}.png"
    state_path = output_dir / f"gemini_probe_{timestamp}.json"

    result: dict[str, Any] = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "target_url": args.url,
        "user_data_dir": str(Path(args.user_data_dir).expanduser()),
        "headless": args.headless,
        "screenshot_path": str(screenshot_path),
        "state_path": str(state_path),
    }

    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "headless": args.headless,
            "accept_downloads": True,
        }
        if args.chrome_path:
            launch_kwargs["executable_path"] = args.chrome_path
        if args.channel:
            launch_kwargs["channel"] = args.channel

        context = playwright.chromium.launch_persistent_context(
            str(Path(args.user_data_dir).expanduser()),
            **launch_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(10_000)

        try:
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=45_000)
                result["goto"] = "ok"
            except PlaywrightTimeoutError as exc:
                result["goto"] = "timeout"
                result["goto_error"] = str(exc)

            page.wait_for_timeout(3_000)
            result["page"] = collect_page_state(page)

            if args.prompt and args.submit:
                result["prompt_action"] = maybe_submit_prompt(page, args.prompt)
                page.wait_for_timeout(3_000)
                result["page_after_prompt"] = collect_page_state(page)
            elif args.prompt:
                result["prompt_action"] = {
                    "submitted": False,
                    "reason": "--prompt was provided without --submit",
                }

            page.screenshot(path=str(screenshot_path), full_page=True)

            if args.hold_seconds > 0 and not args.headless:
                print(f"Holding browser open for {args.hold_seconds}s. Press Ctrl-C to end early.")
                time.sleep(args.hold_seconds)
        finally:
            context.close()

    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    state_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
