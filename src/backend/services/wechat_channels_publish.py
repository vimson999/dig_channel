"""Fail-closed browser boundary for WeChat Channels video publishing.

Plaintext Cookies enter this module only at runtime. They must never be copied
to PublicationTask params/results, logs, screenshots, or API responses.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


WECHAT_CHANNELS_UPLOAD_URL = "https://channels.weixin.qq.com/platform/post/create"
_COOKIE_FIELDS = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
_VISIBILITY_LABELS = {"private": "仅自己可见", "public": "公开"}


class WechatChannelsBrowserError(RuntimeError):
    pass


class WechatChannelsAuthenticationRequired(WechatChannelsBrowserError):
    pass


class WechatChannelsVerificationRequired(WechatChannelsBrowserError):
    pass


def _json_payload(payload: str) -> Any:
    try:
        return json.loads(str(payload or "").strip())
    except (TypeError, ValueError):
        return None


def _cookie_items_from_json(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("cookies"), list):
        return [item for item in value["cookies"] if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("cookies"), dict):
        return _cookie_items_from_json(value["cookies"])
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [
            {"name": str(name), "value": str(cookie_value)}
            for name, cookie_value in value.items()
            if isinstance(cookie_value, (str, int, float))
        ]
    return []


def _cookie_items_from_header(payload: str) -> List[Dict[str, Any]]:
    text = str(payload or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    items = []
    for part in text.replace("\r", "").replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name and not any(char.isspace() for char in name):
            items.append({"name": name, "value": value.strip()})
    return items


def playwright_cookies_from_payload(payload: str) -> List[Dict[str, Any]]:
    parsed = _json_payload(payload)
    raw_items = _cookie_items_from_json(parsed) if parsed is not None else _cookie_items_from_header(payload)
    cookies = []
    seen = set()
    for raw in raw_items:
        name = str(raw.get("name") or "").strip()
        if not name or raw.get("value") is None:
            continue
        cookie = {key: raw[key] for key in _COOKIE_FIELDS if raw.get(key) is not None}
        cookie["name"] = name
        cookie["value"] = str(raw.get("value"))
        cookie["domain"] = str(cookie.get("domain") or ".weixin.qq.com").strip()
        cookie["path"] = str(cookie.get("path") or "/")
        if "expirationDate" in raw and "expires" not in cookie:
            cookie["expires"] = raw["expirationDate"]
        if "sameSite" in cookie:
            normalized = str(cookie["sameSite"]).strip().lower()
            cookie["sameSite"] = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}.get(normalized, "Lax")
        if cookie.get("expires") in (None, "", 0, -1):
            cookie.pop("expires", None)
        elif "expires" in cookie:
            try:
                cookie["expires"] = float(cookie["expires"])
            except (TypeError, ValueError):
                cookie.pop("expires", None)
        identity = (cookie["name"], cookie["domain"], cookie["path"])
        if identity not in seen:
            seen.add(identity)
            cookies.append(cookie)
    return cookies


def _bounded_timeout_ms() -> int:
    try:
        seconds = int(os.environ.get("WECHAT_CHANNELS_BROWSER_TIMEOUT_SECONDS", "35") or 35)
    except (TypeError, ValueError):
        seconds = 35
    return max(10, min(seconds, 90)) * 1000


def _bounded_upload_timeout_ms() -> int:
    try:
        seconds = int(os.environ.get("WECHAT_CHANNELS_UPLOAD_TIMEOUT_SECONDS", "1200") or 1200)
    except (TypeError, ValueError):
        seconds = 1200
    return max(60, min(seconds, 3600)) * 1000


def _truthy_env(name: str, default: bool) -> bool:
    return str(os.environ.get(name, "1" if default else "0")).strip().lower() not in {"0", "false", "no", "off"}


def _browser_launch_kwargs() -> Dict[str, Any]:
    headless = _truthy_env("WECHAT_CHANNELS_BROWSER_HEADLESS", True)
    configured = str(os.environ.get("WECHAT_CHANNELS_BROWSER_EXECUTABLE_PATH") or "").strip()
    kwargs: Dict[str, Any] = {"headless": headless}
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise WechatChannelsBrowserError(f"视频号浏览器路径不存在: {path}")
        kwargs["executable_path"] = str(path)
    if headless:
        kwargs["headless"] = False
        kwargs["args"] = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"]
    return kwargs


def compose_wechat_channels_description(title: str, description: str = "", tags: Any = None) -> str:
    title = re.sub(r"\s+", " ", str(title or "").strip())[:100]
    description = re.sub(r"[ \t]+", " ", str(description or "").strip())[:1000]
    if isinstance(tags, str):
        items = re.split(r"[,，、\s]+", tags)
    elif isinstance(tags, (list, tuple, set)):
        items = list(tags)
    else:
        items = []
    normalized = []
    seen = set()
    for item in items:
        tag = re.sub(r"\s+", "", str(item or "").strip().lstrip("#"))[:20]
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(f"#{tag}")
        if len(normalized) >= 10:
            break
    return "\n".join(part for part in (title, description, " ".join(normalized)) if part)[:1000]


def _classify_page(page: Any) -> Dict[str, str]:
    url = str(getattr(page, "url", "") or "").lower()
    try:
        body = str(page.locator("body").inner_text(timeout=3000) or "")
    except Exception:
        body = ""
    if any(text in body for text in ("安全验证", "验证码", "完成验证", "访问频繁")):
        return {"status": "verification_required", "message": "视频号要求人工完成安全验证，请处理后重新校验"}
    if "/login" in url or any(text in body for text in ("扫码登录", "微信扫码", "请使用微信扫描")):
        return {"status": "auth_required", "message": "视频号 Cookie 已失效或未登录，请重新导出 Cookie"}
    try:
        if page.locator('input[type="file"]').count() > 0:
            return {"status": "valid", "message": "登录态校验通过：已进入视频号发表页"}
    except Exception:
        pass
    if "channels.weixin.qq.com/platform" in url and any(text in body for text in ("发表视频", "视频管理", "发表动态")):
        return {"status": "valid", "message": "登录态校验通过：已进入视频号助手"}
    return {"status": "unknown", "message": "已打开视频号助手，但无法确认发表页状态；Cookie 未被自动判为失效"}


def _wait_for_classified_page(page: Any, timeout_ms: int) -> Dict[str, str]:
    deadline = time.monotonic() + min(max(timeout_ms / 1000, 1), 12)
    state = _classify_page(page)
    while state["status"] == "unknown" and time.monotonic() < deadline:
        page.wait_for_timeout(500)
        state = _classify_page(page)
    return state


def _first_existing(page: Any, selectors: List[str]) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count():
                return locator
        except Exception:
            continue
    return None


def _first_visible(page: Any, selectors: List[str]) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


def _raise_for_page_state(page: Any) -> None:
    state = _classify_page(page)
    if state["status"] == "auth_required":
        raise WechatChannelsAuthenticationRequired(state["message"])
    if state["status"] == "verification_required":
        raise WechatChannelsVerificationRequired(state["message"])


def _selected(control: Any) -> bool:
    try:
        inputs = control.locator('input[type="radio"],input[type="checkbox"]')
        if inputs.count() and inputs.first.is_checked(timeout=1000):
            return True
    except Exception:
        pass
    try:
        if str(control.get_attribute("aria-checked") or "").lower() == "true":
            return True
        if str(control.get_attribute("aria-selected") or "").lower() == "true":
            return True
        classes = str(control.get_attribute("class") or "").lower()
        return any(token in classes for token in ("checked", "selected", "active"))
    except Exception:
        return False


def _set_visibility(page: Any, visibility: str, deadline: float) -> str:
    normalized = str(visibility or "private").strip().lower()
    label_text = _VISIBILITY_LABELS.get(normalized)
    if not label_text:
        raise WechatChannelsBrowserError("视频号当前仅支持公开或仅自己可见")
    while time.monotonic() < deadline:
        try:
            option_text = page.get_by_text(label_text, exact=True).last
            if option_text.count() and option_text.is_visible(timeout=800):
                control = option_text.locator("xpath=ancestor::label[1]")
                if not control.count():
                    control = option_text.locator("xpath=ancestor::*[@role='radio' or @role='option'][1]")
                if not control.count():
                    control = option_text
                if not _selected(control):
                    control.click(timeout=3000)
                    page.wait_for_timeout(350)
                if _selected(control):
                    return normalized
            trigger = _first_visible(page, [
                '[class*="visible"]', '[class*="privacy"]',
                'text="谁可以看"', 'text="可见范围"', 'text="公开"',
            ])
            if trigger is not None:
                trigger.click(timeout=2500)
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise WechatChannelsBrowserError(f"无法确认视频号可见范围已切换为“{label_text}”，已停止发布")


def _publish_button(page: Any) -> Any:
    for label in ("发表", "发布"):
        try:
            button = page.get_by_role("button", name=label, exact=True).first
            if button.count() and button.is_visible(timeout=800):
                return button
        except Exception:
            pass
    return _first_visible(page, ['button:has-text("发表")', 'button:has-text("发布")'])


def _fill_text(locator: Any, value: str) -> None:
    try:
        locator.fill(value)
    except Exception:
        locator.click()
        locator.press("Meta+A")
        locator.press("Backspace")
        locator.type(value)


def publish_wechat_channels_video(
    *,
    cookies: List[Dict[str, Any]],
    video_path: str,
    title: str,
    description: str = "",
    tags: Any = None,
    visibility: str = "private",
    submit: bool = False,
    progress: Optional[Callable[[str, int, str], None]] = None,
) -> Dict[str, Any]:
    path = Path(str(video_path or "")).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise WechatChannelsBrowserError("测试视频不存在或为空")
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise WechatChannelsBrowserError("视频号上传测试仅支持 MP4、MOV、M4V")
    if not cookies:
        raise WechatChannelsAuthenticationRequired("视频号 Cookie 为空")
    copy_text = compose_wechat_channels_description(title, description, tags)
    short_title = re.sub(r"\s+", "", str(title or "").strip())[:16]
    if len(short_title) < 6:
        raise WechatChannelsBrowserError("视频号短标题至少需要 6 个字符")
    normalized_visibility = str(visibility or "private").strip().lower()
    if normalized_visibility not in _VISIBILITY_LABELS:
        raise WechatChannelsBrowserError("视频号当前仅支持公开或仅自己可见")

    def report(step: str, percent: int, message: str) -> None:
        if progress:
            progress(step, percent, message)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WechatChannelsBrowserError("缺少 playwright 依赖，无法上传视频号视频") from exc

    timeout_ms = _bounded_timeout_ms()
    upload_timeout_ms = _bounded_upload_timeout_ms()
    report("opening_creator", 15, "正在打开视频号助手")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**_browser_launch_kwargs())
            try:
                context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(WECHAT_CHANNELS_UPLOAD_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                state = _wait_for_classified_page(page, timeout_ms)
                if state["status"] == "auth_required":
                    raise WechatChannelsAuthenticationRequired(state["message"])
                if state["status"] == "verification_required":
                    raise WechatChannelsVerificationRequired(state["message"])
                file_input = _first_existing(page, ['input[type="file"][accept*="video"]', 'input[type="file"]'])
                if file_input is None:
                    raise WechatChannelsBrowserError("未找到视频号视频上传控件，页面结构可能已变化")
                report("uploading_video", 35, "正在上传视频文件")
                file_input.set_input_files(str(path))
                deadline = time.monotonic() + upload_timeout_ms / 1000
                editor = None
                while time.monotonic() < deadline:
                    _raise_for_page_state(page)
                    editor = _first_visible(page, [
                        'textarea[placeholder*="描述"]', 'textarea[placeholder*="说点什么"]',
                        '[contenteditable="true"]',
                    ])
                    if editor is not None:
                        break
                    page.wait_for_timeout(1500)
                if editor is None:
                    raise WechatChannelsBrowserError("等待视频号视频进入编辑器超时")
                report("filling_copy", 72, "视频已上传，正在填写描述和短标题")
                _fill_text(editor, copy_text)
                title_input = _first_visible(page, ['input[placeholder*="短标题"]', 'input[placeholder*="标题"]'])
                if title_input is not None:
                    _fill_text(title_input, short_title)
                report("setting_visibility", 82, "正在设置视频号可见范围")
                selected_visibility = _set_visibility(page, normalized_visibility, deadline)
                publish_button = None
                while time.monotonic() < deadline:
                    _raise_for_page_state(page)
                    publish_button = _publish_button(page)
                    if publish_button is not None:
                        try:
                            if publish_button.is_enabled(timeout=1000):
                                break
                        except Exception:
                            pass
                    page.wait_for_timeout(1500)
                else:
                    raise WechatChannelsBrowserError("等待视频号处理完成或发表按钮可用超时")
                if not submit:
                    report("prepared", 100, "上传、文案和私密范围校验通过，未点击发表")
                    return {
                        "publish_status": "prepared", "submitted": False,
                        "visibility": selected_visibility,
                        "message": "视频上传、文案和可见范围已完成；测试模式未点击发表",
                    }
                report("submitting", 90, "正在点击发表，请勿重复投递")
                publish_button = _publish_button(page)
                if publish_button is None or not publish_button.is_enabled(timeout=2000):
                    raise WechatChannelsBrowserError("发表按钮不可用，请检查页面必填项")
                publish_button.click()
                page.wait_for_timeout(5000)
                _raise_for_page_state(page)
                report("submitted", 100, "已向视频号提交，等待平台处理")
                return {
                    "publish_status": "submitted", "submitted": True,
                    "visibility": selected_visibility,
                    "message": "已点击发表；当前仅确认 submitted，尚未确认作品可见",
                }
            finally:
                browser.close()
    except (WechatChannelsAuthenticationRequired, WechatChannelsVerificationRequired, WechatChannelsBrowserError):
        raise
    except Exception as exc:
        raise WechatChannelsBrowserError(f"视频号浏览器上传失败：{exc}") from exc


def check_wechat_channels_session(payload: str) -> Dict[str, str]:
    cookies = playwright_cookies_from_payload(payload)
    if not cookies:
        return {"status": "auth_required", "message": "视频号 Cookie 为空"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WechatChannelsBrowserError("缺少 playwright 依赖，无法校验视频号登录态") from exc
    timeout_ms = _bounded_timeout_ms()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**_browser_launch_kwargs())
            try:
                context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(WECHAT_CHANNELS_UPLOAD_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                return _wait_for_classified_page(page, timeout_ms)
            finally:
                browser.close()
    except WechatChannelsBrowserError:
        raise
    except Exception as exc:
        raise WechatChannelsBrowserError(f"视频号登录态校验失败：{exc}") from exc
