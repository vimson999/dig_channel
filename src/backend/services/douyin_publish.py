"""Douyin creator-center browser boundary.

This module deliberately owns browser Cookie conversion and session-state
classification. Publication orchestration must pass only a credential id; the
plaintext Cookie payload must never be copied into PublicationTask params,
results, logs, or screenshots.
"""

import json
import hashlib
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - local deployment is macOS/Linux
    fcntl = None


DOUYIN_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
DOUYIN_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
LOGGER = logging.getLogger(__name__)
_ACTIVE_CREDENTIAL_LOCKS: Dict[str, Dict[str, Any]] = {}
_COOKIE_FIELDS = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
_AUTH_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_guard", "sid_tt"}
_VISIBILITY_LABELS = {
    "public": "公开",
    "friends": "好友可见",
    "private": "仅自己可见",
}
_DECLARATION_LABELS = {
    "personal_opinion": "内容为个人观点或见解",
    "ai_generated": "内容由AI生成",
    "reposted": "内容为转载信息",
    "marketing": "内容含营销推广信息",
    "fictional": "虚构演绎，仅供娱乐",
    "none": "无需添加自主声明",
}


class DouyinBrowserError(RuntimeError):
    pass


class DouyinAuthenticationRequired(DouyinBrowserError):
    pass


class DouyinVerificationRequired(DouyinBrowserError):
    pass


class DouyinSubmissionPendingVerification(DouyinBrowserError):
    def __init__(self, message: str, *, credential_expired: bool = False):
        super().__init__(message)
        self.credential_expired = credential_expired


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _int_env(name: str, default: int, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _credential_key(credential_id: Any) -> str:
    value = str(credential_id or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20] if value else "default"


def _douyin_profile_dir(credential_id: Any) -> Path:
    configured = str(os.environ.get("DOUYIN_BROWSER_PROFILE_ROOT") or "").strip()
    root = Path(configured).expanduser() if configured else _project_root() / "runtime" / "browser_profiles" / "douyin"
    return root / _credential_key(credential_id)


@contextmanager
def douyin_publish_execution_guard(*, credential_id: Any, command: Dict[str, Any], progress=None):
    """Serialize one Douyin account across worker threads/processes.

    The platform worker remains concurrent for Bilibili and WeChat Channels;
    only jobs sharing this Douyin credential wait for one another.
    """
    if fcntl is None:
        yield
        return

    key = _credential_key(credential_id)
    configured_lock_root = str(os.environ.get("DOUYIN_PUBLISH_LOCK_ROOT") or "").strip()
    lock_dir = (
        Path(configured_lock_root).expanduser()
        if configured_lock_root
        else _project_root() / "runtime" / "locks" / "douyin_publish"
    )
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{key}.lock"
    timeout_seconds = _int_env("DOUYIN_ACCOUNT_LOCK_TIMEOUT_SECONDS", 3600, 30, 7200)
    cooldown_seconds = _int_env("DOUYIN_PUBLISH_COOLDOWN_SECONDS", 180, 0, 3600)
    deadline = time.monotonic() + timeout_seconds
    owner = (os.getpid(), threading.get_ident())

    while True:
        active = _ACTIVE_CREDENTIAL_LOCKS.get(str(lock_path))
        if active and active.get("owner") != owner:
            if progress:
                progress("waiting_account_lock", 8, "同一抖音账号已有任务，正在串行等待")
            if time.monotonic() >= deadline:
                raise DouyinBrowserError("等待同一抖音账号的上一项发布任务结束超时")
            time.sleep(1)
            continue

        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if progress:
                        progress("waiting_account_lock", 8, "同一抖音账号已有任务，正在串行等待")
                    if time.monotonic() >= deadline:
                        raise DouyinBrowserError("等待同一抖音账号的上一项发布任务结束超时") from exc
                    time.sleep(1)

            handle.seek(0)
            raw_state = handle.read().strip()
            try:
                state = json.loads(raw_state) if raw_state else {}
            except (TypeError, ValueError):
                state = {}
            if bool(command.get("submit")) and cooldown_seconds:
                last_attempt = float(state.get("last_submit_attempt_epoch") or 0)
                remaining = cooldown_seconds - max(0, time.time() - last_attempt)
                while remaining > 0:
                    if progress:
                        progress(
                            "account_cooldown",
                            9,
                            f"抖音账号发布冷却中，约 {int(remaining) + 1} 秒后继续",
                        )
                    time.sleep(min(5, remaining))
                    remaining = cooldown_seconds - max(0, time.time() - last_attempt)

            _ACTIVE_CREDENTIAL_LOCKS[str(lock_path)] = {"owner": owner, "handle": handle}
            try:
                yield
            finally:
                if bool(command.get("submit")):
                    state["last_submit_attempt_epoch"] = time.time()
                state.update({
                    "last_owner_pid": os.getpid(),
                    "last_released_at": datetime.utcnow().isoformat() + "Z",
                })
                handle.seek(0)
                handle.truncate()
                json.dump(state, handle, ensure_ascii=False)
                handle.flush()
                _ACTIVE_CREDENTIAL_LOCKS.pop(str(lock_path), None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        return


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
    """Convert extension exports, storage_state, maps, or Cookie headers."""
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
        cookie["domain"] = str(cookie.get("domain") or ".douyin.com").strip()
        cookie["path"] = str(cookie.get("path") or "/")
        if "expirationDate" in raw and "expires" not in cookie:
            cookie["expires"] = raw["expirationDate"]
        if "sameSite" in cookie:
            same_site = str(cookie["sameSite"]).strip().lower()
            cookie["sameSite"] = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}.get(
                same_site,
                "Lax",
            )
        if cookie.get("expires") in (None, "", 0, -1):
            cookie.pop("expires", None)
        elif "expires" in cookie:
            try:
                cookie["expires"] = float(cookie["expires"])
            except (TypeError, ValueError):
                cookie.pop("expires", None)
        identity = (cookie["name"], cookie["domain"], cookie["path"])
        if identity in seen:
            continue
        seen.add(identity)
        cookies.append(cookie)
    return cookies


def _bounded_timeout_ms() -> int:
    try:
        seconds = int(os.environ.get("DOUYIN_BROWSER_TIMEOUT_SECONDS", "35") or 35)
    except (TypeError, ValueError):
        seconds = 35
    return max(10, min(seconds, 90)) * 1000


def _truthy_env(name: str, default: bool) -> bool:
    value = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _bounded_upload_timeout_ms() -> int:
    try:
        seconds = int(os.environ.get("DOUYIN_UPLOAD_TIMEOUT_SECONDS", "1200") or 1200)
    except (TypeError, ValueError):
        seconds = 1200
    return max(60, min(seconds, 3600)) * 1000


def _bounded_publish_confirmation_timeout_ms() -> int:
    try:
        seconds = int(os.environ.get("DOUYIN_PUBLISH_CONFIRM_TIMEOUT_SECONDS", "45") or 45)
    except (TypeError, ValueError):
        seconds = 45
    return max(20, min(seconds, 120)) * 1000


def _browser_launch_kwargs() -> Dict[str, Any]:
    """Build launch options, preferring Playwright's pinned Chromium.

    An external browser is used only when explicitly configured. Playwright's
    protocol is version-sensitive, so silently selecting a much newer system
    Chrome can make the process exit before a page is opened.
    """
    headless = _truthy_env("DOUYIN_BROWSER_HEADLESS", True)
    configured = str(os.environ.get("DOUYIN_BROWSER_EXECUTABLE_PATH") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise DouyinBrowserError(f"抖音浏览器路径不存在: {configured_path}")
        executable_path = str(configured_path)
    else:
        executable_path = ""

    kwargs: Dict[str, Any] = {"headless": headless}
    if executable_path:
        kwargs["executable_path"] = executable_path
    if headless:
        # Playwright 1.48 still requests --headless=old, while both current
        # Chrome and its pinned Chromium warn or abort on recent macOS builds.
        kwargs["headless"] = False
        kwargs["args"] = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"]
    return kwargs


def compose_douyin_description(title: str, description: str = "", tags: Any = None) -> str:
    """Build deterministic publish copy without logging or storing credentials."""
    clean_title = re.sub(r"\s+", " ", str(title or "").strip())[:100]
    clean_description = re.sub(r"[ \t]+", " ", str(description or "").strip())[:3000]
    if isinstance(tags, str):
        tag_items = re.split(r"[,，、\s]+", tags)
    elif isinstance(tags, (list, tuple, set)):
        tag_items = list(tags)
    else:
        tag_items = []
    normalized_tags = []
    seen = set()
    for item in tag_items:
        tag = re.sub(r"\s+", "", str(item or "").strip().lstrip("#"))[:20]
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized_tags.append(f"#{tag}")
        if len(normalized_tags) >= 10:
            break
    parts = [part for part in (clean_title, clean_description, " ".join(normalized_tags)) if part]
    return "\n".join(parts)[:4000]


def _has_visible_text(page: Any, text: str) -> bool:
    """Return true only when matching text is actually rendered to the user.

    Douyin's creator SPA keeps inactive login and verification components in
    the DOM. Checking the whole body text therefore produces false expired-
    cookie errors during a long video upload.
    """
    try:
        matches = page.get_by_text(text, exact=False)
        for index in range(min(matches.count(), 8)):
            if matches.nth(index).is_visible(timeout=300):
                return True
    except Exception:
        pass
    return False


def _has_visible_login_surface(page: Any) -> bool:
    """Detect an actual login form/dialog, not a harmless navigation link."""
    login_hints = ("扫码登录", "手机号登录", "验证码登录")
    selectors = (
        '[role="dialog"]', '[class*="login"]', '[class*="Login"]',
        'input[placeholder*="手机号"]', 'input[placeholder*="验证码"]',
    )
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 8)):
                item = matches.nth(index)
                if not item.is_visible(timeout=300):
                    continue
                if selector.startswith("input"):
                    return True
                rendered = str(item.inner_text(timeout=500) or "")
                if any(hint in rendered for hint in login_hints):
                    return True
        except Exception:
            continue
    return False


def _classify_page(page: Any) -> Dict[str, str]:
    url = str(getattr(page, "url", "") or "").lower()
    body_text = ""
    try:
        body_text = str(page.locator("body").inner_text(timeout=3000) or "")
    except Exception:
        pass

    # A real login URL is decisive. Text alone is not: hidden login dialogs
    # remain mounted on authenticated creator pages and used to interrupt
    # large uploads halfway through.
    if "/login" in url:
        return {"status": "auth_required", "message": "抖音 Cookie 已失效或未登录，请重新导出 Cookie"}

    verification_hints = ("安全验证", "短信验证", "请输入验证码", "滑块验证", "完成验证")
    if any(_has_visible_text(page, hint) for hint in verification_hints):
        return {"status": "verification_required", "message": "抖音要求人工完成安全验证，请处理后重新校验"}

    try:
        if page.locator('input[type="file"]').count() > 0:
            return {"status": "valid", "message": "登录态校验通过：已进入抖音创作者发布页"}
    except Exception:
        pass

    creator_hints = (
        "发布视频", "发布作品", "作品管理", "全部作品", "基础信息", "作品描述",
        "作品上传中，请勿关闭页面", "上传完成后将自动发布",
    )
    if "creator.douyin.com" in url and any(hint in body_text for hint in creator_hints):
        return {"status": "valid", "message": "登录态校验通过：已进入抖音创作者中心"}
    if _has_visible_login_surface(page):
        return {"status": "auth_required", "message": "抖音 Cookie 已失效或未登录，请重新导出 Cookie"}
    return {"status": "unknown", "message": "已打开抖音创作者中心，但无法确认发布页状态；Cookie 未被自动判为失效"}


def _wait_for_classified_page(page: Any, timeout_ms: int) -> Dict[str, str]:
    """Wait for the creator SPA to render a decisive login/upload state."""
    deadline = time.monotonic() + min(max(timeout_ms / 1000, 1), 12)
    state = _classify_page(page)
    while state["status"] == "unknown" and time.monotonic() < deadline:
        page.wait_for_timeout(500)
        state = _classify_page(page)
    return state


def _first_visible(page: Any, selectors: List[str]) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


def _first_existing(page: Any, selectors: List[str]) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count():
                return locator
        except Exception:
            continue
    return None


def _publish_button(page: Any) -> Any:
    try:
        exact = page.get_by_role("button", name="发布", exact=True).first
        if exact.count() and exact.is_visible(timeout=1000):
            return exact
    except Exception:
        pass
    return _first_visible(page, [
        'button:has-text("发布")',
        '[role="button"]:has-text("发布")',
        'button[class*="publish"]',
    ])


def _set_declaration(page: Any, declaration: str, deadline: float) -> str:
    normalized = str(declaration or "none").strip().lower()
    option_text = _DECLARATION_LABELS.get(normalized)
    if not option_text:
        raise DouyinBrowserError("不支持的抖音自主声明")

    trigger = None
    while time.monotonic() < deadline:
        try:
            placeholder = page.get_by_text("请选择自主声明", exact=True).first
            if placeholder.count() and placeholder.is_visible(timeout=1000):
                trigger = placeholder.locator("xpath=..")
                break
            selected = page.get_by_text(option_text, exact=True).first
            if selected.count() and selected.is_visible(timeout=1000):
                return normalized
        except Exception:
            pass
        page.wait_for_timeout(500)
    if trigger is None:
        raise DouyinBrowserError("未找到抖音自主声明控件，已停止发布")

    try:
        trigger.click(timeout=3000)
        option = page.get_by_text(option_text, exact=True).last.locator("xpath=ancestor::label[1]")
        if not option.count() or not option.is_visible(timeout=3000):
            raise DouyinBrowserError(f"未找到自主声明选项“{option_text}”")
        option.click(timeout=3000)
        radio = option.locator('input[type="radio"]').first
        page.wait_for_timeout(300)
        option_class = str(option.get_attribute("class") or "")
        if radio.count() and not radio.is_checked(timeout=1000) and "semi-radio-checked" not in option_class:
            raise DouyinBrowserError(f"自主声明“{option_text}”未选中")
        confirm = page.get_by_role("button", name="确定", exact=True).last
        if not confirm.count() or not confirm.is_visible(timeout=2000):
            raise DouyinBrowserError("未找到自主声明确认按钮")
        confirm.click(timeout=3000)
        for _ in range(8):
            page.wait_for_timeout(300)
            selected_text = page.get_by_text(option_text, exact=True).first
            if selected_text.count() and selected_text.is_visible(timeout=500):
                inside_option = selected_text.locator("xpath=ancestor::label[1]").count() > 0
                if not inside_option:
                    return normalized
        raise DouyinBrowserError(f"无法确认自主声明已设置为“{option_text}”")
    except DouyinBrowserError:
        raise
    except Exception as exc:
        raise DouyinBrowserError(f"设置抖音自主声明失败：{exc}") from exc


def _set_visibility(page: Any, visibility: str, deadline: float) -> str:
    normalized = str(visibility or "private").strip().lower()
    label_text = _VISIBILITY_LABELS.get(normalized)
    if not label_text:
        raise DouyinBrowserError("不支持的抖音可见范围")

    while time.monotonic() < deadline:
        try:
            text = page.get_by_text(label_text, exact=True).first
            label = text.locator("xpath=ancestor::label[1]")
            if label.count() and label.is_visible(timeout=1000):
                checkbox = label.locator('input[type="checkbox"]').first
                if not checkbox.count():
                    raise DouyinBrowserError(f"抖音“{label_text}”控件结构已变化")
                if not checkbox.is_checked(timeout=1000):
                    label.click(timeout=3000)
                    page.wait_for_timeout(300)
                if checkbox.is_checked(timeout=1000):
                    return normalized
        except DouyinBrowserError:
            raise
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise DouyinBrowserError(f"无法确认抖音可见范围已切换为“{label_text}”，已停止发布")


def _raise_for_page_state(page: Any) -> None:
    state = _classify_page(page)
    if state["status"] == "auth_required":
        raise DouyinAuthenticationRequired(state["message"])
    if state["status"] == "verification_required":
        raise DouyinVerificationRequired(state["message"])


def _compact_publish_text(value: Any) -> str:
    return re.sub(r"[\s|｜·:：,，。.!！?？_\-—]+", "", str(value or "")).lower()


def _published_title_is_visible(body_text: str, title: str) -> bool:
    """Match the editor title against the newest creator-center cards.

    Douyin truncates the dedicated title input and may shorten it again in the
    management card. Comparing the normalized first 16 characters keeps the
    topic-bearing second phrase while tolerating those two display truncations.
    """
    expected = _compact_publish_text(re.sub(r"\s+", " ", str(title or "")).strip()[:30])[:16]
    # The upload lab intentionally allows tiny titles such as ``zz``. They are
    # still useful end-to-end probes, and the manage-page lookup is already
    # scoped to the newest works list rather than arbitrary site text.
    return len(expected) >= 2 and expected in _compact_publish_text(body_text)


def _publish_page_error(body_text: str) -> Optional[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(body_text or "").splitlines()]
    hints = (
        "发布失败", "投稿失败", "提交失败", "上传失败", "发布过于频繁",
        "请完善作品信息", "请填写作品", "内容不符合", "网络异常",
    )
    return next((line[:240] for line in lines if line and any(hint in line for hint in hints)), None)


def _video_upload_state(body_text: str) -> Dict[str, Any]:
    """Classify Douyin's large-file upload panel from rendered text."""
    text = str(body_text or "")
    compact = re.sub(r"\s+", " ", text)
    in_progress_hints = (
        "作品上传中，请勿关闭页面",
        "上传过程中请不要删除/移动文件",
        "上传完成后将自动发布",
        "取消上传",
        "剩余时间",
    )
    in_progress = any(hint in compact for hint in in_progress_hints)
    progress = None
    if in_progress:
        candidates = []
        for line in text.splitlines():
            if not any(hint in line for hint in ("上传", "剩余", "%")):
                continue
            candidates.extend(int(value) for value in re.findall(r"(?<!\d)(\d{1,3})\s*%", line))
        valid = [value for value in candidates if 0 <= value <= 100]
        if valid:
            progress = max(valid)
    completed = not in_progress and any(
        hint in compact for hint in ("上传完成", "上传成功", "视频上传成功", "已完成上传")
    )
    return {"in_progress": in_progress, "completed": completed, "progress": progress}


def _wait_for_video_upload_complete(
    page: Any,
    timeout_ms: int,
    report: Callable[[str, int, str], None],
) -> None:
    """Keep the upload page alive until a large file has fully transferred."""
    deadline = time.monotonic() + timeout_ms / 1000
    stable_ready_checks = 0
    saw_upload_progress = False
    while time.monotonic() < deadline:
        _raise_for_page_state(page)
        try:
            body_text = str(page.locator("body").inner_text(timeout=3000) or "")
        except Exception:
            body_text = ""
        error = _publish_page_error(body_text)
        if error:
            raise DouyinBrowserError(f"抖音视频上传失败：{error}")

        state = _video_upload_state(body_text)
        if state["in_progress"]:
            saw_upload_progress = True
            stable_ready_checks = 0
            raw_progress = state.get("progress")
            displayed = f" {raw_progress}%" if raw_progress is not None else ""
            mapped_progress = 35 + int(min(max(raw_progress or 0, 0), 100) * 0.35)
            report("uploading_video", mapped_progress, f"抖音视频仍在上传{displayed}，保持页面打开")
            page.wait_for_timeout(2000)
            continue

        publish_button = _publish_button(page)
        button_ready = False
        if publish_button is not None:
            try:
                button_ready = publish_button.is_enabled(timeout=1000)
            except Exception:
                button_ready = False
        if button_ready:
            # The progress panel can briefly disappear during a render. Require
            # several stable checks, especially after observing a real upload.
            stable_ready_checks += 1
            required_checks = 3 if saw_upload_progress else 4
            if state["completed"] or stable_ready_checks >= required_checks:
                report("upload_complete", 72, "抖音视频上传完成，发布按钮已稳定可用")
                return
        else:
            stable_ready_checks = 0
        page.wait_for_timeout(1500)

    raise DouyinBrowserError("等待抖音大文件上传完成超时；已保持页面打开，未点击发布")


def _click_publish_confirmation_dialog(page: Any) -> bool:
    """Handle Douyin's optional, explicit second publish confirmation."""
    try:
        dialogs = page.locator(
            '[role="dialog"]:visible, .semi-modal:visible, [class*="modal"]:visible, [class*="Modal"]:visible'
        )
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            text = str(dialog.inner_text(timeout=1000) or "")
            if "发布" not in text or not any(hint in text for hint in ("确认", "继续")):
                continue
            LOGGER.info("Detected Douyin publish confirmation dialog: %s", re.sub(r"\s+", " ", text)[:300])
            # Generic labels are safe here because they are scoped to a visible
            # dialog that explicitly asks to confirm/continue publication.
            for label in ("确认发布", "继续发布", "确认", "发布", "确定"):
                button = dialog.get_by_role("button", name=label, exact=True)
                if button.count() == 1 and button.is_visible(timeout=500) and button.is_enabled(timeout=500):
                    button.click(timeout=3000)
                    LOGGER.info("Confirmed Douyin publish dialog with button: %s", label)
                    return True
    except Exception as exc:
        LOGGER.warning("Unable to handle Douyin publish confirmation dialog: %s", exc)
        return False
    return False


def _confirm_published_work(page: Any, title: str, timeout_ms: int) -> Dict[str, Any]:
    """Require a creator-center work card before reporting publish success."""
    settle_deadline = time.monotonic() + min(15, timeout_ms / 1000)
    while time.monotonic() < settle_deadline:
        _raise_for_page_state(page)
        _click_publish_confirmation_dialog(page)
        try:
            body_text = str(page.locator("body").inner_text(timeout=2000) or "")
        except Exception:
            body_text = ""
        error = _publish_page_error(body_text)
        if error:
            raise DouyinBrowserError(f"抖音拒绝发布：{error}")
        page.wait_for_timeout(1000)

    try:
        page.goto(DOUYIN_MANAGE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        raise DouyinBrowserError(f"已点击发布，但无法打开作品管理页回查：{exc}") from exc

    verify_deadline = time.monotonic() + timeout_ms / 1000
    reloaded = False
    while time.monotonic() < verify_deadline:
        _raise_for_page_state(page)
        try:
            body_text = str(page.locator("body").inner_text(timeout=3000) or "")
        except Exception:
            body_text = ""
        error = _publish_page_error(body_text)
        if error:
            raise DouyinBrowserError(f"抖音作品管理页返回异常：{error}")
        if _published_title_is_visible(body_text, title):
            return {
                "publish_status": "visible",
                "submitted": True,
                "confirmed": True,
                "confirmation_source": "creator_manage_title",
                "message": "发布已提交，并已在抖音作品管理页回查到作品",
            }
        if not reloaded and time.monotonic() + 12 >= verify_deadline:
            try:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
            reloaded = True
        page.wait_for_timeout(1500)

    raise DouyinBrowserError(
        "已点击发布，但在抖音作品管理页未找到对应作品；本次不能判定发布成功，请检查页面校验、草稿或审核状态后重试"
    )


def publish_douyin_video(
    *,
    cookies: List[Dict[str, Any]],
    video_path: str,
    title: str,
    description: str = "",
    tags: Any = None,
    visibility: str = "private",
    declaration: str = "none",
    submit: bool = False,
    progress: Optional[Callable[[str, int, str], None]] = None,
    credential_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload and fill one creator-center video, optionally clicking publish.

    A non-submitting run proves that the file reached the editor and the copy
    could be filled. The browser is then closed, so it is reported as
    ``prepared`` rather than as a draft saved on Douyin.
    """
    path = Path(str(video_path or "")).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise DouyinBrowserError("测试视频不存在或为空")
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise DouyinBrowserError("抖音上传测试仅支持 MP4、MOV、M4V")
    if not cookies:
        raise DouyinAuthenticationRequired("抖音 Cookie 为空")

    copy_text = compose_douyin_description(title, description, tags)
    if not copy_text:
        raise DouyinBrowserError("抖音发布文案不能为空")
    normalized_visibility = str(visibility or "private").strip().lower()
    if normalized_visibility not in _VISIBILITY_LABELS:
        raise DouyinBrowserError("不支持的抖音可见范围")
    normalized_declaration = str(declaration or "none").strip().lower()
    if normalized_declaration not in _DECLARATION_LABELS:
        raise DouyinBrowserError("不支持的抖音自主声明")

    def report(step: str, percent: int, message: str) -> None:
        if progress:
            progress(step, percent, message)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DouyinBrowserError("缺少 playwright 依赖，无法上传抖音视频") from exc

    timeout_ms = _bounded_timeout_ms()
    upload_timeout_ms = _bounded_upload_timeout_ms()
    report("opening_creator", 15, "正在打开抖音创作者中心")
    try:
        with sync_playwright() as playwright:
            launch_kwargs = _browser_launch_kwargs()
            browser = None
            context = None
            try:
                use_persistent_profile = bool(credential_id) and _truthy_env(
                    "DOUYIN_BROWSER_PERSISTENT_PROFILE", True
                )
                if use_persistent_profile:
                    profile_dir = _douyin_profile_dir(credential_id)
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    context = playwright.chromium.launch_persistent_context(
                        str(profile_dir),
                        **launch_kwargs,
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                    )
                else:
                    browser = playwright.chromium.launch(**launch_kwargs)
                    context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(DOUYIN_UPLOAD_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                initial_state = _wait_for_classified_page(page, timeout_ms)
                if initial_state["status"] == "auth_required":
                    raise DouyinAuthenticationRequired(initial_state["message"])
                if initial_state["status"] == "verification_required":
                    raise DouyinVerificationRequired(initial_state["message"])

                # File inputs are commonly hidden behind a styled button;
                # Playwright can still set files on the underlying element.
                file_input = _first_existing(page, ['input[type="file"][accept*="video"]', 'input[type="file"]'])
                if file_input is None:
                    raise DouyinBrowserError("未找到抖音视频上传控件，页面结构可能已变化")
                report("uploading_video", 35, "正在上传视频文件")
                file_input.set_input_files(str(path))

                deadline = time.monotonic() + upload_timeout_ms / 1000
                editor = None
                while time.monotonic() < deadline:
                    _raise_for_page_state(page)
                    editor = _first_visible(page, [
                        '[contenteditable="true"]',
                        'textarea[placeholder*="描述"]',
                        'textarea[placeholder*="作品"]',
                    ])
                    if editor is not None:
                        break
                    page.wait_for_timeout(1500)
                else:
                    raise DouyinBrowserError("等待抖音视频进入编辑器超时")

                report("filling_copy", 75, "视频已上传，正在填写标题和话题")
                title_input = _first_visible(page, [
                    'input[placeholder*="标题"]',
                    'input[class*="title"]',
                ])
                if title_input is not None:
                    title_input.fill(re.sub(r"\s+", " ", str(title or "").strip())[:30])

                editor_text = compose_douyin_description(
                    "" if title_input is not None else title,
                    description,
                    tags,
                )
                editor.fill(editor_text)

                if normalized_declaration == "none":
                    report("setting_declaration", 80, "本次不添加作品自主声明")
                    selected_declaration = "none"
                else:
                    report("setting_declaration", 80, "正在设置作品自主声明")
                    selected_declaration = _set_declaration(page, normalized_declaration, deadline)
                report("setting_visibility", 82, "正在设置作品可见范围")
                selected_visibility = _set_visibility(page, normalized_visibility, deadline)

                _wait_for_video_upload_complete(page, upload_timeout_ms, report)

                if not submit:
                    report("prepared", 100, "上传和文案填写校验通过，未点击发布")
                    return {
                        "publish_status": "prepared",
                        "submitted": False,
                        "visibility": selected_visibility,
                        "declaration": selected_declaration,
                        "message": "视频上传和文案填写已完成；测试模式未点击发布",
                    }

                report("submitting", 90, "正在点击发布，请勿重复投递")
                publish_button = _publish_button(page)
                if publish_button is None or not publish_button.is_enabled(timeout=2000):
                    raise DouyinBrowserError("发布按钮不可用，请检查页面必填项")
                publish_button.click()
                try:
                    confirmation = _confirm_published_work(
                        page,
                        str(title or ""),
                        _bounded_publish_confirmation_timeout_ms(),
                    )
                except DouyinAuthenticationRequired as exc:
                    raise DouyinSubmissionPendingVerification(
                        "发布按钮已点击，但回查时登录态失效；已停止自动重投，请在作品管理页人工确认",
                        credential_expired=True,
                    ) from exc
                except DouyinVerificationRequired as exc:
                    raise DouyinSubmissionPendingVerification(
                        "发布按钮已点击，但回查时触发安全验证；已停止自动重投，请人工确认作品状态",
                        credential_expired=False,
                    ) from exc
                report("visible", 100, "已在抖音作品管理页确认作品")
                return {
                    **confirmation,
                    "visibility": selected_visibility,
                    "declaration": selected_declaration,
                }
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    except (DouyinAuthenticationRequired, DouyinVerificationRequired, DouyinBrowserError):
        raise
    except Exception as exc:
        raise DouyinBrowserError(f"抖音浏览器上传失败：{exc}") from exc


def check_douyin_session(payload: str) -> Dict[str, str]:
    cookies = playwright_cookies_from_payload(payload)
    cookie_names = {item["name"].lower() for item in cookies}
    if not cookies or not cookie_names.intersection(_AUTH_COOKIE_NAMES):
        return {"status": "auth_required", "message": "抖音 Cookie 缺少 sessionid/sessionid_ss 登录态字段"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DouyinBrowserError("缺少 playwright 依赖，无法校验抖音登录态") from exc

    timeout_ms = _bounded_timeout_ms()
    try:
        with sync_playwright() as playwright:
            launch_kwargs = _browser_launch_kwargs()
            browser = playwright.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(DOUYIN_UPLOAD_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                return _wait_for_classified_page(page, timeout_ms)
            finally:
                browser.close()
    except DouyinBrowserError:
        raise
    except Exception as exc:
        raise DouyinBrowserError(f"抖音登录态校验失败：{exc}") from exc
