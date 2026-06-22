import json
import os
import time
import base64
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


FEATURE_NAME = "report_pdf_gemini"
QUEUE_NAME = "report_pdf_gemini"
LEGACY_FEATURE_NAME = "gemini_browser"
LEGACY_QUEUE_NAME = "gemini_browser"
DEFAULT_GEMINI_URL = "https://gemini.google.com/gem/099a92ae7888"
_ACTIVE_PROFILE_LOCKS: Dict[str, Dict[str, Any]] = {}


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
    response_timeout_seconds: int = 600
    image_timeout_seconds: int = 360
    response_stable_seconds: float = 3.0
    total_timeout_seconds: int = 900
    ready_timeout_seconds: int = 90
    upload_timeout_seconds: int = 300
    min_response_chars: int = 120
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
            response_timeout_seconds=_int_env("REPORT_PDF_GEMINI_RESPONSE_TIMEOUT", _int_env("GEMINI_BROWSER_RESPONSE_TIMEOUT", 600)),
            image_timeout_seconds=_int_env("REPORT_PDF_GEMINI_IMAGE_TIMEOUT", _int_env("GEMINI_BROWSER_IMAGE_TIMEOUT", 360)),
            response_stable_seconds=_float_env("REPORT_PDF_GEMINI_RESPONSE_STABLE_SECONDS", _float_env("GEMINI_BROWSER_RESPONSE_STABLE_SECONDS", 3.0)),
            total_timeout_seconds=_int_env("REPORT_PDF_GEMINI_TOTAL_TIMEOUT", _int_env("GEMINI_BROWSER_TOTAL_TIMEOUT", 900)),
            ready_timeout_seconds=_int_env("REPORT_PDF_GEMINI_READY_TIMEOUT", _int_env("GEMINI_BROWSER_READY_TIMEOUT", 90)),
            upload_timeout_seconds=_int_env("REPORT_PDF_GEMINI_UPLOAD_TIMEOUT", _int_env("GEMINI_BROWSER_UPLOAD_TIMEOUT", 300)),
            min_response_chars=_int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", _int_env("GEMINI_BROWSER_MIN_RESPONSE_CHARS", 120)),
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
        "upload_timeout_seconds": options.upload_timeout_seconds,
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
        self._emit(progress_callback, "wait_browser_profile", 4, "等待 Gemini 浏览器 profile 空闲")
        with _gemini_profile_lock(self.options):
            self._wait_for_profile_singleton_release()
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
                        self._upload_file(page, str(pdf_file), started)
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

    def generate_from_file(
        self,
        *,
        job_id: str,
        file_path: str,
        output_path: str,
        prompt: str = "",
        target_url: Optional[str] = None,
        followup_prompts: Optional[List[str]] = None,
        image_followup_prompt: Optional[str] = None,
        image_output_path: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        source_file = Path(file_path).expanduser() if file_path else None
        if not source_file or not source_file.exists() or not source_file.is_file():
            raise GeminiBrowserConfigError(f"上传文件不存在: {file_path}")
        prompt = (prompt or "").strip()
        followups = [str(item).strip() for item in (followup_prompts or []) if str(item).strip()]
        image_prompt = (image_followup_prompt or "").strip()
        if image_prompt and not image_output_path:
            raise ValueError("需要指定 image_output_path 才能保存 Gemini 生成图片")

        started = time.monotonic()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image_output = Path(image_output_path).expanduser() if image_output_path else None
        if image_output:
            image_output.parent.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GeminiBrowserConfigError("缺少 playwright 依赖，请安装 requirements 后执行 playwright install") from exc

        url = (target_url or self.options.gemini_url or DEFAULT_GEMINI_URL).strip()
        self._emit(progress_callback, "wait_browser_profile", 3, "等待 Gemini 浏览器 profile 空闲")
        self._emit(progress_callback, "prepare_browser", 4, "启动 Gemini 浏览器")
        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(10_000)

            try:
                self._emit(progress_callback, "open_page", 8, "打开 Gemini 页面")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._wait_for_interactive_page(page, started)
                self._raise_if_login_or_captcha(page)
                self._guard_total_timeout(started)

                baseline_text = self._body_text(page)
                self._emit(progress_callback, "upload_file", 18, f"上传文件到 Gemini：{source_file.name}")
                self._upload_file(page, str(source_file), started)
                self._guard_total_timeout(started)
                self._emit(progress_callback, "upload_ready", 25, f"Gemini 文件上传完成：{source_file.name}")

                self._emit(progress_callback, "submit_initial", 28, "提交 Gemini 初始任务")
                self._submit_prompt(page, prompt)
                self._guard_total_timeout(started)

                self._emit(progress_callback, "wait_initial_response", 40, "等待 Gemini 第一轮回复")
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
                    48,
                    "Gemini 第一轮回复已获取",
                    result={
                        "initial_response_text": response["text"],
                        "response_text": response["text"],
                        "turns": turns,
                    },
                )
                output.write_text(json.dumps({
                    "status": "partial",
                    "prompt": prompt,
                    "followup_prompts": followups,
                    "image_followup_prompt": image_prompt,
                    "file": {
                        "path": str(source_file),
                        "filename": source_file.name,
                        "bytes": source_file.stat().st_size,
                    },
                    "initial_response_text": response["text"],
                    "response_text": response["text"],
                    "final_response_text": response["text"],
                    "image": None,
                    "turns": turns,
                    "page_url": page.url,
                    "target_url": url,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }, ensure_ascii=False, indent=2), encoding="utf-8")

                for index, followup in enumerate(followups, start=2):
                    progress_base = min(68, 48 + (index - 1) * 8)
                    baseline_text = self._body_text(page)
                    self._emit(progress_callback, f"followup_{index - 1}_submit", progress_base, f"提交第 {index - 1} 个追问")
                    self._submit_prompt(page, followup)
                    self._guard_total_timeout(started)
                    self._emit(progress_callback, f"followup_{index - 1}_wait", min(72, progress_base + 4), f"等待第 {index - 1} 个追问回复")
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
                        min(76, progress_base + 7),
                        f"第 {index - 1} 个追问回复已获取",
                        result={"response_text": turns[-1]["response_text"], "turns": turns},
                    )

                image_info = None
                image_response_text = ""
                if image_prompt:
                    baseline_text = self._body_text(page)
                    baseline_images = self._visible_image_keys(page)
                    self._emit(progress_callback, "image_prompt_submit", 76, "提交 Gemini 生成图片提示词")
                    self._submit_prompt(page, image_prompt)
                    self._guard_total_timeout(started)
                    self._emit(progress_callback, "wait_generated_image", 86, "等待 Gemini 生成图片")
                    image_record = self._wait_for_generated_image(page, baseline_images, started)
                    image_info = self._save_generated_image(page, image_record, str(image_output))
                    image_response_text = self._latest_response_text(page, image_prompt, baseline_text)
                    turns.append({
                        "index": len(turns) + 1,
                        "kind": "image_followup",
                        "prompt": image_prompt,
                        "response_text": image_response_text,
                        "image": image_info,
                        "page_url": page.url,
                        "completed_at": datetime.utcnow().isoformat() + "Z",
                    })
                    self._emit(
                        progress_callback,
                        "generated_image_saved",
                        94,
                        "Gemini 生成图片已保存",
                        result={"image": image_info, "turns": turns},
                    )

                result = {
                    "prompt": prompt,
                    "followup_prompts": followups,
                    "image_followup_prompt": image_prompt,
                    "file": {
                        "path": str(source_file),
                        "filename": source_file.name,
                        "bytes": source_file.stat().st_size,
                    },
                    "initial_response_text": response["text"],
                    "response_text": turns[-1]["response_text"],
                    "final_response_text": turns[-1]["response_text"],
                    "image": image_info,
                    "turns": turns,
                    "page_url": page.url,
                    "target_url": url,
                    "completed_at": datetime.utcnow().isoformat() + "Z",
                }
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result["output_path"] = str(output)
                self._emit(progress_callback, "save_result", 97, "Gemini 结果已保存", result={"output_path": str(output)})
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

    def _wait_for_profile_singleton_release(self) -> None:
        user_data_dir = Path(self.options.user_data_dir).expanduser()
        lock_path = user_data_dir / "SingletonLock"
        timeout_seconds = max(0, _int_env("GEMINI_BROWSER_SINGLETON_WAIT_SECONDS", 90))
        deadline = time.monotonic() + timeout_seconds

        while _path_exists_or_symlink(lock_path):
            owner = _singleton_owner(lock_path)
            if _clear_stale_singleton_files(user_data_dir, owner):
                continue
            if time.monotonic() >= deadline:
                owner_suffix = f"；占用信息: {owner}" if owner else ""
                raise GeminiBrowserAutomationError(
                    "Gemini 浏览器 profile 正在被其他 Chrome 占用"
                    f"{owner_suffix}。请关闭该浏览器窗口，或等待正在运行的任务结束后重试: {user_data_dir}"
                )
            time.sleep(1)

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

        profile_lock = _gemini_profile_lock(self.options)
        profile_lock.__enter__()
        try:
            self._wait_for_profile_singleton_release()
            context = self._launch_persistent_context_with_singleton_retry(playwright, user_data_dir, kwargs)
            return _LockedBrowserContext(context, profile_lock)
        except Exception:
            profile_lock.__exit__(None, None, None)
            raise

    def _launch_persistent_context_with_singleton_retry(self, playwright, user_data_dir: str, kwargs: Dict[str, Any]):
        try:
            return playwright.chromium.launch_persistent_context(user_data_dir, **kwargs)
        except Exception as exc:
            if not _looks_like_singleton_launch_error(exc):
                raise
            profile_dir = Path(user_data_dir).expanduser()
            owner = _singleton_owner(profile_dir / "SingletonLock")
            if not _clear_stale_singleton_files(profile_dir, owner):
                raise
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
        self._upload_file(page, pdf_path, started_at)

    def _upload_file(self, page, file_path: str, started_at: float) -> None:
        self._guard_total_timeout(started_at)
        direct_error = None
        try:
            inputs = page.locator("input[type='file']")
            count = inputs.count()
            for index in range(count - 1, -1, -1):
                candidate = inputs.nth(index)
                try:
                    candidate.set_input_files(file_path, timeout=10_000)
                    self._wait_for_upload_ready(page, Path(file_path).name, started_at)
                    return
                except Exception as exc:
                    direct_error = exc
        except Exception as exc:
            direct_error = exc

        attach_button = self._wait_for_visible_enabled(page, self._selectors("attach"), started_at, timeout_seconds=30)
        if attach_button is None:
            raise GeminiBrowserAutomationError(f"未找到 Gemini 文件上传入口; direct_error={direct_error}")

        attach_error = None
        try:
            with page.expect_file_chooser(timeout=5_000) as chooser_info:
                attach_button.click(timeout=10_000)
            chooser_info.value.set_files(file_path)
            self._wait_for_upload_ready(page, Path(file_path).name, started_at)
            return
        except Exception as exc:
            attach_error = exc

        upload_option = self._wait_for_visible_enabled(page, self._selectors("upload_file_option"), started_at, timeout_seconds=10)
        if upload_option is None:
            raise GeminiBrowserAutomationError(
                f"未找到 Gemini 文件上传菜单项; direct_error={direct_error}; attach_error={attach_error}"
            )

        try:
            with page.expect_file_chooser(timeout=10_000) as chooser_info:
                upload_option.click(timeout=10_000)
            chooser_info.value.set_files(file_path)
            self._wait_for_upload_ready(page, Path(file_path).name, started_at)
        except Exception as exc:
            raise GeminiBrowserAutomationError(f"文件上传失败: {exc}") from exc

    def _wait_for_upload_ready(self, page, filename: str, started_at: float) -> None:
        timeout_seconds = max(30, int(getattr(self.options, "upload_timeout_seconds", 300) or 300))
        settle_seconds = max(0.0, _float_env("GEMINI_BROWSER_UPLOAD_READY_SETTLE_SECONDS", 1.5))
        deadline = time.monotonic() + timeout_seconds
        name = filename.lower()
        ready_since = None
        last_state: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            state = self._composer_submit_state(page, name)
            last_state = state if isinstance(state, dict) else {}
            if isinstance(state, dict) and state.get("canceled"):
                raise GeminiBrowserAutomationError("Gemini 文件上传被取消或失败")
            if _upload_is_ready(state):
                if ready_since is None:
                    ready_since = time.monotonic()
                if time.monotonic() - ready_since >= settle_seconds:
                    return
            else:
                ready_since = None
            time.sleep(0.75)
        summary = _submit_state_summary(last_state, "")
        raise GeminiBrowserAutomationError(
            f"等待 Gemini 文件上传完成超时: {filename} ({timeout_seconds}s); {summary}"
        )

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

        promptless = not _clean_text(prompt)
        last_error = None
        for attempt in range(3):
            input_locator = self._last_visible(page, self._selectors("input")) or input_locator
            send_button = self._wait_for_send_button(page)
            clicked_send = False
            try:
                if send_button is not None:
                    self._click_send_button(page, send_button, force=attempt > 0)
                    clicked_send = True
                elif promptless:
                    last_error = GeminiBrowserAutomationError("未找到 Gemini 发送按钮")
                else:
                    input_locator.press("Enter", timeout=10_000)
            except Exception as exc:
                last_error = exc

            if self._wait_for_prompt_submitted(page, prompt, timeout_seconds=8, clicked_send=clicked_send):
                return

            if promptless:
                continue

            try:
                input_locator.press("Enter", timeout=10_000)
            except Exception as exc:
                last_error = exc
            if self._wait_for_prompt_submitted(page, prompt, timeout_seconds=5):
                return

        state = self._composer_submit_state(page, "")
        debug = _submit_state_summary(state, self._composer_text(page))
        suffix_parts = []
        if last_error:
            suffix_parts.append(str(last_error))
        if debug:
            suffix_parts.append(debug)
        suffix = f": {'; '.join(suffix_parts)}" if suffix_parts else ""
        raise GeminiBrowserAutomationError(f"Gemini 提示词未成功提交{suffix}")

    def _click_send_button(self, page, button, *, force: bool = False) -> None:
        last_error = None
        try:
            button.click(timeout=3_000, force=force)
            return
        except Exception as exc:
            last_error = exc

        try:
            button.dispatch_event("click", timeout=2_000)
            return
        except Exception as exc:
            last_error = exc

        try:
            button.evaluate(
                """(node) => {
                    node.focus();
                    node.click();
                }"""
            )
            return
        except Exception as exc:
            last_error = exc

        try:
            box = button.bounding_box(timeout=2_000)
            if box:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                return
        except Exception as exc:
            last_error = exc

        if last_error:
            raise last_error

    def _wait_for_prompt_submitted(self, page, prompt: str, timeout_seconds: float = 8, *, clicked_send: bool = False) -> bool:
        deadline = time.monotonic() + timeout_seconds
        normalized_prompt = _clean_text(prompt)
        while time.monotonic() < deadline:
            generating = self._is_generating(page)
            if generating:
                return True
            composer_text = _clean_text(self._composer_text(page))
            if not normalized_prompt:
                state = self._composer_submit_state(page, "")
                if _promptless_submit_confirmed(state, composer_text, generating, clicked_send=clicked_send):
                    return True
            if normalized_prompt not in composer_text:
                return True
            time.sleep(0.5)
        return False

    def _composer_submit_state(self, page, filename: str = "") -> Dict[str, Any]:
        try:
            state = page.evaluate(
                """(filename) => {
                    const rawText = (document.body && document.body.innerText || "").toLowerCase();
                    const normalize = (value) => String(value || "")
                        .toLowerCase()
                        .replace(/[\\s\\u00a0]+/g, " ")
                        .trim();
                    const compact = (value) => normalize(value).replace(/\\s+/g, "");
                    const text = normalize(rawText);
                    const compactText = compact(rawText);
                    const normalizedName = normalize(filename);
                    const stem = normalizedName.replace(/\\.[^.]+$/, "");
                    const markers = [
                        normalizedName,
                        stem,
                        stem ? stem.slice(0, Math.min(10, stem.length)) : "",
                        stem ? stem.slice(Math.max(0, stem.length - 10)) : ""
                    ].filter((marker) => compact(marker).length >= 4);
                    const hasName = markers.some((marker) => {
                        const normalizedMarker = normalize(marker);
                        const compactMarker = compact(marker);
                        return text.includes(normalizedMarker) || compactText.includes(compactMarker);
                    });
                    const busyMarkers = [
                        "uploading",
                        "processing",
                        "正在上传",
                        "正在处理",
                        "上传中",
                        "处理中"
                    ];
                    const canceledMarkers = [
                        "canceled",
                        "cancelled",
                        "failed",
                        "已取消",
                        "上传失败",
                        "处理失败"
                    ];
                    const busy = busyMarkers.some((marker) => text.includes(marker));
                    const canceled = canceledMarkers.some((marker) => text.includes(marker));
                    const visible = (node) => {
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style && style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                    };
                    const isTransparent = (value) => {
                        const text = String(value || "").replace(/\\s+/g, "").toLowerCase();
                        return !text || text === "transparent" || text === "rgba(0,0,0,0)";
                    };
                    const isLightNeutral = (value) => {
                        const match = String(value || "").match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
                        if (!match) return false;
                        const r = Number(match[1]);
                        const g = Number(match[2]);
                        const b = Number(match[3]);
                        return r >= 235 && g >= 235 && b >= 235;
                    };
                    const isDisabled = (button) => {
                        const ariaDisabled = String(button.getAttribute("aria-disabled") || "").toLowerCase();
                        return button.disabled || ariaDisabled === "true";
                    };
                    const buttonLabel = (button) => [
                        button.getAttribute("aria-label"),
                        button.getAttribute("title"),
                        button.innerText,
                        button.textContent,
                        button.innerHTML
                    ].filter(Boolean).join(" ").toLowerCase();
                    const looksLikeSubmit = (button) => {
                        if (!visible(button) || isDisabled(button)) return false;
                        const label = buttonLabel(button);
                        const explicit = label.includes("send") ||
                            label.includes("发送") ||
                            label.includes("submit") ||
                            label.includes("提交") ||
                            label.includes("arrow_upward") ||
                            label.includes("arrow-upward") ||
                            label.includes("north");
                        if (explicit) return true;

                        const blocked = [
                            "add",
                            "attach",
                            "upload",
                            "mic",
                            "microphone",
                            "record",
                            "添加",
                            "附件",
                            "上传",
                            "麦克风",
                            "语音"
                        ].some((marker) => label.includes(marker));
                        if (blocked) return false;

                        const rect = button.getBoundingClientRect();
                        const style = window.getComputedStyle(button);
                        const colored = !isTransparent(style.backgroundColor) && !isLightNeutral(style.backgroundColor);
                        const rightSide = rect.left > window.innerWidth * 0.55;
                        const lowerHalf = rect.top > window.innerHeight * 0.45;
                        const buttonSized = rect.width >= 28 && rect.height >= 28 && rect.width <= 80 && rect.height <= 80;
                        return colored && rightSide && lowerHalf && buttonSized;
                    };
                    const sendButtons = Array.from(document.querySelectorAll("button")).filter(looksLikeSubmit);
                    const enabledSend = sendButtons.length > 0;
                    return { hasName, busy, canceled, enabledSend, sendButtonCount: sendButtons.length };
                }""",
                filename,
            )
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _submit_button_index(self, page) -> Optional[int]:
        try:
            index = page.evaluate(
                """() => {
                    const visible = (node) => {
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style && style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                    };
                    const isTransparent = (value) => {
                        const text = String(value || "").replace(/\\s+/g, "").toLowerCase();
                        return !text || text === "transparent" || text === "rgba(0,0,0,0)";
                    };
                    const isLightNeutral = (value) => {
                        const match = String(value || "").match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
                        if (!match) return false;
                        const r = Number(match[1]);
                        const g = Number(match[2]);
                        const b = Number(match[3]);
                        return r >= 235 && g >= 235 && b >= 235;
                    };
                    const isDisabled = (button) => {
                        const ariaDisabled = String(button.getAttribute("aria-disabled") || "").toLowerCase();
                        return button.disabled || ariaDisabled === "true";
                    };
                    const buttonLabel = (button) => [
                        button.getAttribute("aria-label"),
                        button.getAttribute("title"),
                        button.innerText,
                        button.textContent,
                        button.innerHTML
                    ].filter(Boolean).join(" ").toLowerCase();
                    const looksLikeSubmit = (button) => {
                        if (!visible(button) || isDisabled(button)) return false;
                        const label = buttonLabel(button);
                        const explicit = label.includes("send") ||
                            label.includes("发送") ||
                            label.includes("submit") ||
                            label.includes("提交") ||
                            label.includes("arrow_upward") ||
                            label.includes("arrow-upward") ||
                            label.includes("north");
                        if (explicit) return true;

                        const blocked = [
                            "add",
                            "attach",
                            "upload",
                            "mic",
                            "microphone",
                            "record",
                            "添加",
                            "附件",
                            "上传",
                            "麦克风",
                            "语音"
                        ].some((marker) => label.includes(marker));
                        if (blocked) return false;

                        const rect = button.getBoundingClientRect();
                        const style = window.getComputedStyle(button);
                        const colored = !isTransparent(style.backgroundColor) && !isLightNeutral(style.backgroundColor);
                        const rightSide = rect.left > window.innerWidth * 0.55;
                        const lowerHalf = rect.top > window.innerHeight * 0.45;
                        const buttonSized = rect.width >= 28 && rect.height >= 28 && rect.width <= 80 && rect.height <= 80;
                        return colored && rightSide && lowerHalf && buttonSized;
                    };
                    return Array.from(document.querySelectorAll("button")).findIndex(looksLikeSubmit);
                }"""
            )
            if isinstance(index, int) and index >= 0:
                return index
        except Exception:
            return None
        return None

    def _composer_text(self, page) -> str:
        input_locator = self._last_visible(page, self._selectors("input"))
        if input_locator is None:
            return ""
        try:
            text = input_locator.evaluate(
                """(node) => {
                    if ("value" in node) return node.value || "";
                    return node.innerText || node.textContent || "";
                }"""
            )
            return str(text or "")
        except Exception:
            return ""

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
            index = self._submit_button_index(page)
            if index is not None:
                button = page.locator("button").nth(index)
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
        debug = _submit_state_summary(self._composer_submit_state(page, ""), self._composer_text(page))
        suffix = f"; {debug}" if debug else ""
        raise GeminiBrowserAutomationError(f"等待 Gemini 回复超时{suffix}")

    def _visible_image_keys(self, page) -> List[str]:
        records = self._visible_image_records(page)
        keys = []
        for record in records:
            key = record.get("src") or record.get("currentSrc") or record.get("alt") or str(record.get("dom_index"))
            if key:
                keys.append(str(key))
        return keys

    def _visible_image_records(self, page) -> List[Dict[str, Any]]:
        try:
            records = page.evaluate(
                """() => {
                    const visible = (node) => {
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style && style.visibility !== "hidden" && style.display !== "none" && rect.width >= 80 && rect.height >= 80;
                    };
                    return Array.from(document.querySelectorAll("img")).map((img, index) => {
                        const rect = img.getBoundingClientRect();
                        return {
                            dom_index: index,
                            src: img.getAttribute("src") || "",
                            currentSrc: img.currentSrc || "",
                            alt: img.getAttribute("alt") || "",
                            width: rect.width,
                            height: rect.height,
                            naturalWidth: img.naturalWidth || 0,
                            naturalHeight: img.naturalHeight || 0,
                            top: rect.top,
                            left: rect.left
                        };
                    }).filter((item) => item.width >= 80 && item.height >= 80);
                }"""
            )
            return records if isinstance(records, list) else []
        except Exception:
            return []

    def _wait_for_generated_image(self, page, baseline_keys: List[str], started_at: float) -> Dict[str, Any]:
        deadline = time.monotonic() + max(30, int(self.options.image_timeout_seconds or self.options.response_timeout_seconds))
        baseline = set(str(item or "") for item in (baseline_keys or []))
        last_records: List[Dict[str, Any]] = []
        last_candidates: List[Dict[str, Any]] = []
        stable_since = time.monotonic()
        last_key = ""

        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            records = self._visible_image_records(page)
            candidates = []
            for record in records:
                key = str(record.get("src") or record.get("currentSrc") or record.get("alt") or record.get("dom_index") or "")
                area = float(record.get("width") or 0) * float(record.get("height") or 0)
                if key in baseline:
                    continue
                if area < 30_000:
                    continue
                candidates.append((area, record))
            if candidates:
                candidates.sort(key=lambda item: (item[0], float(item[1].get("top") or 0)))
                last_candidates = [item[1] for item in candidates]
                candidate = candidates[-1][1]
                key = str(candidate.get("src") or candidate.get("currentSrc") or candidate.get("dom_index") or "")
                if key != last_key:
                    last_key = key
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= self.options.response_stable_seconds and not self._is_generating(page):
                    return candidate
            last_records = records
            time.sleep(1)

        if last_candidates:
            last_candidates.sort(key=lambda item: float(item.get("width") or 0) * float(item.get("height") or 0))
            return last_candidates[-1]
        raise GeminiBrowserAutomationError("等待 Gemini 生成图片超时")

    def _save_generated_image(self, page, record: Dict[str, Any], output_path: str) -> Dict[str, Any]:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        src = record.get("currentSrc") or record.get("src") or ""
        saved_by = "screenshot"
        if src:
            try:
                data_url = page.evaluate(
                    """async (src) => {
                        const response = await fetch(src);
                        const blob = await response.blob();
                        return await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                    }""",
                    src,
                )
                if isinstance(data_url, str) and data_url.startswith("data:") and "," in data_url:
                    header, payload = data_url.split(",", 1)
                    output.write_bytes(base64.b64decode(payload))
                    saved_by = "fetch"
            except Exception:
                saved_by = "screenshot"

        if not output.exists() or output.stat().st_size <= 0:
            dom_index = record.get("dom_index")
            if dom_index is None:
                raise GeminiBrowserAutomationError("无法定位 Gemini 生成图片")
            page.locator("img").nth(int(dom_index)).screenshot(path=str(output))
            saved_by = "screenshot"

        return {
            "path": str(output),
            "bytes": output.stat().st_size,
            "source": src,
            "saved_by": saved_by,
            "width": record.get("naturalWidth") or record.get("width"),
            "height": record.get("naturalHeight") or record.get("height"),
        }

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
        if _looks_like_page_shell_text(text):
            return False
        if prompt and text == prompt:
            return False
        if len(text) < max(0, int(self.options.min_response_chars or 0)):
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
                "button[aria-label*='提交']",
                "button[title*='Send']",
                "button[title*='Submit']",
                "button[title*='发送']",
                "button[title*='提交']",
                "button:has-text('提交')",
                "button:has-text('发送')",
                "button:has(mat-icon:has-text('send'))",
                "button:has(mat-icon:has-text('arrow_upward'))",
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
                "model-response",
                "[id^='model-response-message']",
                "[class*='response-content']",
                "[class*='markdown']",
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

    def _wait_for_visible_enabled(self, page, selectors: List[str], started_at: float, timeout_seconds: float = 10):
        deadline = time.monotonic() + timeout_seconds
        last_visible = None

        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            last_visible = self._last_visible(page, selectors)
            if last_visible is not None:
                try:
                    if last_visible.is_enabled(timeout=500):
                        return last_visible
                except Exception:
                    pass
            time.sleep(0.5)

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


class _LockedBrowserContext:
    def __init__(self, context, profile_lock):
        self._context = context
        self._profile_lock = profile_lock
        self._released = False

    def __getattr__(self, name: str):
        return getattr(self._context, name)

    def close(self):
        try:
            return self._context.close()
        finally:
            self._release_profile_lock()

    def _release_profile_lock(self) -> None:
        if self._released:
            return
        self._released = True
        self._profile_lock.__exit__(None, None, None)


@contextmanager
def _gemini_profile_lock(options: GeminiBrowserOptions):
    if fcntl is None:
        yield
        return

    timeout_seconds = max(1, _int_env("GEMINI_BROWSER_PROFILE_LOCK_TIMEOUT_SECONDS", 1800))
    user_data_dir = str(Path(options.user_data_dir or "").expanduser())
    profile = options.profile_directory or "Default"
    key = hashlib.sha1(f"{user_data_dir}|{profile}".encode("utf-8")).hexdigest()[:16]
    owner = (os.getpid(), threading.get_ident())
    lock_dir = Path(_project_root()) / "runtime" / "locks" / "gemini_browser"
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
                raise GeminiBrowserAutomationError(
                    f"等待 Gemini 浏览器 profile 空闲超时: {user_data_dir} ({timeout_seconds}s)"
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
                        raise GeminiBrowserAutomationError(
                            f"等待 Gemini 浏览器 profile 空闲超时: {user_data_dir} ({timeout_seconds}s)"
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


def _path_exists_or_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _looks_like_singleton_launch_error(exc: Any) -> bool:
    text = str(exc or "").lower()
    return (
        "singletonlock" in text
        or "processsingleton" in text
        or ("profile directory" in text and "aborting" in text)
    )


def _singleton_owner(lock_path: Path) -> str:
    target = ""
    try:
        target = os.readlink(lock_path)
    except OSError:
        if _path_exists_or_symlink(lock_path):
            target = str(lock_path)
    if not target:
        return ""

    pid = _singleton_pid(target)
    if pid is None:
        return target
    status = "运行中" if _pid_is_running(pid) else "未运行/疑似残留"
    return f"{target}（pid {pid} {status}）"


def _clear_stale_singleton_files(user_data_dir: Path, owner: str) -> bool:
    if not _truthy_env("GEMINI_BROWSER_CLEAN_STALE_SINGLETON", True):
        return False

    pid = _singleton_pid(owner)
    if pid is None or _pid_is_running(pid):
        return False

    cleared = False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = user_data_dir / name
        if not _path_exists_or_symlink(path):
            continue
        try:
            path.unlink()
            cleared = True
        except OSError:
            pass
    return cleared


def _singleton_pid(target: str) -> Optional[int]:
    text = str(target or "")
    marker = "pid "
    marker_index = text.find(marker)
    if marker_index >= 0:
        rest = text[marker_index + len(marker):].strip()
        token = rest.split(None, 1)[0] if rest else ""
        if token.isdigit():
            return int(token)

    tail = text.rsplit("-", 1)[-1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    if digits and digits == tail.strip("）).,;: "):
        return int(digits)
    return None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _upload_is_ready(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("busy") or state.get("canceled"):
        return False
    return bool(state.get("hasName") and state.get("enabledSend"))


def _promptless_submit_confirmed(state: Any, composer_text: str, generating: bool = False, *, clicked_send: bool = False) -> bool:
    if generating:
        return True
    if _clean_text(composer_text):
        return False
    if not clicked_send:
        return False
    if not isinstance(state, dict):
        return False
    if state.get("busy"):
        return True
    return not bool(state.get("enabledSend"))


def _submit_state_summary(state: Any, composer_text: str = "") -> str:
    if not isinstance(state, dict):
        return ""
    parts = []
    for key in ("hasName", "busy", "canceled", "enabledSend", "sendButtonCount"):
        if key in state:
            parts.append(f"{key}={state.get(key)}")
    text = _clean_text(composer_text)
    if text:
        parts.append(f"composer_chars={len(text)}")
    return "submit_state(" + ", ".join(parts) + ")" if parts else ""


def _looks_like_page_shell_text(value: str) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return False

    strong_markers = (
        "跳至主要内容",
        "发起新对话",
        "搜索对话内容",
        "gemini 是一款 ai 工具",
        "与 gemini 对话",
        "ask gemini",
        "enter a prompt",
        "gemini can make mistakes",
    )
    if any(marker in text for marker in strong_markers):
        return True

    shell_markers = (
        "近期对话",
        "所有笔记本",
        "新建笔记本",
        "问问 gemini",
        "笔记本",
        "gem",
        "pro",
    )
    hits = sum(1 for marker in shell_markers if marker in text)
    return hits >= 3 and "gemini" in text


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
