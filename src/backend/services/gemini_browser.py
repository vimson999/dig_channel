import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


FEATURE_NAME = "report_pdf_gemini"
QUEUE_NAME = "report_pdf_gemini"
LEGACY_FEATURE_NAME = "gemini_browser"
LEGACY_QUEUE_NAME = "gemini_browser"
DEFAULT_GEMINI_URL = "https://gemini.google.com/gem/099a92ae7888"


class GeminiBrowserError(RuntimeError):
    pass


class GeminiBrowserConfigError(GeminiBrowserError):
    pass


class GeminiBrowserAutomationError(GeminiBrowserError):
    pass


@dataclass
class GeminiBrowserOptions:
    gemini_url: str = DEFAULT_GEMINI_URL
    user_data_dir: str = ""
    profile_directory: str = ""
    chrome_executable_path: str = ""
    chrome_channel: str = ""
    headless: bool = False
    response_timeout_seconds: int = 180
    response_stable_seconds: float = 3.0
    total_timeout_seconds: int = 300
    ready_timeout_seconds: int = 90
    selectors: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "GeminiBrowserOptions":
        return cls(
            gemini_url=_env("REPORT_PDF_GEMINI_URL", "GEMINI_BROWSER_URL", DEFAULT_GEMINI_URL),
            user_data_dir=_effective_user_data_dir(
                _env("REPORT_PDF_GEMINI_USER_DATA_DIR", "GEMINI_BROWSER_USER_DATA_DIR", _default_user_data_dir()),
                _default_user_data_dir(),
                _legacy_user_data_dir(),
            ),
            profile_directory=_env("REPORT_PDF_GEMINI_PROFILE_DIRECTORY", "GEMINI_BROWSER_PROFILE_DIRECTORY", ""),
            chrome_executable_path=_env("REPORT_PDF_GEMINI_CHROME_PATH", "GEMINI_BROWSER_CHROME_PATH", ""),
            chrome_channel=_env("REPORT_PDF_GEMINI_CHANNEL", "GEMINI_BROWSER_CHANNEL", ""),
            headless=_truthy_env("REPORT_PDF_GEMINI_HEADLESS", _truthy_env("GEMINI_BROWSER_HEADLESS", False)),
            response_timeout_seconds=_int_env("REPORT_PDF_GEMINI_RESPONSE_TIMEOUT", _int_env("GEMINI_BROWSER_RESPONSE_TIMEOUT", 180)),
            response_stable_seconds=_float_env("REPORT_PDF_GEMINI_RESPONSE_STABLE_SECONDS", _float_env("GEMINI_BROWSER_RESPONSE_STABLE_SECONDS", 3.0)),
            total_timeout_seconds=_int_env("REPORT_PDF_GEMINI_TOTAL_TIMEOUT", _int_env("GEMINI_BROWSER_TOTAL_TIMEOUT", 300)),
            ready_timeout_seconds=_int_env("REPORT_PDF_GEMINI_READY_TIMEOUT", _int_env("GEMINI_BROWSER_READY_TIMEOUT", 90)),
            selectors=_load_selectors_from_env(),
        )


def is_configured(options: Optional[GeminiBrowserOptions] = None) -> bool:
    options = options or GeminiBrowserOptions.from_env()
    return bool(options.user_data_dir)


def status_payload(options: Optional[GeminiBrowserOptions] = None) -> Dict[str, Any]:
    options = options or GeminiBrowserOptions.from_env()
    user_data_dir = Path(options.user_data_dir).expanduser() if options.user_data_dir else None
    profile_name = options.profile_directory or "Default"
    profile_dir = user_data_dir / profile_name if user_data_dir else None
    chrome_path = Path(options.chrome_executable_path).expanduser() if options.chrome_executable_path else None
    return {
        "configured": is_configured(options),
        "ready": False,
        "gemini_url": options.gemini_url,
        "requires_login_profile": True,
        "user_data_dir_configured": bool(options.user_data_dir),
        "user_data_dir_exists": bool(user_data_dir and user_data_dir.exists()),
        "profile_directory": options.profile_directory,
        "profile_directory_effective": profile_name,
        "profile_directory_exists": bool(profile_dir and profile_dir.exists()),
        "chrome_path_configured": bool(options.chrome_executable_path),
        "chrome_path_exists": bool(chrome_path and chrome_path.exists()),
        "chrome_channel": options.chrome_channel,
        "mode": "real_browser",
        "business_feature": FEATURE_NAME,
        "automation_engine": "gemini_browser",
    }


class GeminiBrowserWorker:
    def __init__(self, options: Optional[GeminiBrowserOptions] = None):
        self.options = options or GeminiBrowserOptions.from_env()
        self._validate_options()

    def generate(
        self,
        *,
        job_id: str,
        prompt: str,
        output_path: str,
        pdf_path: Optional[str] = None,
        target_url: Optional[str] = None,
        followup_prompts: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        followups = [str(item).strip() for item in (followup_prompts or []) if str(item).strip()]
        pdf_file = Path(pdf_path).expanduser() if pdf_path else None
        if not prompt and not pdf_file:
            raise ValueError("Prompt 和 PDF 不能同时为空")
        if pdf_file and (not pdf_file.exists() or not pdf_file.is_file()):
            raise GeminiBrowserConfigError(f"PDF 文件不存在: {pdf_file}")

        started = time.monotonic()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GeminiBrowserConfigError("缺少 playwright 依赖，请安装 requirements 后执行 playwright install") from exc

        url = (target_url or self.options.gemini_url or DEFAULT_GEMINI_URL).strip()
        self._emit(progress_callback, "prepare_browser", 5, "启动研报 PDF Gem 浏览器")
        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(10_000)

            try:
                self._emit(progress_callback, "open_page", 12, "打开 Gemini 页面")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._wait_for_interactive_page(page, started)
                self._raise_if_login_or_captcha(page)
                self._guard_total_timeout(started)

                baseline_text = self._body_text(page)
                if pdf_file:
                    self._emit(progress_callback, "upload_pdf", 25, "上传 PDF 到 Gemini")
                    self._upload_pdf(page, str(pdf_file), started)
                    self._guard_total_timeout(started)

                self._emit(progress_callback, "submit_prompt", 35, "提交 Gemini 任务")
                self._submit_prompt(page, prompt)
                self._guard_total_timeout(started)

                self._emit(progress_callback, "wait_response", 48, "等待 Gemini 回复")
                response = self._wait_for_response(page, prompt, baseline_text, started)
                turns = [{
                    "index": 1,
                    "kind": "initial",
                    "prompt": prompt,
                    "response_text": response["text"],
                    "page_url": page.url,
                    "completed_at": datetime.utcnow().isoformat() + "Z",
                }]
                self._emit(
                    progress_callback,
                    "initial_response",
                    55,
                    "第一轮 Gemini 回复已获取",
                    result={
                        "initial_response_text": response["text"],
                        "response_text": response["text"],
                        "turns": turns,
                    },
                )

                for index, followup in enumerate(followups, start=2):
                    progress_base = min(88, 50 + (index - 1) * 10)
                    baseline_text = self._body_text(page)
                    self._emit(
                        progress_callback,
                        f"followup_{index - 1}_submit",
                        progress_base,
                        f"提交第 {index - 1} 个追问",
                    )
                    self._submit_prompt(page, followup)
                    self._guard_total_timeout(started)

                    self._emit(
                        progress_callback,
                        f"followup_{index - 1}_wait",
                        min(92, progress_base + 5),
                        f"等待第 {index - 1} 个追问回复",
                    )
                    followup_response = self._wait_for_response(page, followup, baseline_text, started)
                    turns.append({
                        "index": index,
                        "kind": "followup",
                        "prompt": followup,
                        "response_text": followup_response["text"],
                        "page_url": page.url,
                        "completed_at": datetime.utcnow().isoformat() + "Z",
                    })
                    self._emit(
                        progress_callback,
                        f"followup_{index - 1}_response",
                        min(94, progress_base + 8),
                        f"第 {index - 1} 个追问回复已获取",
                        result={
                            "response_text": turns[-1]["response_text"],
                            "final_response_text": turns[-1]["response_text"],
                            "turns": turns,
                        },
                    )

                result = {
                    "prompt": prompt,
                    "followup_prompts": followups,
                    "pdf": {
                        "path": str(pdf_file) if pdf_file else None,
                        "filename": pdf_file.name if pdf_file else None,
                        "bytes": pdf_file.stat().st_size if pdf_file else None,
                    } if pdf_file else None,
                    "initial_response_text": response["text"],
                    "response_text": turns[-1]["response_text"],
                    "final_response_text": turns[-1]["response_text"],
                    "turns": turns,
                    "page_url": page.url,
                    "target_url": url,
                    "completed_at": datetime.utcnow().isoformat() + "Z",
                }
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result["output_path"] = str(output)
                self._emit(progress_callback, "save_result", 96, "Gemini 结果已保存", result={"output_path": str(output)})
                return result
            except PlaywrightTimeoutError as exc:
                screenshot = self._save_failure_screenshot(page, job_id)
                raise GeminiBrowserAutomationError(f"页面自动化超时: {exc}; screenshot={screenshot}") from exc
            except Exception:
                self._save_failure_screenshot(page, job_id)
                raise
            finally:
                context.close()

    def _validate_options(self) -> None:
        if not self.options.user_data_dir:
            raise GeminiBrowserConfigError("缺少 REPORT_PDF_GEMINI_USER_DATA_DIR 或 GEMINI_BROWSER_USER_DATA_DIR")
        user_data_dir = Path(self.options.user_data_dir).expanduser()
        if self.options.profile_directory and not (user_data_dir / self.options.profile_directory).exists():
            raise GeminiBrowserConfigError(
                f"Chrome profile 目录不存在: {user_data_dir / self.options.profile_directory}"
            )
        if self.options.headless:
            raise GeminiBrowserConfigError("研报 PDF Gem 浏览器自动化建议使用非 headless profile 以复用登录态")

    def _launch_context(self, playwright):
        user_data_dir = str(Path(self.options.user_data_dir).expanduser())
        args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--use-mock-keychain",
        ]
        if self.options.profile_directory:
            args.append(f"--profile-directory={self.options.profile_directory}")
        kwargs = {
            "headless": False,
            "accept_downloads": True,
            "args": args,
        }
        if self.options.chrome_executable_path:
            kwargs["executable_path"] = self.options.chrome_executable_path
        if self.options.chrome_channel:
            kwargs["channel"] = self.options.chrome_channel
        return playwright.chromium.launch_persistent_context(user_data_dir, **kwargs)

    def _wait_for_interactive_page(self, page, started_at: float) -> None:
        deadline = time.monotonic() + self.options.ready_timeout_seconds
        last_text = ""

        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)

            input_locator = self._last_visible(page, self._selectors("input"))
            if input_locator is not None:
                return

            last_text = self._body_text(page)
            time.sleep(1)

        sample = _clean_text(last_text)[:200]
        raise GeminiBrowserAutomationError(
            f"Gemini 页面未加载完成，未找到输入区; text_sample={sample}"
        )

    def _upload_pdf(self, page, pdf_path: str, started_at: float) -> None:
        self._guard_total_timeout(started_at)
        direct_error = None
        try:
            inputs = page.locator("input[type='file']")
            count = inputs.count()
            for index in range(count - 1, -1, -1):
                candidate = inputs.nth(index)
                try:
                    candidate.set_input_files(pdf_path, timeout=10_000)
                    self._wait_for_upload_ready(page, Path(pdf_path).name, started_at)
                    return
                except Exception as exc:
                    direct_error = exc
        except Exception as exc:
            direct_error = exc

        attach_button = self._last_visible(page, self._selectors("attach"))
        if attach_button is None:
            raise GeminiBrowserAutomationError(f"未找到 Gemini 文件上传入口; direct_error={direct_error}")

        attach_error = None
        try:
            with page.expect_file_chooser(timeout=5_000) as chooser_info:
                attach_button.click(timeout=10_000)
            chooser_info.value.set_files(pdf_path)
            self._wait_for_upload_ready(page, Path(pdf_path).name, started_at)
            return
        except Exception as exc:
            attach_error = exc

        upload_option = self._last_visible(page, self._selectors("upload_file_option"))
        if upload_option is None:
            raise GeminiBrowserAutomationError(
                f"未找到 Gemini 文件上传菜单项; direct_error={direct_error}; attach_error={attach_error}"
            )

        try:
            with page.expect_file_chooser(timeout=10_000) as chooser_info:
                upload_option.click(timeout=10_000)
            chooser_info.value.set_files(pdf_path)
            self._wait_for_upload_ready(page, Path(pdf_path).name, started_at)
        except Exception as exc:
            raise GeminiBrowserAutomationError(f"PDF 上传失败: {exc}") from exc

    def _wait_for_upload_ready(self, page, filename: str, started_at: float) -> None:
        deadline = time.monotonic() + 90
        name = filename.lower()
        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            state = page.evaluate(
                """(filename) => {
                    const text = (document.body && document.body.innerText || "").toLowerCase();
                    const hasName = filename ? text.includes(filename.toLowerCase()) : false;
                    const busyMarkers = [
                        "uploading",
                        "processing",
                        "正在上传",
                        "正在处理",
                        "上传中",
                        "处理中"
                    ];
                    const busy = busyMarkers.some((marker) => text.includes(marker));
                    const sendButtons = Array.from(document.querySelectorAll("button")).filter((button) => {
                        const label = [
                            button.getAttribute("aria-label"),
                            button.getAttribute("title"),
                            button.innerText
                        ].filter(Boolean).join(" ").toLowerCase();
                        return label.includes("send") || label.includes("发送") || label.includes("submit");
                    });
                    const enabledSend = sendButtons.some((button) => !button.disabled);
                    return { hasName, busy, enabledSend };
                }""",
                name,
            )
            if isinstance(state, dict) and (state.get("hasName") or state.get("enabledSend")) and not state.get("busy"):
                return
            time.sleep(1)
        raise GeminiBrowserAutomationError("等待 Gemini PDF 上传完成超时")

    def _submit_prompt(self, page, prompt: str) -> None:
        input_locator = self._last_visible(page, self._selectors("input"))
        if input_locator is None:
            raise GeminiBrowserAutomationError("未找到 Gemini 输入框")

        input_locator.click(timeout=10_000)
        if prompt:
            try:
                input_locator.fill(prompt, timeout=10_000)
            except Exception:
                input_locator.evaluate(
                    """(node, value) => {
                        node.focus();
                        if ("value" in node) {
                            node.value = value;
                        } else {
                            node.textContent = value;
                        }
                        node.dispatchEvent(new InputEvent("input", {
                            bubbles: true,
                            inputType: "insertText",
                            data: value
                        }));
                    }""",
                    prompt,
                )

        send_button = self._wait_for_send_button(page)
        if send_button is not None:
            send_button.click(timeout=10_000)
            return
        input_locator.press("Enter", timeout=10_000)

    def _wait_for_send_button(self, page):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            button = self._last_visible(page, self._selectors("send"))
            if button is not None:
                try:
                    if button.is_enabled(timeout=500):
                        return button
                except Exception:
                    return button
            time.sleep(0.3)
        return None

    def _wait_for_response(self, page, prompt: str, baseline_text: str, started_at: float) -> Dict[str, Any]:
        deadline = time.monotonic() + self.options.response_timeout_seconds
        last_text = ""
        stable_since = time.monotonic()

        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            candidate = self._latest_response_text(page, prompt, baseline_text)
            if candidate:
                if candidate != last_text:
                    last_text = candidate
                    stable_since = time.monotonic()
                elif (
                    time.monotonic() - stable_since >= self.options.response_stable_seconds
                    and not self._is_generating(page)
                ):
                    return {"text": candidate}
            time.sleep(1)

        if last_text:
            return {"text": last_text}
        raise GeminiBrowserAutomationError("等待 Gemini 回复超时")

    def _latest_response_text(self, page, prompt: str, baseline_text: str) -> str:
        candidates = page.evaluate(
            """(selectors) => {
                const visible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style && style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                };
                const clean = (text) => String(text || "").replace(/\\s+/g, " ").trim();
                const values = [];
                const seen = new Set();
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (!visible(node)) continue;
                        const text = clean(node.innerText || node.textContent || "");
                        if (!text || seen.has(text)) continue;
                        seen.add(text);
                        values.push(text);
                    }
                }
                return values;
            }""",
            self._selectors("response"),
        )
        if not isinstance(candidates, list):
            candidates = []

        normalized_prompt = _clean_text(prompt)
        normalized_baseline = _clean_text(baseline_text)
        filtered = []
        for raw in candidates:
            text = _clean_text(str(raw))
            if not text:
                continue
            text = self._strip_prompt_from_candidate(text, normalized_prompt)
            if normalized_baseline and text and text in normalized_baseline:
                continue
            if self._looks_like_response(text, normalized_prompt):
                filtered.append(text)
        if filtered:
            return filtered[-1]

        body = _clean_text(self._body_text(page))
        baseline = _clean_text(baseline_text)
        if baseline and body.startswith(baseline):
            body = body[len(baseline):].strip()
        body = self._strip_prompt_from_candidate(body, normalized_prompt)
        return body if self._looks_like_response(body, normalized_prompt) else ""

    def _strip_prompt_from_candidate(self, text: str, prompt: str) -> str:
        if not prompt:
            return text.strip()
        if text == prompt:
            return ""
        if text.startswith(prompt):
            return text[len(prompt):].strip(" \n:-")
        marker_index = text.rfind(prompt)
        if marker_index >= 0:
            return text[marker_index + len(prompt):].strip(" \n:-")
        return text.strip()

    def _looks_like_response(self, text: str, prompt: str) -> bool:
        if not text or len(text) < 2:
            return False
        lowered = text.lower()
        blocked_markers = (
            "sign in",
            "login",
            "privacy",
            "terms",
            "ask gemini",
            "enter a prompt",
            "gemini can make mistakes",
            "登录",
            "使用 google 账号",
            "输入提示",
            "发送",
        )
        if any(marker in lowered for marker in blocked_markers):
            return False
        if prompt and text == prompt:
            return False
        return True

    def _is_generating(self, page) -> bool:
        return bool(page.evaluate(
            """(selectors) => {
                const visible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style && style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                };
                return selectors.some((selector) => {
                    try {
                        return Array.from(document.querySelectorAll(selector)).some(visible);
                    } catch {
                        return false;
                    }
                });
            }""",
            self._selectors("generating"),
        ))

    def _body_text(self, page) -> str:
        try:
            return page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return ""

    def _selectors(self, key: str) -> List[str]:
        defaults = {
            "input": [
                "rich-textarea textarea",
                "textarea",
                "[contenteditable='true']",
                "[role='textbox']",
                "div.ql-editor",
            ],
            "send": [
                "button[aria-label*='Send']",
                "button[aria-label*='Submit']",
                "button[aria-label*='发送']",
                "button[title*='Send']",
                "button[title*='发送']",
                "[data-testid*='send']",
            ],
            "attach": [
                "button[aria-label*='Add files']",
                "button[aria-label*='Add file']",
                "button[aria-label*='Add image']",
                "button[aria-label*='Add photo']",
                "button[aria-label*='Attach files']",
                "button[aria-label*='Upload']",
                "button[aria-label*='Attach']",
                "button[aria-label*='More upload']",
                "button[aria-label*='Open upload']",
                "button[aria-label*='Insert file']",
                "button[aria-label*='添加文件']",
                "button[aria-label*='添加']",
                "button[aria-label*='上传文件']",
                "button[aria-label*='上传']",
                "button[aria-label*='附件']",
                "button[title*='Add files']",
                "button[title*='Add file']",
                "button[title*='Add image']",
                "button[title*='Add photo']",
                "button[title*='Attach files']",
                "button[title*='Upload']",
                "button[title*='Attach']",
                "button[title*='Open upload']",
                "button[title*='Insert file']",
                "button[title*='添加文件']",
                "button[title*='添加']",
                "button[title*='上传文件']",
                "button[title*='上传']",
                "button[title*='附件']",
                "button:has-text('添加文件')",
                "button:has-text('上传文件')",
                "button:has(mat-icon:has-text('add'))",
                "button:has(mat-icon:has-text('attach_file'))",
                "button:has(mat-icon:has-text('add_photo_alternate'))",
                "[data-testid*='upload']",
                "[data-testid*='attach']",
            ],
            "upload_file_option": [
                "[role='menuitem']:has-text('上传文件')",
                "[role='menuitem']:has-text('上传')",
                "[role='menuitem']:has-text('Upload files')",
                "[role='menuitem']:has-text('Upload file')",
                "[role='menuitem']:has-text('Upload')",
                "button:has-text('上传文件')",
                "button:has-text('Upload files')",
                "button:has-text('Upload file')",
                "button:has-text('Upload')",
                "div[role='button']:has-text('上传文件')",
                "div[role='button']:has-text('Upload files')",
                "div[role='button']:has-text('Upload file')",
                "div[role='button']:has-text('Upload')",
                "mat-option:has-text('上传文件')",
                "mat-option:has-text('Upload files')",
            ],
            "response": [
                "message-content",
                "[data-response-index]",
                "[data-testid*='response']",
                "[class*='model-response']",
                "[class*='response-container']",
                "main article",
                "article",
            ],
            "login": [
                "text=Sign in",
                "text=Sign into",
                "text=Login",
                "text=登录",
                "text=使用 Google 账号",
            ],
            "captcha": [
                "text=Captcha",
                "text=I'm not a robot",
                "text=Verify it’s you",
                "text=Verify it's you",
                "text=unusual traffic",
                "text=安全验证",
                "text=验证身份",
                "text=验证码",
            ],
            "generating": [
                "button[aria-label*='Stop']",
                "button[aria-label*='停止']",
                "[role='progressbar']",
                ".mat-mdc-progress-spinner",
                "mat-spinner",
            ],
        }
        configured = self.options.selectors.get(key) or []
        return configured + defaults.get(key, [])

    def _first_visible(self, page, selectors: List[str]):
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible(timeout=500):
                    return locator
            except Exception:
                continue
        return None

    def _last_visible(self, page, selectors: List[str]):
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = locator.count()
                for index in range(count - 1, -1, -1):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        return candidate
            except Exception:
                continue
        return None

    def _raise_if_login_or_captcha(self, page) -> None:
        if self._first_visible(page, self._selectors("captcha")) is not None:
            raise GeminiBrowserAutomationError("Gemini 页面出现验证码或安全验证，需要人工处理")
        if self._first_visible(page, self._selectors("login")) is not None:
            raise GeminiBrowserAutomationError("Gemini 登录态失效，需要先在浏览器 profile 中登录")

    def _save_failure_screenshot(self, page, job_id: str) -> str:
        try:
            path = Path(_project_root()) / "logs" / "report_pdf_gemini" / f"{job_id}_failed.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return ""

    def _guard_total_timeout(self, started_at: float) -> None:
        if time.monotonic() - started_at > self.options.total_timeout_seconds:
            raise GeminiBrowserAutomationError("任务总超时")

    def _emit(
        self,
        callback: Optional[Callable[[Dict[str, Any]], None]],
        step: str,
        progress: int,
        message: str,
        *,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        if callback:
            callback({
                "step": step,
                "progress": progress,
                "message": message,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "result": result or {},
            })


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / "report_pdf_gemini" / created_at.strftime("%Y%m%d")
    return str(root / f"{job_id}.json")


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])


def _default_user_data_dir() -> str:
    return str(Path(_project_root()) / "runtime" / "browser_profiles" / "report_pdf_gemini")


def _legacy_user_data_dir() -> str:
    return str(Path(_project_root()) / "data" / "browser_profiles" / "gemini")


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env(primary: str, fallback: str, default: str) -> str:
    value = os.getenv(primary)
    if value is not None and str(value).strip() != "":
        return str(value).strip()
    value = os.getenv(fallback)
    if value is not None and str(value).strip() != "":
        return str(value).strip()
    return default


def _effective_user_data_dir(configured: str, default: str, legacy: str) -> str:
    value = str(configured or "").strip() or default
    value_path = Path(value).expanduser()
    legacy_path = Path(legacy).expanduser()
    if not value_path.exists() and legacy_path.exists():
        return str(legacy_path)
    return value


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _load_selectors_from_env() -> Dict[str, List[str]]:
    raw = os.getenv("REPORT_PDF_GEMINI_SELECTORS_JSON") or os.getenv("GEMINI_BROWSER_SELECTORS_JSON", "")
    if not raw:
        path = os.getenv("REPORT_PDF_GEMINI_SELECTORS_FILE") or os.getenv("GEMINI_BROWSER_SELECTORS_FILE", "")
        if path and Path(path).expanduser().exists():
            raw = Path(path).expanduser().read_text(encoding="utf-8")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiBrowserConfigError(f"选择器 JSON 解析失败: {exc}") from exc
    if not isinstance(parsed, dict):
        return {}
    selectors: Dict[str, List[str]] = {}
    for key, value in parsed.items():
        if isinstance(value, str):
            selectors[key] = [value]
        elif isinstance(value, list):
            selectors[key] = [str(item) for item in value if str(item).strip()]
    return selectors
