import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import wave
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.workflow_notification_service import workflow_notification_service


logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


FEATURE_NAME = "doubao_browser_tts"
QUEUE_NAME = "doubao_browser_tts"
DEFAULT_DOUBAO_URL = "https://www.doubao.com/chat/"
DEFAULT_SAMPLE_RATE = 24000
DOUBAO_REPEAT_PROMPT_PREFIX = "请一字不差重复下面的内容，不要总结、不要改写、不要添加开场白或解释："
MIN_DURATION_CHECK_CHARS = 80
SHORT_DURATION_CHECK_CHARS = 20
SHORT_TEXT_MIN_AUDIO_SECONDS = 1.0
MAX_REASONABLE_TTS_CHARS_PER_SECOND = 25.0
_ACTIVE_PROFILE_LOCKS: Dict[str, Dict[str, Any]] = {}


class DoubaoBrowserTTSError(RuntimeError):
    pass


class DoubaoBrowserTTSConfigError(DoubaoBrowserTTSError):
    pass


class DoubaoBrowserTTSAutomationError(DoubaoBrowserTTSError):
    pass


class DoubaoBrowserTTSCaptchaError(DoubaoBrowserTTSAutomationError):
    def __init__(
        self,
        message: str,
        *,
        screenshot: str = "",
        page_url: str = "",
        profile: str = "",
        worker_id: str = "",
    ):
        super().__init__(message)
        self.screenshot = screenshot
        self.page_url = page_url
        self.profile = profile
        self.worker_id = worker_id


@dataclass
class DoubaoBrowserTTSOptions:
    doubao_url: str = DEFAULT_DOUBAO_URL
    user_data_dir: str = ""
    profile_directory: str = ""
    extension_path: str = ""
    chrome_executable_path: str = ""
    headless: bool = False
    sample_rate: int = DEFAULT_SAMPLE_RATE
    response_timeout_seconds: int = 120
    audio_start_timeout_seconds: int = 30
    audio_stable_seconds: float = 2.5
    audio_capture_timeout_seconds: int = 90
    total_timeout_seconds: int = 300
    captcha_wait_timeout_seconds: int = 300
    selectors: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "DoubaoBrowserTTSOptions":
        selectors = _load_selectors_from_env()
        return cls(
            doubao_url=os.getenv("DOUBAO_BROWSER_TTS_URL", DEFAULT_DOUBAO_URL),
            user_data_dir=_effective_user_data_dir(
                os.getenv("DOUBAO_BROWSER_TTS_USER_DATA_DIR"),
                _default_user_data_dir(),
                _legacy_user_data_dir(),
            ),
            profile_directory=os.getenv("DOUBAO_BROWSER_TTS_PROFILE_DIRECTORY", ""),
            extension_path=os.getenv("DOUBAO_BROWSER_TTS_EXTENSION_PATH", _bundled_extension_path()),
            chrome_executable_path=os.getenv("DOUBAO_BROWSER_TTS_CHROME_PATH", ""),
            headless=_truthy_env("DOUBAO_BROWSER_TTS_HEADLESS", False),
            sample_rate=_int_env("DOUBAO_BROWSER_TTS_SAMPLE_RATE", DEFAULT_SAMPLE_RATE),
            response_timeout_seconds=_int_env("DOUBAO_BROWSER_TTS_RESPONSE_TIMEOUT", 120),
            audio_start_timeout_seconds=_int_env("DOUBAO_BROWSER_TTS_AUDIO_START_TIMEOUT", 30),
            audio_stable_seconds=_float_env("DOUBAO_BROWSER_TTS_AUDIO_STABLE_SECONDS", 2.5),
            audio_capture_timeout_seconds=_int_env("DOUBAO_BROWSER_TTS_AUDIO_CAPTURE_TIMEOUT", 90),
            total_timeout_seconds=_int_env("DOUBAO_BROWSER_TTS_TOTAL_TIMEOUT", 300),
            captcha_wait_timeout_seconds=_int_env("DOUBAO_BROWSER_TTS_CAPTCHA_WAIT_TIMEOUT", 300),
            selectors=selectors,
        )


def is_configured(options: Optional[DoubaoBrowserTTSOptions] = None) -> bool:
    options = options or DoubaoBrowserTTSOptions.from_env()
    return bool(options.user_data_dir and options.extension_path and Path(options.extension_path).expanduser().exists())


def status_payload(options: Optional[DoubaoBrowserTTSOptions] = None) -> Dict[str, Any]:
    options = options or DoubaoBrowserTTSOptions.from_env()
    extension_path = Path(options.extension_path).expanduser() if options.extension_path else None
    user_data_dir = Path(options.user_data_dir).expanduser() if options.user_data_dir else None
    profile_dir = user_data_dir / options.profile_directory if user_data_dir and options.profile_directory else None
    chrome_path = Path(options.chrome_executable_path).expanduser() if options.chrome_executable_path else None
    return {
        "configured": is_configured(options),
        "ready": False,
        "doubao_url": options.doubao_url,
        "sample_rate": options.sample_rate,
        "requires_login_profile": True,
        "user_data_dir_configured": bool(options.user_data_dir),
        "user_data_dir_exists": bool(user_data_dir and user_data_dir.exists()),
        "profile_directory": options.profile_directory,
        "profile_directory_exists": bool(profile_dir and profile_dir.exists()),
        "extension_path_configured": bool(options.extension_path),
        "extension_path_exists": bool(extension_path and extension_path.exists()),
        "chrome_path_configured": bool(options.chrome_executable_path),
        "chrome_path_exists": bool(chrome_path and chrome_path.exists()),
        "mode": "real_browser_with_extension",
        "worker_id": os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID", "default"),
        "profile_pool_root": os.getenv("DOUBAO_BROWSER_TTS_PROFILE_POOL_ROOT", ""),
        "profile_count": _int_env("DOUBAO_BROWSER_TTS_PROFILE_COUNT", 6),
    }


class DoubaoBrowserTTSWorker:
    def __init__(self, options: Optional[DoubaoBrowserTTSOptions] = None):
        self.options = options or DoubaoBrowserTTSOptions.from_env()
        self._validate_options()

    def synthesize(
        self,
        *,
        job_id: str,
        text: str,
        output_path: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("合成文本不能为空")

        started = time.monotonic()
        self._captcha_waited_seconds = 0.0
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DoubaoBrowserTTSConfigError("缺少 playwright 依赖，请安装 requirements 后执行 playwright install") from exc

        with sync_playwright() as playwright:
            self._emit(progress_callback, "wait_browser_profile", 4, "等待豆包浏览器 profile 空闲")
            with _doubao_profile_lock(self.options):
                self._emit(progress_callback, "prepare_browser", 5, "启动真实浏览器")
                context = self._launch_context(playwright)
                self._install_capture_init_script(context)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(10_000)

                try:
                    page.goto(self.options.doubao_url, wait_until="domcontentloaded", timeout=45_000)
                    self._raise_if_login_or_captcha(page, job_id)
                    self._dismiss_known_promotional_dialogs(page)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "clear_audio_cache", 12, "清空插件音频缓存")
                    self._clear_audio(page)
                    self._raise_if_login_or_captcha(page, job_id)

                    self._emit(progress_callback, "submit_text", 25, "提交文本到豆包")
                    self._submit_text(page, text, job_id)
                    self._raise_if_login_or_captcha(page, job_id)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "wait_response", 45, "等待豆包回复完成")
                    read_button = self._wait_for_read_button(page, job_id)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "start_reading", 62, "触发朗读")
                    self._raise_if_login_or_captcha(page, job_id)
                    self._dismiss_known_promotional_dialogs(page)
                    read_button.click(timeout=10_000)

                    self._emit(progress_callback, "capture_audio", 72, "等待音频捕获")
                    audio = self._wait_for_audio(page, started, job_id, text=text)

                    self._emit(progress_callback, "download_wav", 88, "导出 WAV")
                    wav_bytes = self._export_wav_bytes(page, output, job_id)
                    if wav_bytes:
                        output.write_bytes(wav_bytes)

                    if not output.exists() or output.stat().st_size == 0:
                        raise DoubaoBrowserTTSAutomationError("WAV 导出完成但文件为空")

                    file_seconds = _wav_duration_seconds(output)
                    result = {
                        "wav_path": str(output),
                        "bytes": output.stat().st_size,
                        "sample_rate": audio.get("sample_rate") or self.options.sample_rate,
                        "chunks": audio.get("chunks") or 0,
                        "seconds": file_seconds or audio.get("seconds") or 0,
                        "page_url": page.url,
                        "worker_id": os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID", "default"),
                        "user_data_dir": self.options.user_data_dir,
                        "profile_directory": self.options.profile_directory,
                    }
                    self._emit(progress_callback, "save_file", 96, "WAV 已保存", result=result)
                    return result
                except PlaywrightTimeoutError as exc:
                    captcha_error = self._captcha_error(page, job_id)
                    if captcha_error:
                        raise captcha_error from exc
                    screenshot = self._save_failure_screenshot(page, job_id)
                    raise DoubaoBrowserTTSAutomationError(f"页面自动化超时: {exc}; screenshot={screenshot}") from exc
                except DoubaoBrowserTTSCaptchaError:
                    raise
                except Exception:
                    captcha_error = self._captcha_error(page, job_id)
                    if captcha_error:
                        raise captcha_error
                    self._save_failure_screenshot(page, job_id)
                    raise
                finally:
                    context.close()

    def _validate_options(self) -> None:
        if not self.options.user_data_dir:
            raise DoubaoBrowserTTSConfigError("缺少 DOUBAO_BROWSER_TTS_USER_DATA_DIR")
        user_data_dir = Path(self.options.user_data_dir).expanduser()
        if self.options.profile_directory and not (user_data_dir / self.options.profile_directory).exists():
            raise DoubaoBrowserTTSConfigError(
                f"Chrome profile 目录不存在: {user_data_dir / self.options.profile_directory}"
            )
        if not self.options.extension_path:
            raise DoubaoBrowserTTSConfigError("缺少 DOUBAO_BROWSER_TTS_EXTENSION_PATH")
        extension_path = Path(self.options.extension_path).expanduser()
        if not extension_path.exists():
            raise DoubaoBrowserTTSConfigError(f"Chrome 插件路径不存在: {extension_path}")
        if self.options.headless:
            raise DoubaoBrowserTTSConfigError("Chrome 扩展捕获音频需要非 headless 浏览器")

    def _launch_context(self, playwright):
        extension = str(Path(self.options.extension_path).expanduser())
        user_data_dir = str(Path(self.options.user_data_dir).expanduser())
        args = [
            f"--disable-extensions-except={extension}",
            f"--load-extension={extension}",
            "--autoplay-policy=no-user-gesture-required",
            # Keep cookie encryption compatible with the manual profile-login
            # launcher on macOS. Playwright currently supplies these defaults,
            # but making them explicit prevents a future launch-mode mismatch.
            "--password-store=basic",
            "--use-mock-keychain",
            "--no-first-run",
            "--no-default-browser-check",
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
        return playwright.chromium.launch_persistent_context(user_data_dir, **kwargs)

    def _install_capture_init_script(self, context) -> None:
        script_path = self._capture_script_path()
        context.add_init_script(path=str(script_path))

    def _capture_script_path(self) -> Path:
        extension_path = Path(self.options.extension_path).expanduser()
        candidate = extension_path / "injected.js"
        if candidate.exists():
            return candidate
        bundled = Path(_bundled_extension_path()) / "injected.js"
        if bundled.exists():
            return bundled
        raise DoubaoBrowserTTSConfigError("缺少豆包音频捕获 injected.js")

    def _clear_audio(self, page) -> None:
        ok = page.evaluate(
            """async () => {
                if (typeof window.clearDoubaoAudio !== "function") return false;
                await window.clearDoubaoAudio();
                return true;
            }"""
        )
        if not ok:
            raise DoubaoBrowserTTSAutomationError("豆包音频捕获插件未注入或缺少 window.clearDoubaoAudio")

    def _submit_text(self, page, text: str, job_id: str) -> None:
        self._raise_if_login_or_captcha(page, job_id)
        # The desktop-app promotion can be mounted asynchronously after the
        # first page load.  Dismiss it immediately before resolving/clicking
        # the textarea as well, otherwise Playwright may resolve the correct
        # input but fail because the modal still intercepts pointer events.
        self._dismiss_known_promotional_dialogs(page)
        input_locator = self._first_visible(page, self._selectors("input"))
        if input_locator is None:
            raise DoubaoBrowserTTSAutomationError("未找到豆包输入框")

        prompt_text = build_repeat_prompt(text)
        try:
            input_locator.click(timeout=10_000)
        except Exception:
            # A promotion may appear between the dismiss pass and the click.
            # Retry once after closing it; non-promotion failures are re-raised
            # so login/captcha and selector regressions remain visible.
            dismissed = self._dismiss_known_promotional_dialogs(page)
            if not dismissed:
                raise
            input_locator.click(timeout=10_000)
        tag_name = input_locator.evaluate("(node) => node.tagName.toLowerCase()")
        editable = input_locator.evaluate("(node) => Boolean(node.isContentEditable)")
        if tag_name in {"textarea", "input"}:
            input_locator.fill(prompt_text, timeout=10_000)
        elif editable:
            input_locator.evaluate(
                """(node, value) => {
                    node.focus();
                    node.textContent = value;
                    node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
                }""",
                prompt_text,
            )
        else:
            raise DoubaoBrowserTTSAutomationError("豆包输入框不可编辑")

        send_button = self._first_visible(page, self._selectors("send"))
        if send_button is None:
            input_locator.press("Enter", timeout=10_000)
        else:
            send_button.click(timeout=10_000)
        self._raise_if_login_or_captcha(page, job_id)

    def _wait_for_read_button(self, page, job_id: str):
        deadline = time.monotonic() + self.options.response_timeout_seconds
        while time.monotonic() < deadline:
            self._dismiss_known_promotional_dialogs(page)
            button = self._last_visible(page, self._selectors("read"))
            if button is not None:
                return button
            deadline += self._raise_if_login_or_captcha(page, job_id)
            time.sleep(1)
        raise DoubaoBrowserTTSAutomationError("等待豆包回复或朗读按钮超时")

    def _dismiss_known_promotional_dialogs(self, page) -> int:
        """Close Doubao's desktop-app promotion without hiding auth/captcha UI."""
        try:
            return int(page.evaluate(
                """() => {
                    const markers = ["下载电脑版", "使用完整功能", "共享桌面应用", "下次提醒我"];
                    let dismissed = 0;
                    for (const dialog of document.querySelectorAll('[role="dialog"]')) {
                        const text = String(dialog.innerText || "");
                        if (!markers.some((marker) => text.includes(marker))) continue;
                        const box = dialog.getBoundingClientRect();
                        const visible = (node) => {
                            const rect = node.getBoundingClientRect();
                            const style = window.getComputedStyle(node);
                            return rect.width > 0 && rect.height > 0
                                && style.visibility !== "hidden"
                                && style.display !== "none";
                        };
                        const clickTarget = (node) => {
                            const parent = node.closest(
                                'button, [role="button"], [data-slot="dialog-close"], '
                                + '[data-radix-dialog-close]'
                            );
                            (parent || node).click();
                        };
                        const direct = dialog.querySelector(
                            'button[aria-label*="关闭"], button[title*="关闭"], '
                            + '[role="button"][aria-label*="关闭"], [role="button"][title*="关闭"], '
                            + '[data-slot="dialog-close"], [data-radix-dialog-close], '
                            + '[class*="close"], [class*="Close"]'
                        );
                        if (direct && visible(direct)) {
                            clickTarget(direct);
                            dismissed += 1;
                            continue;
                        }
                        // The current Doubao modal renders its top-right X as
                        // an unlabelled element.  Select the smallest visible
                        // control in the dialog's top-right corner, including
                        // role=button/svg wrappers rather than only <button>.
                        const candidates = Array.from(dialog.querySelectorAll(
                            'button, [role="button"], [tabindex], svg, [data-slot], [class*="close"], [class*="Close"]'
                        ))
                            .filter((button) => {
                                const rect = button.getBoundingClientRect();
                                return visible(button)
                                    && rect.width <= 96 && rect.height <= 96
                                    && rect.left >= box.right - 100
                                    && rect.top <= box.top + 100;
                            })
                            .sort((left, right) => {
                                const leftRect = left.getBoundingClientRect();
                                const rightRect = right.getBoundingClientRect();
                                return rightRect.left - leftRect.left || leftRect.width - rightRect.width;
                            });
                        if (candidates[0]) {
                            clickTarget(candidates[0]);
                            dismissed += 1;
                            continue;
                        }
                        // Last-resort hit test for a non-semantic close icon.
                        const hit = document.elementFromPoint(box.right - 28, box.top + 28);
                        if (hit && dialog.contains(hit)) {
                            clickTarget(hit);
                            dismissed += 1;
                        }
                    }
                    return dismissed;
                }"""
            ) or 0)
        except Exception as exc:
            logger.debug("Dismiss Doubao promotional dialog failed: %s", exc)
            return 0

    def _wait_for_audio(self, page, started_at: float, job_id: str, *, text: str = "") -> Dict[str, Any]:
        chars = len(re.sub(r"\s+", "", str(text or "")))
        minimum_seconds = _minimum_expected_audio_seconds(chars)
        first_audio_deadline = time.monotonic() + self.options.audio_start_timeout_seconds
        last_stats = self._audio_stats(page)
        while time.monotonic() < first_audio_deadline:
            self._guard_total_timeout(started_at)
            first_audio_deadline += self._raise_if_login_or_captcha(page, job_id)
            stats = self._audio_stats(page)
            if int(stats.get("chunks") or 0) > 0:
                last_stats = stats
                break
            time.sleep(0.5)
        else:
            raise DoubaoBrowserTTSAutomationError("朗读已触发但未捕获到 PCM 音频")

        capture_timeout = max(
            float(self.options.audio_capture_timeout_seconds),
            minimum_seconds + float(self.options.audio_stable_seconds) + 10.0,
        )
        capture_deadline = time.monotonic() + capture_timeout
        stable_since = time.monotonic()
        last_chunks = int(last_stats.get("chunks") or 0)
        last_seconds = _float_value(last_stats.get("seconds"))
        while time.monotonic() < capture_deadline:
            self._guard_total_timeout(started_at)
            capture_deadline += self._raise_if_login_or_captcha(page, job_id)
            stats = self._audio_stats(page)
            chunks = int(stats.get("chunks") or 0)
            seconds = _float_value(stats.get("seconds"))
            if chunks != last_chunks or abs(seconds - last_seconds) >= 0.05:
                last_chunks = chunks
                last_seconds = seconds
                stable_since = time.monotonic()
                last_stats = stats
            elif minimum_seconds > 0 and seconds < minimum_seconds:
                stable_since = time.monotonic()
                last_stats = stats
            elif time.monotonic() - stable_since >= self.options.audio_stable_seconds:
                return stats
            time.sleep(0.5)
        return last_stats

    def _export_wav_bytes(self, page, output_path: Path, job_id: str) -> bytes:
        # Prefer the browser download path. Returning a Blob as
        # ``Array.from(Uint8Array)`` serializes every byte as a JSON number over
        # the Playwright protocol; a several-megabyte WAV can then spend tens
        # of minutes in ``page.evaluate`` and look stale to the parent workflow.
        has_download = page.evaluate(
            """() => typeof window.downloadDoubaoWav === 'function'"""
        )
        if has_download:
            with page.expect_download(timeout=30_000) as download_info:
                ok = page.evaluate(
                    """async (filename) => {
                        if (typeof window.downloadDoubaoWav !== "function") return false;
                        await window.downloadDoubaoWav(filename);
                        return true;
                    }""",
                    output_path.name,
                )
                if not ok:
                    raise DoubaoBrowserTTSAutomationError("插件调用 downloadDoubaoWav 失败")
            download_info.value.save_as(str(output_path))
            return b""

        # Compatibility fallback for an older capture script without the
        # download helper. Base64 keeps the protocol payload bounded compared
        # with a JSON array containing one integer per byte.
        raw = page.evaluate(
            """async () => {
                const candidateFns = [
                    window.exportDoubaoWav,
                    window.getDoubaoWavBlob,
                    window.buildDoubaoWavBlob
                ].filter((fn) => typeof fn === "function");
                for (const fn of candidateFns) {
                    const value = await fn();
                    const blob = value instanceof Blob ? value : (value && value.blob instanceof Blob ? value.blob : null);
                    if (blob) {
                        const buffer = await blob.arrayBuffer();
                        const bytes = new Uint8Array(buffer);
                        const chunkSize = 0x8000;
                        let binary = "";
                        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                            binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
                        }
                        return { kind: "base64", data: btoa(binary) };
                    }
                    if (value instanceof ArrayBuffer) {
                        const bytes = new Uint8Array(value);
                        const chunkSize = 0x8000;
                        let binary = "";
                        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                            binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
                        }
                        return { kind: "base64", data: btoa(binary) };
                    }
                    if (typeof value === "string" && value.startsWith("data:audio/wav;base64,")) {
                        return { kind: "base64", data: value.slice("data:audio/wav;base64,".length) };
                    }
                }
                return null;
            }"""
        )
        if raw:
            if raw.get("kind") == "base64":
                return base64.b64decode(raw.get("data") or "")
            return bytes(raw.get("data") or [])
        raise DoubaoBrowserTTSAutomationError(
            "插件未提供 exportDoubaoWav/getDoubaoWavBlob/downloadDoubaoWav"
        )

    def _audio_stats(self, page) -> Dict[str, Any]:
        stats = page.evaluate(
            """async () => {
                if (typeof window.getDoubaoAudioStats === "function") {
                    return await window.getDoubaoAudioStats();
                }
                if (window.__doubaoAudioCaptureStats) return window.__doubaoAudioCaptureStats;
                return {};
            }"""
        )
        if not isinstance(stats, dict):
            return {}
        if "sampleRate" in stats and "sample_rate" not in stats:
            stats["sample_rate"] = stats.get("sampleRate")
        return stats

    def _selectors(self, key: str) -> List[str]:
        defaults = {
            "input": [
                "textarea",
                "[contenteditable='true']",
                "[data-testid*='input']",
                "[class*='input'] [contenteditable='true']",
            ],
            "send": [
                "button[type='submit']",
                "button[aria-label*='发送']",
                "button[title*='发送']",
                "button:has-text('发送')",
                "[data-testid*='send']",
            ],
            "read": [
                "button[aria-label*='朗读']",
                "button[title*='朗读']",
                "button:has-text('朗读')",
                "[aria-label*='播放']",
                "[title*='播放']",
            ],
            "login": [
                'button:has-text("登录")',
                '[role="button"]:has-text("登录")',
                'input[placeholder*="手机号"]',
                'input[placeholder*="验证码"]',
                'button:has-text("获取验证码")',
            ],
            "captcha": [
                "text=人机验证",
                "text=安全验证",
                "text=验证身份",
                "text=请选择所有符合上文描述的图片",
                "text=请选择符合以下描述的图片",
                "text=请选择符合描述的图片",
                "text=请将下列图片中符合描述的选项拖动到下方",
                "text=请将符合描述的图片拖拽到下方",
                "text=拖拽到这里",
                "text=拖动到这里",
                "text=请拖动上方图片到此处",
                "text=拖动滑块",
                "text=滑块验证",
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

    def _captcha_visible(self, page) -> bool:
        # ByteDance's image-selection challenge is commonly rendered in a
        # cross-origin iframe. A page-level locator cannot see into that frame,
        # which previously turned a CAPTCHA into three misleading 120-second
        # "wait for read button" timeouts. Check every frame as well as the
        # main document so the parent workflow can stop retrying immediately.
        roots = [page]
        try:
            roots.extend(frame for frame in page.frames if frame is not page)
        except Exception:
            pass

        for root in roots:
            if self._is_verification_challenge_frame(root):
                return True
            if self._first_visible(root, self._selectors("captcha")) is not None:
                return True
        return False

    @staticmethod
    def _is_verification_challenge_frame(root) -> bool:
        """Detect a verification iframe before its challenge text has loaded."""
        url = str(getattr(root, "url", "") or "").lower()
        return (
            "verifycenter" in url
            or "/captcha/" in url
            or "captcha?" in url
            or "captcha=" in url
        )

    def _captcha_error(self, page, job_id: str) -> Optional[DoubaoBrowserTTSCaptchaError]:
        if not self._captcha_visible(page):
            return None
        screenshot = self._save_failure_screenshot(page, job_id)
        return DoubaoBrowserTTSCaptchaError(
            "豆包页面触发人机验证，浏览器自动化已被阻塞，需要人工进入浏览器完成验证后再重试",
            screenshot=screenshot,
            page_url=getattr(page, "url", "") or "",
            profile=str(self.options.user_data_dir or ""),
            worker_id=str(
                os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID", "")
                or self.options.profile_directory
                or "default"
            ),
        )

    def _wait_for_captcha_resolution(self, page, job_id: str = "") -> float:
        captcha_error = self._captcha_error(page, job_id)
        if not captcha_error:
            return 0.0
        started = time.monotonic()
        deadline = started + max(1, int(self.options.captcha_wait_timeout_seconds))
        logger.warning(
            "Doubao CAPTCHA detected; keeping browser open for manual resolution | job=%s timeout=%ss",
            job_id,
            self.options.captcha_wait_timeout_seconds,
        )
        while time.monotonic() < deadline:
            time.sleep(1)
            if not self._captcha_visible(page):
                waited = max(0.0, time.monotonic() - started)
                self._captcha_waited_seconds = float(getattr(self, "_captcha_waited_seconds", 0.0)) + waited
                logger.info("Doubao CAPTCHA cleared manually; automation continues | job=%s waited=%.1fs", job_id, waited)
                return waited
        raise DoubaoBrowserTTSCaptchaError(
            f"{captcha_error}；浏览器已保留 {self.options.captcha_wait_timeout_seconds} 秒等待人工验证，但验证未完成",
            screenshot=captcha_error.screenshot,
            page_url=captcha_error.page_url,
            profile=captcha_error.profile,
            worker_id=captcha_error.worker_id,
        )

    def _raise_if_login_or_captcha(self, page, job_id: str = "") -> float:
        captcha_waited = self._wait_for_captcha_resolution(page, job_id)
        if self._first_visible(page, self._selectors("login")) is not None:
            message = "豆包登录态失效，需要先在浏览器 profile 中登录"
            screenshot = self._save_failure_screenshot(page, job_id or "browser_auth")
            workflow_notification_service.send_browser_human_intervention(
                provider="豆包",
                reason="login",
                message=message,
                job_id=str(job_id or ""),
                page_url=str(getattr(page, "url", "") or ""),
                screenshot_path=screenshot,
                profile=str(self.options.user_data_dir or ""),
                worker_id=str(
                    os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID", "")
                    or self.options.profile_directory
                    or "default"
                ),
            )
            raise DoubaoBrowserTTSAutomationError(message)
        return captcha_waited

    def _save_failure_screenshot(self, page, job_id: str) -> str:
        try:
            path = Path(_project_root()) / "logs" / "doubao_browser_tts" / f"{job_id}_failed.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return ""

    def _guard_total_timeout(self, started_at: float) -> None:
        captcha_waited = float(getattr(self, "_captcha_waited_seconds", 0.0))
        if time.monotonic() - started_at - captcha_waited > self.options.total_timeout_seconds:
            raise DoubaoBrowserTTSAutomationError("任务总超时")

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


def build_repeat_prompt(text: str) -> str:
    content = str(text or "").strip()
    return f"{DOUBAO_REPEAT_PROMPT_PREFIX}\n\n{content}"


def validate_audio_duration_for_text(audio: Dict[str, Any], text: str) -> Dict[str, Any]:
    result = dict(audio or {})
    seconds = _float_value(result.get("seconds"))
    chars = len(re.sub(r"\s+", "", str(text or "")))
    minimum = _minimum_expected_audio_seconds(chars)
    if minimum > 0 and seconds < minimum:
        raise DoubaoBrowserTTSError(
            f"豆包音频时长异常：文本 {chars} 字，音频 {seconds:.3f}s，"
            f"低于最低预期 {minimum:.1f}s，疑似朗读未完成或捕获到了空音频"
        )
    result["seconds"] = seconds
    return result


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / "doubao_browser_tts" / created_at.strftime("%Y%m%d")
    return str(root / f"{job_id}.wav")


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])


def _bundled_extension_path() -> str:
    return str(Path(_project_root()) / "tools" / "doubao-audio-capture-extension")


def _default_user_data_dir() -> str:
    return str(Path(_project_root()) / "runtime" / "browser_profiles" / "doubao")


def _legacy_user_data_dir() -> str:
    return str(Path(_project_root()) / "runtime" / "doubao-browser-profile")


@contextmanager
def _doubao_profile_lock(options: DoubaoBrowserTTSOptions):
    if fcntl is None:
        yield
        return

    timeout_seconds = max(1, _int_env("DOUBAO_BROWSER_TTS_PROFILE_LOCK_TIMEOUT_SECONDS", 1800))
    user_data_dir = str(Path(options.user_data_dir or "").expanduser())
    profile = options.profile_directory or "Default"
    key = hashlib.sha1(f"{user_data_dir}|{profile}".encode("utf-8")).hexdigest()[:16]
    owner = (os.getpid(), threading.get_ident())
    lock_dir = Path(_project_root()) / "runtime" / "locks" / "doubao_browser_tts"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{key}.lock"
    deadline = time.monotonic() + timeout_seconds

    while True:
        state = _ACTIVE_PROFILE_LOCKS.get(str(lock_path))
        if state:
            if state.get("owner") == owner:
                state["depth"] = int(state.get("depth") or 0) + 1
                try:
                    yield
                finally:
                    state["depth"] = max(0, int(state.get("depth") or 1) - 1)
                return
            if time.monotonic() >= deadline:
                raise DoubaoBrowserTTSAutomationError(
                    f"等待豆包浏览器 profile 空闲超时: {user_data_dir} ({timeout_seconds}s)"
                )
            time.sleep(0.5)
            continue

        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise DoubaoBrowserTTSAutomationError(
                            f"等待豆包浏览器 profile 空闲超时: {user_data_dir} ({timeout_seconds}s)"
                        ) from exc
                    time.sleep(1)

            handle.seek(0)
            handle.truncate()
            handle.write(
                "pid={pid} thread={thread} acquired_at={at} user_data_dir={dir} profile={profile}\n".format(
                    pid=os.getpid(),
                    thread=threading.get_ident(),
                    at=datetime.utcnow().isoformat() + "Z",
                    dir=user_data_dir,
                    profile=profile,
                )
            )
            handle.flush()
            _ACTIVE_PROFILE_LOCKS[str(lock_path)] = {"owner": owner, "depth": 1, "handle": handle}
            try:
                yield
            finally:
                state = _ACTIVE_PROFILE_LOCKS.get(str(lock_path))
                if state and state.get("owner") == owner:
                    depth = max(0, int(state.get("depth") or 1) - 1)
                    if depth > 0:
                        state["depth"] = depth
                    else:
                        _ACTIVE_PROFILE_LOCKS.pop(str(lock_path), None)
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        return


def _effective_user_data_dir(configured: Optional[str], default: str, legacy: str) -> str:
    value = str(configured or "").strip() or default
    value_path = Path(value).expanduser()
    legacy_path = Path(legacy).expanduser()
    if not value_path.exists() and legacy_path.exists():
        return str(legacy_path)
    return value


def _minimum_expected_audio_seconds(chars: int) -> float:
    if chars >= MIN_DURATION_CHECK_CHARS:
        return max(SHORT_TEXT_MIN_AUDIO_SECONDS, chars / MAX_REASONABLE_TTS_CHARS_PER_SECOND)
    if chars >= SHORT_DURATION_CHECK_CHARS:
        return SHORT_TEXT_MIN_AUDIO_SECONDS
    return 0.0


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as reader:
            sample_rate = reader.getframerate()
            if sample_rate <= 0:
                return 0.0
            return reader.getnframes() / sample_rate
    except Exception:
        return 0.0


def _float_value(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
    raw = os.getenv("DOUBAO_BROWSER_TTS_SELECTORS_JSON", "")
    if not raw:
        path = os.getenv("DOUBAO_BROWSER_TTS_SELECTORS_FILE", "")
        if path and Path(path).expanduser().exists():
            raw = Path(path).expanduser().read_text(encoding="utf-8")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DoubaoBrowserTTSConfigError(f"选择器 JSON 解析失败: {exc}") from exc
    if not isinstance(parsed, dict):
        return {}
    selectors: Dict[str, List[str]] = {}
    for key, value in parsed.items():
        if isinstance(value, str):
            selectors[key] = [value]
        elif isinstance(value, list):
            selectors[key] = [str(item) for item in value if str(item).strip()]
    return selectors
