import json
import logging
import os
import re
import shlex
import signal
import subprocess
import time
import base64
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.workflow_notification_service import workflow_notification_service

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


FEATURE_NAME = "report_pdf_gemini"
QUEUE_NAME = "report_pdf_gemini"
LEGACY_FEATURE_NAME = "gemini_browser"
LEGACY_QUEUE_NAME = "gemini_browser"
DEFAULT_GEMINI_URL = "https://gemini.google.com/gem/980ba0a0ff22"
_ACTIVE_PROFILE_LOCKS: Dict[str, Dict[str, Any]] = {}
logger = logging.getLogger(__name__)
_IMAGE_ONLY_STORYBOARD_RECOVERY_PROMPT = """你刚才只生成了图片，没有返回可保存的完整分镜文字。
请停止继续生图，现在只输出完整分镜文本，不要再次调用图片工具。
必须逐个分镜包含【文案原句】【画面动作】【视觉隐喻】【AI 提示词】；保留刚才使用的英文 AI 提示词原文，不得翻译、改写、缩写或遗漏。
所有【文案原句】合并后必须 100% 覆盖用户提供的正文。"""
_IMAGE_QUOTA_MARKERS = (
    "i can create more images as soon as your limit resets",
    "image generation limit reached",
    "you've reached your image generation limit",
    "you have reached your image generation limit",
    "图片生成额度已用尽",
    "图片生成已达上限",
    "图片生成次数已达上限",
    "达到图片生成限额",
)


class GeminiBrowserError(RuntimeError):
    pass


class GeminiBrowserConfigError(GeminiBrowserError):
    pass


class GeminiBrowserAutomationError(GeminiBrowserError):
    pass


class GeminiBrowserHumanInterventionError(GeminiBrowserAutomationError):
    def __init__(self, message, *, reason, notification=None, context=None):
        super().__init__(message)
        self.reason = str(reason or "verification")
        self.notification = dict(notification or {})
        self.context = dict(context or {})


def _is_image_quota_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return any(marker in normalized for marker in _IMAGE_QUOTA_MARKERS)


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
    # Playwright's persistent-context launch defaults to no timeout.  A
    # browser/profile startup failure must not hold a Celery worker forever.
    browser_launch_timeout_seconds: int = 90
    # Isolated report-visual-plan profiles may be left behind by an orphaned
    # Chrome process after a worker restart.  Keep recovery opt-in for generic
    # Gemini/PDF profiles so a user's normal Chrome session is never touched.
    auto_recover_orphaned_chrome: bool = False
    min_response_chars: int = 120
    prefer_pro_model: bool = True
    require_pro_model: bool = False
    model_switch_timeout_seconds: int = 12
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
            browser_launch_timeout_seconds=_int_env(
                "REPORT_PDF_GEMINI_BROWSER_LAUNCH_TIMEOUT_SECONDS",
                _int_env("GEMINI_BROWSER_LAUNCH_TIMEOUT_SECONDS", 90),
            ),
            auto_recover_orphaned_chrome=_truthy_env(
                "REPORT_PDF_GEMINI_AUTO_RECOVER_ORPHANED_CHROME",
                _truthy_env("GEMINI_BROWSER_AUTO_RECOVER_ORPHANED_CHROME", False),
            ),
            min_response_chars=_int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", _int_env("GEMINI_BROWSER_MIN_RESPONSE_CHARS", 120)),
            prefer_pro_model=_truthy_env(
                "REPORT_PDF_GEMINI_PREFER_PRO",
                _truthy_env("GEMINI_BROWSER_PREFER_PRO", True),
            ),
            model_switch_timeout_seconds=_int_env(
                "REPORT_PDF_GEMINI_MODEL_SWITCH_TIMEOUT_SECONDS",
                _int_env("GEMINI_BROWSER_MODEL_SWITCH_TIMEOUT_SECONDS", 12),
            ),
            selectors=_load_selectors_from_env(),
        )


def is_configured(options: Optional[GeminiBrowserOptions] = None) -> bool:
    options = options or GeminiBrowserOptions.from_env()
    return bool(options.user_data_dir)


def _looks_like_image_bytes(value: bytes) -> bool:
    return bool(
        value.startswith(b"\x89PNG\r\n\x1a\n")
        or value.startswith(b"\xff\xd8\xff")
        or value.startswith((b"GIF87a", b"GIF89a", b"RIFF"))
    )


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
        "browser_launch_timeout_seconds": options.browser_launch_timeout_seconds,
        "auto_recover_orphaned_chrome": options.auto_recover_orphaned_chrome,
        "mode": "real_browser",
        "business_feature": FEATURE_NAME,
        "automation_engine": "gemini_browser",
    }


class GeminiBrowserWorker:
    def __init__(self, options: Optional[GeminiBrowserOptions] = None):
        self.options = options or GeminiBrowserOptions.from_env()
        self._current_job_id = ""
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
        embedded_image_output_dir: Optional[str] = None,
        recover_image_only_storyboard: bool = False,
        response_complete_callback: Optional[Callable[[str], bool]] = None,
        incomplete_response_followup_prompt: str = "",
        response_contract_validator: Optional[Callable[[str], Dict[str, Any]]] = None,
        response_contract_repair_prompt_builder: Optional[Callable[[Dict[str, Any]], str]] = None,
        response_contract_max_repairs: int = 1,
        human_intervention_details: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        self._current_job_id = str(job_id or "")
        self._human_intervention_details = dict(human_intervention_details or {})
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
                # Profile contention is queueing, not execution. Start the hard
                # budget only after this worker actually owns a browser context.
                started = time.monotonic()
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(10_000)

                try:
                    self._emit(progress_callback, "open_page", 12, "打开 Gemini 页面")
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    self._recover_gem_manager_redirect(page, url, started)
                    self._wait_for_interactive_page(page, started)
                    self._recover_gem_manager_redirect(page, url, started)
                    self._wait_for_interactive_page(page, started)
                    self._raise_if_login_or_captcha(page)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "select_pro_model", 18, "尝试切换 Gemini Pro 模型")
                    pro_model_selection = self._try_select_pro_model(page)
                    self._emit(
                        progress_callback,
                        "pro_model_ready",
                        20,
                        _pro_model_selection_message(pro_model_selection),
                        result={"gemini_model_selection": pro_model_selection},
                    )
                    self._ensure_required_pro_model(pro_model_selection)

                    baseline_text = self._body_text(page)
                    baseline_image_keys = self._visible_image_keys(page)
                    if pdf_file:
                        self._emit(progress_callback, "upload_pdf", 25, "上传 PDF 到 Gemini")
                        self._upload_file(page, str(pdf_file), started)
                        self._guard_total_timeout(started)

                    self._emit(progress_callback, "submit_prompt", 35, "提交 Gemini 任务")
                    self._submit_prompt(page, prompt)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "wait_response", 48, "等待 Gemini 回复")
                    response = self._wait_for_response(
                        page,
                        prompt,
                        baseline_text,
                        started,
                        image_only_baseline_keys=baseline_image_keys if recover_image_only_storyboard else None,
                    )
                    embedded_images = []
                    if response.get("image_only"):
                        if embedded_image_output_dir:
                            try:
                                embedded_images = self._save_embedded_response_images(
                                    page,
                                    baseline_image_keys,
                                    embedded_image_output_dir,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Gemini image-only response images were not saved for job %s: %s",
                                    job_id,
                                    exc,
                                )
                        self._emit(
                            progress_callback,
                            "recover_image_only_storyboard",
                            52,
                            f"Gem 仅返回了 {len(embedded_images)} 张图片，自动追问完整分镜文字",
                        )
                        recovery_baseline_text = self._body_text(page)
                        self._submit_prompt(page, _IMAGE_ONLY_STORYBOARD_RECOVERY_PROMPT)
                        self._guard_total_timeout(started)
                        response = self._wait_for_response(
                            page,
                            _IMAGE_ONLY_STORYBOARD_RECOVERY_PROMPT,
                            recovery_baseline_text,
                            started,
                        )
                    initial_response_text = response["text"]
                    turns = [{
                        "index": 1,
                        "kind": "image_only_recovery" if embedded_images else "initial",
                        "prompt": prompt,
                        "response_text": initial_response_text,
                        "page_url": page.url,
                        "completed_at": datetime.utcnow().isoformat() + "Z",
                    }]
                    self._emit(
                        progress_callback,
                        "initial_response",
                        55,
                        "第一轮 Gemini 回复已获取",
                        result={
                            "initial_response_text": initial_response_text,
                            "response_text": initial_response_text,
                            "turns": turns,
                        },
                    )

                    combined_response_text = initial_response_text
                    _write_generation_checkpoint(
                        output,
                        prompt=prompt,
                        followups=followups,
                        initial_response_text=initial_response_text,
                        combined_response_text=combined_response_text,
                        turns=turns,
                        page_url=page.url,
                        target_url=url,
                        embedded_images=embedded_images,
                    )
                    continuation_prompt = str(incomplete_response_followup_prompt or "").strip()
                    max_continuations = max(
                        0,
                        _int_env("GEMINI_BROWSER_INCOMPLETE_CONTINUATION_MAX", 12),
                    )
                    continuation_round = 0
                    while (
                        response_complete_callback
                        and continuation_prompt
                        and continuation_round < max_continuations
                        and not response_complete_callback(combined_response_text)
                    ):
                        continuation_round += 1
                        baseline_text = self._body_text(page)
                        continuation_baseline_images = self._visible_image_keys(page)
                        self._emit(
                            progress_callback,
                            f"incomplete_continuation_{continuation_round}_submit",
                            min(88, 55 + continuation_round * 2),
                            f"Gem 分镜尚未覆盖全文，继续请求剩余部分（第 {continuation_round} 次）",
                        )
                        self._submit_prompt(page, continuation_prompt)
                        self._guard_total_timeout(started)
                        continuation_response = self._wait_for_response(
                            page,
                            continuation_prompt,
                            baseline_text,
                            started,
                            image_only_baseline_keys=continuation_baseline_images,
                        )
                        if continuation_response.get("image_only"):
                            recovery_baseline_text = self._body_text(page)
                            self._emit(
                                progress_callback,
                                f"incomplete_continuation_{continuation_round}_recover_text",
                                min(89, 56 + continuation_round * 2),
                                "本轮仅返回图片，自动追问本轮分镜文字",
                            )
                            self._submit_prompt(page, _IMAGE_ONLY_STORYBOARD_RECOVERY_PROMPT)
                            self._guard_total_timeout(started)
                            continuation_response = self._wait_for_response(
                                page,
                                _IMAGE_ONLY_STORYBOARD_RECOVERY_PROMPT,
                                recovery_baseline_text,
                                started,
                            )
                        continuation_text = str(continuation_response.get("text") or "").strip()
                        if continuation_text:
                            combined_response_text = (
                                f"{combined_response_text}\n\n{continuation_text}".strip()
                            )
                        turns.append({
                            "index": len(turns) + 1,
                            "kind": "incomplete_storyboard_continuation",
                            "prompt": continuation_prompt,
                            "response_text": continuation_text,
                            "page_url": page.url,
                            "completed_at": datetime.utcnow().isoformat() + "Z",
                        })
                        _write_generation_checkpoint(
                            output,
                            prompt=prompt,
                            followups=followups,
                            initial_response_text=initial_response_text,
                            combined_response_text=combined_response_text,
                            turns=turns,
                            page_url=page.url,
                            target_url=url,
                            embedded_images=embedded_images,
                        )
                    response = {"text": combined_response_text}
                    # The incomplete-storyboard path may have several turns.
                    # Keep the merged text as the result unless an explicit
                    # generic follow-up later replaces it; returning
                    # ``turns[-1]`` here silently discarded the title and all
                    # earlier continuation scenes.
                    final_response_text = combined_response_text
                    response_contract_validation = None
                    if response_contract_validator:
                        final_response_text, response_contract_validation = self._repair_response_contract(
                            page=page,
                            started=started,
                            response_text=final_response_text,
                            turns=turns,
                            validator=response_contract_validator,
                            repair_prompt_builder=response_contract_repair_prompt_builder,
                            max_repairs=response_contract_max_repairs,
                            progress_callback=progress_callback,
                        )
                        _write_generation_checkpoint(
                            output,
                            prompt=prompt,
                            followups=followups,
                            initial_response_text=initial_response_text,
                            combined_response_text=final_response_text,
                            turns=turns,
                            page_url=page.url,
                            target_url=url,
                            embedded_images=embedded_images,
                        )

                    for followup_number, followup in enumerate(followups, start=1):
                        index = len(turns) + 1
                        progress_base = min(88, 50 + (index - 1) * 10)
                        baseline_text = self._body_text(page)
                        self._emit(
                            progress_callback,
                            f"followup_{followup_number}_submit",
                            progress_base,
                            f"提交第 {followup_number} 个追问",
                        )
                        self._submit_prompt(page, followup)
                        self._guard_total_timeout(started)

                        self._emit(
                            progress_callback,
                            f"followup_{followup_number}_wait",
                            min(92, progress_base + 5),
                            f"等待第 {followup_number} 个追问回复",
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
                        final_response_text = str(turns[-1]["response_text"] or "")
                        _write_generation_checkpoint(
                            output,
                            prompt=prompt,
                            followups=followups,
                            initial_response_text=initial_response_text,
                            combined_response_text=turns[-1]["response_text"],
                            turns=turns,
                            page_url=page.url,
                            target_url=url,
                            embedded_images=embedded_images,
                        )
                        self._emit(
                            progress_callback,
                            f"followup_{followup_number}_response",
                            min(94, progress_base + 8),
                            f"第 {followup_number} 个追问回复已获取",
                            result={
                                "response_text": turns[-1]["response_text"],
                                "final_response_text": turns[-1]["response_text"],
                                "turns": turns,
                            },
                        )

                    if embedded_image_output_dir and not embedded_images:
                        try:
                            embedded_images = self._save_embedded_response_images(
                                page,
                                baseline_image_keys,
                                embedded_image_output_dir,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Gemini embedded response images were not adopted for job %s: %s",
                                job_id,
                                exc,
                            )

                    result = {
                        "prompt": prompt,
                        "followup_prompts": followups,
                        "pdf": {
                            "path": str(pdf_file) if pdf_file else None,
                            "filename": pdf_file.name if pdf_file else None,
                            "bytes": pdf_file.stat().st_size if pdf_file else None,
                        } if pdf_file else None,
                        "initial_response_text": initial_response_text,
                        "response_text": final_response_text,
                        "final_response_text": final_response_text,
                        "turns": turns,
                        "page_url": page.url,
                        "target_url": url,
                        "gemini_model_selection": pro_model_selection,
                        "response_contract_validation": response_contract_validation,
                        "embedded_images": embedded_images,
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

    def _repair_response_contract(
        self,
        *,
        page,
        started: float,
        response_text: str,
        turns: List[Dict[str, Any]],
        validator: Callable[[str], Dict[str, Any]],
        repair_prompt_builder: Optional[Callable[[Dict[str, Any]], str]],
        max_repairs: int,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    ):
        """Validate a response and replace it through at most one repair turn."""

        current_text = str(response_text or "").strip()
        current = _contract_diagnostics(validator(current_text))
        initial = dict(current)
        repairs = []
        try:
            repair_limit = max(0, min(int(max_repairs or 0), 1))
        except (TypeError, ValueError):
            repair_limit = 1

        while (
            not current.get("valid")
            and len(repairs) < repair_limit
            and repair_prompt_builder is not None
        ):
            repair_prompt = str(repair_prompt_builder(current) or "").strip()
            if not repair_prompt:
                break
            attempt = len(repairs) + 1
            baseline_text = self._body_text(page)
            self._emit(
                progress_callback,
                f"response_contract_repair_{attempt}_submit",
                72,
                "Gemini 返回未通过结构校验，提交一次定向修复",
                result={"response_contract_validation": current},
            )
            self._submit_prompt(page, repair_prompt)
            self._guard_total_timeout(started)
            self._emit(
                progress_callback,
                f"response_contract_repair_{attempt}_wait",
                78,
                "等待 Gemini 返回完整替换 JSON",
            )
            response = self._wait_for_response(page, repair_prompt, baseline_text, started)
            current_text = str(response.get("text") or "").strip()
            turns.append({
                "index": len(turns) + 1,
                "kind": "response_contract_repair",
                "prompt": repair_prompt,
                "response_text": current_text,
                "page_url": page.url,
                "completed_at": datetime.utcnow().isoformat() + "Z",
            })
            current = _contract_diagnostics(validator(current_text))
            repairs.append({
                "attempt": attempt,
                "valid": bool(current.get("valid")),
                "error_codes": list(current.get("error_codes") or []),
            })
            self._emit(
                progress_callback,
                f"response_contract_repair_{attempt}_response",
                84,
                "Gemini 定向修复结果已获取",
                result={
                    "response_text": current_text,
                    "final_response_text": current_text,
                    "turns": turns,
                    "response_contract_validation": current,
                },
            )

        diagnostics = dict(current)
        diagnostics["initial_valid"] = bool(initial.get("valid"))
        diagnostics["initial_error_codes"] = list(initial.get("error_codes") or [])
        diagnostics["repair_attempt_count"] = len(repairs)
        diagnostics["repair_attempts"] = repairs
        return current_text, diagnostics

    def generate_image(
        self,
        *,
        job_id: str,
        prompt: str,
        image_output_path: str,
        target_url: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Create one image in a fresh Gemini session without rewriting the prompt."""
        self._current_job_id = str(job_id or "")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("图片提示词不能为空")
        output = Path(image_output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GeminiBrowserConfigError("缺少 playwright 依赖，请安装 requirements 后执行 playwright install") from exc

        url = str(target_url or os.getenv("REPORT_STICK_FIGURE_IMAGE_GEMINI_URL") or "https://gemini.google.com/app").strip()
        self._emit(progress_callback, "wait_browser_profile", 4, "等待 Gemini 图片浏览器 profile 空闲")
        with _gemini_profile_lock(self.options):
            self._wait_for_profile_singleton_release()
            self._emit(progress_callback, "prepare_browser", 8, "启动 Gemini 图片浏览器")
            with sync_playwright() as playwright:
                context = self._launch_context(playwright)
                started = time.monotonic()
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(10_000)
                try:
                    self._emit(progress_callback, "open_page", 15, "打开新的 Gemini 会话")
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    self._recover_gem_manager_redirect(page, url, started)
                    self._wait_for_interactive_page(page, started)
                    self._recover_gem_manager_redirect(page, url, started)
                    self._wait_for_interactive_page(page, started)
                    self._raise_if_login_or_captcha(page)
                    self._guard_total_timeout(started)

                    self._emit(progress_callback, "select_image_mode", 24, "选择 Gemini 生成图片")
                    self._select_image_generation_mode(page, started)
                    baseline_images = self._visible_image_keys(page)
                    baseline_text = self._body_text(page)

                    self._emit(progress_callback, "submit_image_prompt", 32, "原样提交分镜图片提示词")
                    self._submit_prompt(page, prompt)
                    self._guard_total_timeout(started)
                    self._emit(progress_callback, "wait_generated_image", 65, "等待 Gemini 生成图片")
                    image_record = self._wait_for_generated_image(page, baseline_images, started)
                    image_info = self._save_generated_image(page, image_record, str(output))
                    response_text = self._latest_response_text(page, prompt, baseline_text)
                    result = {
                        **image_info,
                        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "page_url": page.url,
                        "target_url": url,
                        "response_text": response_text,
                        "completed_at": datetime.utcnow().isoformat() + "Z",
                    }
                    self._emit(progress_callback, "generated_image_saved", 96, "Gemini 图片已按分镜序号保存", result={"image": image_info})
                    return result
                except PlaywrightTimeoutError as exc:
                    screenshot = self._save_failure_screenshot(page, job_id)
                    raise GeminiBrowserAutomationError(f"Gemini 图片自动化超时: {exc}; screenshot={screenshot}") from exc
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
        self._current_job_id = str(job_id or "")
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
            started = time.monotonic()
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(10_000)

            try:
                self._emit(progress_callback, "open_page", 8, "打开 Gemini 页面")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._recover_gem_manager_redirect(page, url, started)
                self._wait_for_interactive_page(page, started)
                self._recover_gem_manager_redirect(page, url, started)
                self._wait_for_interactive_page(page, started)
                self._raise_if_login_or_captcha(page)
                self._guard_total_timeout(started)

                self._emit(progress_callback, "select_pro_model", 12, "尝试切换 Gemini Pro 模型")
                pro_model_selection = self._try_select_pro_model(page)
                self._emit(
                    progress_callback,
                    "pro_model_ready",
                    15,
                    _pro_model_selection_message(pro_model_selection),
                    result={"gemini_model_selection": pro_model_selection},
                )
                self._ensure_required_pro_model(pro_model_selection)

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
                    "gemini_model_selection": pro_model_selection,
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
            if _recover_orphaned_chrome_profile(
                user_data_dir,
                owner,
                enabled=bool(getattr(self.options, "auto_recover_orphaned_chrome", False)),
            ):
                continue
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
            # Playwright treats None as unlimited here.  Keep this bounded so
            # a broken Chrome/profile launch fails and releases the worker lock.
            "timeout": max(5, int(self.options.browser_launch_timeout_seconds or 0)) * 1000,
        }
        if self.options.chrome_executable_path:
            kwargs["executable_path"] = self.options.chrome_executable_path
        if self.options.chrome_channel:
            kwargs["channel"] = self.options.chrome_channel

        profile_lock = _gemini_profile_lock(self.options)
        profile_lock.__enter__()
        try:
            self._wait_for_profile_singleton_release()
            try:
                context = self._launch_persistent_context_with_singleton_retry(playwright, user_data_dir, kwargs)
            except Exception as exc:
                # Playwright's persistent-context launch has no default
                # timeout.  Convert its bounded timeout into a domain error so
                # the task records a useful failure and releases the lock.
                if exc.__class__.__name__ == "TimeoutError":
                    raise GeminiBrowserAutomationError(
                        "Gemini 浏览器启动超时（"
                        f"{max(5, int(self.options.browser_launch_timeout_seconds or 0))} 秒）: {exc}"
                    ) from exc
                raise
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

    def _recover_gem_manager_redirect(self, page, target_url: str, started_at: float) -> None:
        """Open the requested custom Gem when Google redirects a deep link to Gems manager."""
        match = re.search(r"/gem/([a-zA-Z0-9_-]+)", str(target_url or ""))
        if not match:
            return
        gem_id = match.group(1)
        if f"/gem/{gem_id}" in str(page.url or ""):
            page.wait_for_timeout(2_500)
            if f"/gem/{gem_id}" in str(page.url or ""):
                return
        if "/gems/view" not in str(page.url or ""):
            return

        self._guard_total_timeout(started_at)
        link = page.locator(f'a[href*="/gem/{gem_id}"]').first
        try:
            if link.count() < 1:
                raise GeminiBrowserAutomationError(
                    f"Gemini 已跳转到 Gem 管理器，但未找到目标 Gem: {gem_id}"
                )
            link.scroll_into_view_if_needed(timeout=10_000)
            link.click(timeout=15_000)
        except GeminiBrowserAutomationError:
            raise
        except Exception as exc:
            raise GeminiBrowserAutomationError(
                f"从 Gem 管理器打开目标 Gem 失败: {gem_id}; {exc}"
            ) from exc

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            if f"/gem/{gem_id}" in str(page.url or ""):
                return
            time.sleep(0.5)
        raise GeminiBrowserAutomationError(
            f"点击目标 Gem 后页面未进入对话: {gem_id}; current_url={page.url}"
        )

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

    def _try_select_pro_model(self, page) -> Dict[str, Any]:
        """Attempt Pro selection before an uploaded file reaches Gemini.

        Gemini renders the composer before its mode picker.  Wait for the
        picker separately so strict Pro jobs do not fail during that short UI
        race.  Missing/unsupported pickers are still recorded for the caller;
        strict jobs can then stop before uploading any user content.
        """
        if not self.options.prefer_pro_model:
            return {"requested": False, "selected": False, "confirmed": False, "status": "disabled"}

        try:
            picker_wait_seconds = max(
                0.0,
                min(30.0, float(self.options.model_switch_timeout_seconds or 0)),
            )
            picker_deadline = time.monotonic() + picker_wait_seconds
            picker = None
            while picker is None:
                picker = self._first_visible(page, self._selectors("model_picker"))
                if picker is not None or time.monotonic() >= picker_deadline:
                    break
                time.sleep(0.25)
            if picker is None:
                return {"requested": True, "selected": False, "confirmed": False, "status": "picker_not_found"}

            before = _locator_control_text(picker)
            if _looks_like_pro_model_label(before):
                return {
                    "requested": True,
                    "selected": True,
                    "confirmed": True,
                    "status": "already_pro",
                    "before": before,
                    "after": before,
                }

            picker.click(timeout=8_000)
            timeout_seconds = max(2, min(30, int(self.options.model_switch_timeout_seconds or 12)))
            deadline = time.monotonic() + timeout_seconds
            option = None
            while time.monotonic() < deadline:
                option = self._first_visible(page, self._selectors("pro_model_option"))
                if option is not None and _looks_like_pro_model_label(_locator_control_text(option)):
                    break
                option = None
                time.sleep(0.25)

            if option is None:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                return {
                    "requested": True,
                    "selected": False,
                    "confirmed": False,
                    "status": "pro_option_not_found",
                    "before": before,
                }

            option_label = _locator_control_text(option)
            option.click(timeout=8_000)

            confirm_deadline = time.monotonic() + min(5, timeout_seconds)
            after = ""
            while time.monotonic() < confirm_deadline:
                current_picker = self._first_visible(page, self._selectors("model_picker"))
                after = _locator_control_text(current_picker) if current_picker is not None else ""
                if _looks_like_pro_model_label(after):
                    return {
                        "requested": True,
                        "selected": True,
                        "confirmed": True,
                        "status": "selected_pro",
                        "before": before,
                        "option": option_label,
                        "after": after,
                    }
                time.sleep(0.25)

            return {
                "requested": True,
                "selected": True,
                "confirmed": False,
                "status": "selected_unconfirmed",
                "before": before,
                "option": option_label,
                "after": after,
            }
        except Exception as exc:
            logger.warning("Gemini Pro model selection skipped after recoverable error: %s", exc)
            return {
                "requested": True,
                "selected": False,
                "confirmed": False,
                "status": "selection_error",
                "error": str(exc)[:500],
            }

    def _ensure_required_pro_model(self, selection: Dict[str, Any]) -> None:
        if not self.options.require_pro_model:
            return
        if isinstance(selection, dict) and selection.get("confirmed") is True:
            return
        status = str((selection or {}).get("status") or "unknown")
        raise GeminiBrowserAutomationError(
            f"未能确认 Gemini Pro 模型，已停止提交内容请求（模型切换状态: {status}）"
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

    def _select_image_generation_mode(self, page, started_at: float) -> None:
        self._raise_if_image_quota_exhausted(page)
        image_labels = (
            "制作图片", "制作图像", "生成图片", "生成图像", "创建图片", "创建图像",
            "Create image", "Create images", "Generate image", "Generate images",
        )
        direct_selectors = []
        for label in image_labels:
            direct_selectors.extend((
                f"button:has-text('{label}')",
                f"[role='button']:has-text('{label}')",
                f"[role='menuitem']:has-text('{label}')",
                f"[role='menuitemcheckbox']:has-text('{label}')",
                f"[role='option']:has-text('{label}')",
            ))
        if self._click_first_visible(page, direct_selectors):
            time.sleep(0.8)
            return

        tool_selectors = (
            "button:has-text('工具')",
            "[role='button']:has-text('工具')",
            "button:has-text('Tools')",
            "[role='button']:has-text('Tools')",
            "button[aria-label*='Tools']",
            "button[aria-label*='工具']",
            "button[aria-label*='上传和工具']",
            "button[aria-label*='Upload and tools']",
            "button[aria-label*='Add files and more']",
        )
        tool_button = self._wait_for_visible_enabled(
            page,
            list(tool_selectors),
            started_at,
            timeout_seconds=30,
        )
        if tool_button is None:
            raise GeminiBrowserAutomationError("未找到 Gemini 工具/生成图片入口")
        try:
            tool_button.click(timeout=10_000)
        except Exception as exc:
            raise GeminiBrowserAutomationError(f"Gemini 工具入口点击失败: {exc}") from exc

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            self._raise_if_image_quota_exhausted(page)
            if self._click_first_visible(page, direct_selectors):
                time.sleep(0.8)
                return
            time.sleep(0.5)
        raise GeminiBrowserAutomationError("Gemini 工具菜单中没有找到生成图片选项")

    def _click_first_visible(self, page, selectors) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    if candidate.is_visible() and candidate.is_enabled():
                        candidate.click(timeout=5_000)
                        return True
            except Exception:
                continue
        return False

    def _submit_prompt(self, page, prompt: str) -> None:
        self._raise_if_login_or_captcha(page)
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
            self._raise_if_login_or_captcha(page)
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

    def _wait_for_response(
        self,
        page,
        prompt: str,
        baseline_text: str,
        started_at: float,
        image_only_baseline_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + self.options.response_timeout_seconds
        active_generation_grace_deadline = deadline + max(
            0,
            _int_env("GEMINI_BROWSER_ACTIVE_GENERATION_GRACE_SECONDS", 300),
        )
        incomplete_extra_wait = max(0, _int_env("GEMINI_BROWSER_INCOMPLETE_RESPONSE_EXTRA_WAIT", 90))
        last_text = ""
        stable_since = time.monotonic()
        incomplete_since = None
        normalized_prompt = _clean_text(prompt)
        resubmit_attempts = 0
        last_resubmit_at = 0.0
        image_only_since = None
        idle_empty_since = None
        evidence_contract_expected = _expects_pdf_evidence_contract(prompt)

        while True:
            now = time.monotonic()
            if now >= deadline:
                # Gemini can still visibly be generating when the ordinary
                # response window expires.  Keep waiting for a bounded grace
                # period, still subject to the workflow's total timeout.
                if now >= active_generation_grace_deadline or not self._is_generating(page):
                    break
            self._guard_total_timeout(started_at)
            self._raise_if_login_or_captcha(page)
            candidate = self._latest_response_text(page, prompt, baseline_text)
            if candidate:
                if candidate != last_text:
                    last_text = candidate
                    stable_since = time.monotonic()
                    incomplete_since = None
                elif (
                    time.monotonic() - stable_since >= self.options.response_stable_seconds
                    and not self._is_generating(page)
                ):
                    if (
                        evidence_contract_expected
                        and not _has_complete_pdf_evidence_payload(candidate)
                    ):
                        if incomplete_since is None:
                            incomplete_since = time.monotonic()
                        time.sleep(1)
                        continue
                    if _looks_like_incomplete_response(candidate):
                        if incomplete_since is None:
                            incomplete_since = time.monotonic()
                        if time.monotonic() - incomplete_since < incomplete_extra_wait:
                            time.sleep(1)
                            continue
                    return {"text": candidate}
            elif image_only_baseline_keys is not None and self._has_new_large_image(
                page,
                image_only_baseline_keys,
            ):
                state = self._composer_submit_state(page, "")
                finished = (
                    not self._is_generating(page)
                    and state.get("enabledSend")
                    and not state.get("busy")
                )
                if finished:
                    if image_only_since is None:
                        image_only_since = time.monotonic()
                    elif time.monotonic() - image_only_since >= max(
                        3.0,
                        _float_env("GEMINI_BROWSER_IMAGE_ONLY_STABLE_SECONDS", 8.0),
                    ):
                        return {"text": "", "image_only": True}
                else:
                    image_only_since = None
            elif normalized_prompt and resubmit_attempts < 3:
                now = time.monotonic()
                generating = self._is_generating(page)
                state = self._composer_submit_state(page, "")
                composer_text = _clean_text(self._composer_text(page))
                prompt_still_in_composer = normalized_prompt in composer_text
                idle_empty = (
                    not composer_text
                    and state.get("enabledSend")
                    and not state.get("busy")
                    and not generating
                )
                if idle_empty:
                    idle_empty_since = idle_empty_since or now
                else:
                    idle_empty_since = None
                stalled_after_submit = bool(
                    idle_empty_since
                    and now - idle_empty_since
                    >= max(
                        30.0,
                        _float_env("GEMINI_BROWSER_IDLE_EMPTY_RESUBMIT_SECONDS", 60.0),
                    )
                )
                if now - last_resubmit_at >= 8:
                    if prompt_still_in_composer and state.get("enabledSend") and not state.get("busy") and not generating:
                        button = self._wait_for_send_button(page)
                        if button is not None:
                            self._click_send_button(page, button, force=resubmit_attempts > 0)
                            resubmit_attempts += 1
                            last_resubmit_at = now
                            idle_empty_since = None
                            time.sleep(2)
                            continue
                    elif stalled_after_submit:
                        self._submit_prompt(page, prompt)
                        resubmit_attempts += 1
                        last_resubmit_at = now
                        idle_empty_since = None
                        time.sleep(2)
                        continue
            time.sleep(1)

        if (
            last_text
            and evidence_contract_expected
            and not _has_complete_pdf_evidence_payload(last_text)
        ):
            raise GeminiBrowserAutomationError(
                "等待 Gemini 回复超时：证据契约已请求，但未捕获到完整的 "
                "PDF_EVIDENCE_V1_JSON/claims 或兼容 evidence_map JSON"
            )
        if last_text:
            return {"text": last_text}
        debug = _submit_state_summary(self._composer_submit_state(page, ""), self._composer_text(page))
        if resubmit_attempts:
            debug = f"{debug}; resubmit_attempts={resubmit_attempts}" if debug else f"resubmit_attempts={resubmit_attempts}"
        suffix = f"; {debug}" if debug else ""
        raise GeminiBrowserAutomationError(f"等待 Gemini 回复超时{suffix}")

    def _has_new_large_image(self, page, baseline_keys: List[str]) -> bool:
        baseline = {str(item or "") for item in (baseline_keys or [])}
        for record in self._visible_image_records(page):
            key = str(
                record.get("src")
                or record.get("currentSrc")
                or record.get("alt")
                or record.get("dom_index")
                or ""
            )
            area = float(record.get("width") or 0) * float(record.get("height") or 0)
            if key not in baseline and area >= 30_000:
                return True
        return False

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
            self._raise_if_image_quota_exhausted(page)
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
        pending = output.with_suffix(f"{output.suffix}.part")
        if pending.exists():
            pending.unlink()
        src = record.get("currentSrc") or record.get("src") or ""
        saved_by = "screenshot"
        if src:
            try:
                response = page.context.request.get(src, timeout=30_000)
                content_type = str(response.headers.get("content-type") or "").lower()
                body = response.body() if response.ok else b""
                if body and (content_type.startswith("image/") or _looks_like_image_bytes(body)):
                    pending.write_bytes(body)
                    saved_by = "context_request"
            except Exception:
                saved_by = "screenshot"
        if src and (not pending.exists() or pending.stat().st_size <= 0):
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
                    pending.write_bytes(base64.b64decode(payload))
                    saved_by = "fetch"
            except Exception:
                saved_by = "screenshot"

        if not pending.exists() or pending.stat().st_size <= 0:
            dom_index = record.get("dom_index")
            if dom_index is None:
                raise GeminiBrowserAutomationError("无法定位 Gemini 生成图片")
            saved_by = self._save_isolated_image_screenshot(
                page,
                int(dom_index),
                pending,
            )

        if not pending.exists() or pending.stat().st_size <= 0:
            raise GeminiBrowserAutomationError("Gemini 新图片保存为空")
        pending.replace(output)

        return {
            "path": str(output),
            "bytes": output.stat().st_size,
            "source": src,
            "saved_by": saved_by,
            "width": record.get("naturalWidth") or record.get("width"),
            "height": record.get("naturalHeight") or record.get("height"),
        }

    def _save_isolated_image_screenshot(self, page, dom_index: int, output: Path) -> str:
        """Screenshot a cloned image above page overlays.

        Gemini occasionally shows a privacy/help popover over the generated
        image. Locator screenshots include that popover, so the fallback must
        isolate the image before capture.
        """

        marker = f"gemini-image-capture-{int(time.time() * 1000)}-{dom_index}"
        try:
            created = page.evaluate(
                """({ index, marker }) => {
                    const source = document.querySelectorAll("img")[index];
                    if (!source) return false;
                    const rect = source.getBoundingClientRect();
                    const host = document.createElement("div");
                    host.dataset.geminiIsolatedCapture = marker;
                    Object.assign(host.style, {
                        position: "fixed",
                        left: "0",
                        top: "0",
                        zIndex: "2147483647",
                        width: `${Math.max(1, Math.round(rect.width))}px`,
                        height: `${Math.max(1, Math.round(rect.height))}px`,
                        overflow: "hidden",
                        background: "transparent",
                        isolation: "isolate",
                    });
                    const clone = source.cloneNode(true);
                    Object.assign(clone.style, {
                        display: "block",
                        width: "100%",
                        height: "100%",
                        maxWidth: "none",
                        maxHeight: "none",
                        margin: "0",
                        objectFit: getComputedStyle(source).objectFit || "fill",
                    });
                    host.appendChild(clone);
                    document.body.appendChild(host);
                    return true;
                }""",
                {"index": dom_index, "marker": marker},
            )
            if created:
                page.locator(f'[data-gemini-isolated-capture="{marker}"] img').screenshot(path=str(output))
                return "isolated_screenshot"
        except Exception:
            logger.warning(
                "Gemini isolated image capture failed for job %s; using locator fallback",
                getattr(self, "_current_job_id", ""),
                exc_info=True,
            )
        finally:
            try:
                page.evaluate(
                    """(marker) => {
                        document.querySelectorAll("[data-gemini-isolated-capture]").forEach((node) => {
                            if (node.dataset.geminiIsolatedCapture === marker) node.remove();
                        });
                    }""",
                    marker,
                )
            except Exception:
                pass

        page.locator("img").nth(dom_index).screenshot(path=str(output))
        return "screenshot"

    def _save_embedded_response_images(
        self,
        page,
        baseline_keys: List[str],
        output_dir: str,
    ) -> List[Dict[str, Any]]:
        """Persist large images newly added by one Gemini text response.

        Storyboard Gems do not behave consistently: some generate every image,
        some generate only a prefix, and some return text only.  This collector
        is best-effort so a capture failure never fails the storyboard itself.
        """
        baseline = {str(item or "") for item in (baseline_keys or [])}
        records = []
        seen = set()
        for record in self._visible_image_records(page):
            key = str(
                record.get("currentSrc")
                or record.get("src")
                or record.get("alt")
                or record.get("dom_index")
                or ""
            )
            area = float(record.get("width") or 0) * float(record.get("height") or 0)
            if not key or key in baseline or key in seen or area < 30_000:
                continue
            seen.add(key)
            records.append(record)

        records.sort(key=lambda item: int(item.get("dom_index") or 0))
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        saved = []
        for ordinal, record in enumerate(records, start=1):
            try:
                info = self._save_generated_image(
                    page,
                    record,
                    str(root / f"{ordinal:03d}.png"),
                )
            except Exception as exc:
                logger.warning(
                    "Skipping Gemini embedded response image %s for job %s: %s",
                    ordinal,
                    self._current_job_id,
                    exc,
                )
                continue
            saved.append({
                **info,
                "ordinal": ordinal,
                "dom_index": record.get("dom_index"),
            })
        return saved

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
            text = _strip_gemini_page_suffix(text)
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
        body = _strip_gemini_page_suffix(body)
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
        if "【文案原句】" in text and ("【AI 提示词】" in text or "【AI提示词】" in text):
            return True
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
                "button[aria-label*='上传和工具']",
                "button[aria-label*='Upload and tools']",
                "button[aria-label*='Add files and more']",
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
            "model_picker": [
                "button[data-test-id='bard-mode-menu-button']",
                "bard-mode-switcher button[aria-haspopup='true']",
                "input-area-v2 .model-picker-container button",
                "button.input-area-switch[aria-haspopup='true']",
                "button[aria-label*='模式选择器']",
                "button[aria-label*='当前模式']",
                "button[aria-label*='model' i]",
                "[role='button'][aria-label*='model' i]",
                "button[aria-label*='mode' i]",
                "[role='button'][aria-label*='mode' i]",
                "button[aria-label*='模型']",
                "[role='button'][aria-label*='模型']",
                "button[title*='model' i]",
                "button[title*='模型']",
                "button[data-test-id*='model' i]",
                "button[data-testid*='model' i]",
                "button[class*='model-selector']",
                "button[aria-haspopup='menu']:has-text('Pro')",
                "button[aria-haspopup='menu']:has-text('Fast')",
                "button[aria-haspopup='menu']:has-text('Thinking')",
                "button[aria-haspopup='listbox']:has-text('Pro')",
                "button[aria-haspopup='listbox']:has-text('Fast')",
                "button[aria-haspopup='listbox']:has-text('Thinking')",
            ],
            "pro_model_option": [
                "[data-test-id='gem-mode-menu'] [role='menuitem']:has-text('Pro')",
                "[data-test-id^='bard-mode-option-']:has-text('Pro')",
                "[role='menuitemradio']:has-text('Pro')",
                "[role='option']:has-text('Pro')",
                "[role='menuitem']:has-text('Pro')",
                "mat-option:has-text('Pro')",
                ".mat-mdc-menu-item:has-text('Pro')",
                "button[role='menuitem']:has-text('Pro')",
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
                "button:has-text('Sign in')",
                "a:has-text('Sign in')",
                "[role='button']:has-text('Sign in')",
                "button:has-text('Login')",
                "a:has-text('Login')",
                "[role='button']:has-text('Login')",
                "button:has-text('登录')",
                "a:has-text('登录')",
                "[role='button']:has-text('登录')",
                "button:has-text('使用 Google 账号')",
                "a:has-text('使用 Google 账号')",
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
                "[role='button'][aria-label*='Stop']",
                "[role='button'][aria-label*='停止']",
                "[role='progressbar']",
                ".mat-mdc-progress-spinner",
                "mat-spinner",
            ],
        }
        configured = self.options.selectors.get(key) or []
        return configured + defaults.get(key, [])

    def _first_visible(self, page, selectors: List[str]):
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()
                for index in range(count):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        return candidate
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

    def _notify_human_intervention(self, page, *, reason: str, message: str) -> Dict[str, Any]:
        job_id = str(getattr(self, "_current_job_id", "") or "")
        screenshot = self._save_failure_screenshot(page, job_id or "browser_auth")
        context = {
            "job_id": job_id,
            "page_url": str(getattr(page, "url", "") or ""),
            "screenshot_path": screenshot,
            "profile": str(self.options.user_data_dir or ""),
            "worker_id": str(self.options.profile_directory or "Default"),
        }
        notification = workflow_notification_service.send_browser_human_intervention(
            provider="Gemini",
            reason=reason,
            message=message,
            details=dict(getattr(self, "_human_intervention_details", {}) or {}),
            **context,
        )
        return {"notification": notification, "context": context}

    def _raise_if_login_or_captcha(self, page) -> None:
        if self._first_visible(page, self._selectors("captcha")) is not None:
            message = "Gemini 页面出现验证码或安全验证，需要人工处理"
            intervention = self._notify_human_intervention(
                page,
                reason="verification",
                message=message,
            )
            raise GeminiBrowserHumanInterventionError(
                message,
                reason="verification",
                **intervention,
            )
        if self._first_visible(page, self._selectors("login")) is not None:
            message = "Gemini 登录态失效，需要先在浏览器 profile 中登录"
            intervention = self._notify_human_intervention(
                page,
                reason="login",
                message=message,
            )
            raise GeminiBrowserHumanInterventionError(
                message,
                reason="login",
                **intervention,
            )

    def _raise_if_image_quota_exhausted(self, page) -> None:
        try:
            text = page.locator("body").inner_text(timeout=1_000)
        except Exception:
            return
        if _is_image_quota_text(text):
            raise GeminiBrowserAutomationError(
                "Gemini 图片生成额度已耗尽，请等待额度重置后再继续"
            )

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
            raise GeminiBrowserAutomationError(
                f"任务总超时（{self.options.total_timeout_seconds} 秒）"
            )

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


def _write_generation_checkpoint(
    output: Path,
    *,
    prompt: str,
    followups: List[str],
    initial_response_text: str,
    combined_response_text: str,
    turns: List[Dict[str, Any]],
    page_url: str,
    target_url: str,
    embedded_images: List[Dict[str, Any]],
) -> None:
    """Persist every completed Gemini turn so a later timeout does not erase it."""
    payload = {
        "status": "partial",
        "prompt": prompt,
        "followup_prompts": followups,
        "initial_response_text": initial_response_text,
        "response_text": combined_response_text,
        "final_response_text": combined_response_text,
        "turns": turns,
        "page_url": page_url,
        "target_url": target_url,
        "embedded_images": embedded_images,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    temp_output = output.with_name(f".{output.name}.tmp")
    temp_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_output.replace(output)


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

    # A shared signed-in profile is intentionally serial. Queueing behind other
    # valid long-form turns must not fail before this job gets a chance to run.
    default_lock_timeout = max(
        1800,
        int(getattr(options, "total_timeout_seconds", 0) or 0) * 3,
    )
    timeout_seconds = max(
        1,
        _int_env("GEMINI_BROWSER_PROFILE_LOCK_TIMEOUT_SECONDS", default_lock_timeout),
    )
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


def _profile_process_info(pid: int) -> Dict[str, Any]:
    """Return the parent pid and command line for a suspected Chrome owner."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "ppid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    line = str(result.stdout or "").strip()
    if not line:
        return {}
    parts = line.split(None, 1)
    if len(parts) != 2:
        return {}
    try:
        ppid = int(parts[0])
    except ValueError:
        return {}
    return {"pid": int(pid), "ppid": ppid, "command": parts[1]}


def _command_uses_exact_user_data_dir(command: str, user_data_dir: Path) -> bool:
    expected = str(user_data_dir.expanduser().resolve())
    try:
        argv = shlex.split(str(command or ""))
    except ValueError:
        argv = str(command or "").split()
    for arg in argv:
        if not arg.startswith("--user-data-dir="):
            continue
        actual = arg.split("=", 1)[1]
        try:
            if str(Path(actual).expanduser().resolve()) == expected:
                return True
        except (OSError, RuntimeError):
            continue
    return False


def _recover_orphaned_chrome_profile(
    user_data_dir: Path,
    owner: str,
    *,
    enabled: bool = False,
) -> bool:
    """Terminate only an orphaned Chrome process owned by this automation profile.

    A live SingletonLock is normally treated as a real user session.  The
    recovery path is opt-in and additionally requires an orphaned process
    (PPID 1), the exact profile path, and the launch flags used by our
    Playwright workers.  That combination prevents a normal Chrome window from
    being mistaken for a stale worker process.
    """
    if not enabled:
        return False
    pid = _singleton_pid(owner)
    if pid is None or pid == os.getpid() or not _pid_is_running(pid):
        return False
    info = _profile_process_info(pid)
    command = str(info.get("command") or "")
    if (
        int(info.get("ppid") or 0) != 1
        or "--no-startup-window" not in command
        or "--use-mock-keychain" not in command
        or not _command_uses_exact_user_data_dir(command, user_data_dir)
    ):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False

    timeout_seconds = max(
        1,
        _int_env("GEMINI_BROWSER_ORPHAN_CHROME_TERMINATE_WAIT_SECONDS", 5),
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.1)
    return False


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


def _strip_gemini_page_suffix(value: str) -> str:
    """Remove Gemini's disclaimer/footer without discarding a valid reply."""
    text = str(value or "").strip()
    lowered = text.lower()
    markers = (
        "gemini 是一款 ai 工具",
        "gemini can make mistakes",
    )
    positions = [lowered.find(marker) for marker in markers]
    positions = [position for position in positions if position >= 0]
    if positions:
        text = text[:min(positions)].rstrip()
    return text


def _upload_is_ready(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("busy") or state.get("canceled"):
        return False
    return bool(state.get("hasName") and state.get("enabledSend"))


def _looks_like_incomplete_response(text: Any) -> bool:
    clean = _clean_text(str(text or ""))
    if not clean:
        return True
    if clean.endswith((":", "：")):
        return True
    token = r"\d{1,2}:\d{2}(?::\d{2})?"
    if re.search(rf"(?:开始|起始)(?:时间戳|时间)?\s*[:：]\s*{token}\s*$", clean):
        has_end = re.search(rf"(?:结束|截止|终止)(?:时间戳|时间)?\s*[:：]\s*{token}", clean)
        has_range = re.search(rf"{token}\s*(?:-|~|～|至|到)\s*{token}", clean)
        if not has_end and not has_range:
            return True
    dangling_re = re.compile(
        r"(?:"
        r"版本[一二三四五六七八九十A-Za-z0-9]+|"
        r"推荐截取时间戳|推荐截取时间|开始时间戳|结束时间戳|"
        r"精华片段提取|内容概述与看点"
        r")\s*[:：]?\s*$"
    )
    return bool(dangling_re.search(clean))


def _expects_pdf_evidence_contract(prompt: Any) -> bool:
    value = str(prompt or "").lower()
    return (
        "pdf_evidence_v1_json" in value
        or "report_pdf_evidence_v1" in value
    )


def _has_complete_pdf_evidence_payload(text: Any) -> bool:
    """Require a decodable final evidence block, not a partially streamed prefix."""

    raw = str(text or "")
    decoder = json.JSONDecoder()
    index = 0
    decoded = 0
    while index < len(raw) and decoded < 200:
        match = re.search(r"[\{\[]", raw[index:])
        if not match:
            return False
        start = index + match.start()
        try:
            payload, consumed = decoder.raw_decode(raw[start:])
        except ValueError:
            index = start + 1
            continue
        decoded += 1
        if _contains_pdf_evidence_envelope(payload):
            return True
        index = start + max(1, consumed)
    return False


def _contains_pdf_evidence_envelope(value: Any) -> bool:
    if isinstance(value, dict):
        schema = str(value.get("schema_version") or "").strip().lower()
        if schema == "report_pdf_evidence_v1" and isinstance(value.get("claims"), list):
            return True
        if isinstance(value.get("evidence_map"), list):
            return True
        return any(
            _contains_pdf_evidence_envelope(item)
            for item in value.values()
            if isinstance(item, (dict, list))
        )
    if isinstance(value, list):
        return any(_contains_pdf_evidence_envelope(item) for item in value)
    return False


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


def _locator_control_text(locator) -> str:
    if locator is None:
        return ""
    values = []
    try:
        values.append(locator.inner_text(timeout=2_000))
    except Exception:
        pass
    for name in ("aria-label", "title", "data-test-id", "data-testid"):
        try:
            values.append(locator.get_attribute(name, timeout=2_000))
        except Exception:
            continue
    return " | ".join(
        value
        for value in (_clean_text(str(item or "")) for item in values)
        if value
    )[:300]


def _looks_like_pro_model_label(value: Any) -> bool:
    text = _clean_text(str(value or ""))
    if not text or len(text) > 300:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("upgrade", "get pro", "try pro", "升级", "订阅")):
        return False
    return bool(re.search(r"(?:^|[^a-z])pro(?:$|[^a-z])", lowered))


def _pro_model_selection_message(result: Any) -> str:
    data = result if isinstance(result, dict) else {}
    status = str(data.get("status") or "")
    if status == "already_pro":
        return "Gemini 当前已是 Pro 模型"
    if status == "selected_pro":
        return "已切换并确认 Gemini Pro 模型"
    if status == "selected_unconfirmed":
        return "已点击 Gemini Pro 模型，页面未提供可确认的模型标签，继续上传"
    if status == "disabled":
        return "Gemini Pro 模型优先已关闭，继续使用当前模型"
    if status == "picker_not_found":
        return "当前 Gemini 页面没有模型切换入口，继续使用页面默认模型"
    if status == "pro_option_not_found":
        return "Gemini 模型菜单没有可用的 Pro 选项，继续使用当前模型"
    return "Gemini Pro 模型切换未成功，继续使用当前模型"


def _contract_diagnostics(value: Any) -> Dict[str, Any]:
    diagnostics = dict(value) if isinstance(value, dict) else {"valid": bool(value)}
    diagnostics["valid"] = bool(diagnostics.get("valid"))
    raw_codes = diagnostics.get("error_codes")
    if isinstance(raw_codes, (list, tuple)):
        diagnostics["error_codes"] = [str(item) for item in raw_codes if str(item).strip()]
    else:
        diagnostics["error_codes"] = []
    raw_errors = diagnostics.get("errors")
    diagnostics["errors"] = list(raw_errors) if isinstance(raw_errors, (list, tuple)) else []
    return diagnostics


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
