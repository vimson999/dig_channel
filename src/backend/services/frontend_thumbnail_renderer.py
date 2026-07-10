import base64
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


class FrontendThumbnailRenderError(RuntimeError):
    pass


class FrontendThumbnailRenderer:
    """Render publisher covers with the browser canvas renderer used by the UI."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root or self._project_root()).resolve()
        self.renderer_js = self.project_root / "src" / "frontend" / "js" / "publisher" / "renderer.js"

    def render_landscape_cover(
        self,
        *,
        output_path: str,
        main_title: str,
        sub_title: str = "",
        date_text: str = "",
        tag_text: str = "",
        mode: str = "screenshot",
        custom_bg_path: Optional[str] = None,
        transform: Optional[Dict[str, Any]] = None,
        quality: float = 0.95,
    ) -> str:
        if not self.renderer_js.exists():
            raise FrontendThumbnailRenderError(f"前端封面渲染器不存在: {self.renderer_js}")

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            raise FrontendThumbnailRenderError("缺少 playwright 依赖，无法使用前端封面渲染器") from exc

        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        image_data_url = self._image_data_url(custom_bg_path) if custom_bg_path else ""
        payload = {
            "imageDataUrl": image_data_url,
            "quality": max(0.1, min(1.0, float(quality or 0.95))),
            "config": {
                "mainTitle": str(main_title or ""),
                "subTitle": str(sub_title or ""),
                "dateText": str(date_text or ""),
                "tagText": str(tag_text or ""),
                "mode": mode or "screenshot",
                "transform": transform or {"x": 0, "y": 0, "scale": 1},
            },
        }

        timeout_ms = int(os.getenv("REPORT_COVER_RENDER_TIMEOUT_MS", "30000"))
        with sync_playwright() as playwright:
            browser = self._launch_browser(playwright)
            try:
                page = browser.new_page(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=1,
                )
                page.set_default_timeout(timeout_ms)
                page.set_content(self._html_shell(), wait_until="load")
                page.add_script_tag(path=str(self.renderer_js))
                data_url = page.evaluate(self._render_script(), payload)
            finally:
                browser.close()

        match = re.match(r"^data:image/jpeg;base64,(.+)$", data_url or "")
        if not match:
            raise FrontendThumbnailRenderError("前端封面渲染器未返回有效 JPEG")
        output.write_bytes(base64.b64decode(match.group(1)))
        return str(output)

    def render_workbench_thumbnail(
        self,
        *,
        output_path: str,
        format_name: str = "vertical",
        main_title: str,
        sub_title: str = "",
        footer_text: str = "听见资本",
        institution: str = "深度研报",
        emotion: str = "bull",
        image_path: Optional[str] = None,
        image_offset_x: float = 0,
        image_offset_y: float = 0,
        image_scale: float = 1,
        image_anchor: str = "",
        title_max_lines: Optional[int] = None,
        title_min_size: Optional[int] = None,
        subtitle_max_lines: Optional[int] = None,
        subtitle_min_size: Optional[int] = None,
        show_guides: bool = False,
        show_footer: bool = True,
        quality: float = 0.95,
        expected_size: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Render through an isolated process so a wedged browser cannot block a worker."""
        render_kwargs = {
            "output_path": output_path,
            "format_name": format_name,
            "main_title": main_title,
            "sub_title": sub_title,
            "footer_text": footer_text,
            "institution": institution,
            "emotion": emotion,
            "image_path": image_path,
            "image_offset_x": image_offset_x,
            "image_offset_y": image_offset_y,
            "image_scale": image_scale,
            "image_anchor": image_anchor,
            "title_max_lines": title_max_lines,
            "title_min_size": title_min_size,
            "subtitle_max_lines": subtitle_max_lines,
            "subtitle_min_size": subtitle_min_size,
            "show_guides": show_guides,
            "show_footer": show_footer,
            "quality": quality,
            "expected_size": list(expected_size) if expected_size else None,
        }
        if os.getenv("REPORT_COVER_RENDER_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return self._render_workbench_thumbnail_direct(**render_kwargs)

        worker_path = Path(__file__).with_name("frontend_thumbnail_render_worker.py")
        hard_timeout_ms = max(1000, int(os.getenv("REPORT_COVER_HARD_TIMEOUT_MS", "70000")))
        payload = json.dumps(
            {
                "project_root": str(self.project_root),
                "render_kwargs": render_kwargs,
            },
            ensure_ascii=False,
        )
        process = subprocess.Popen(
            [sys.executable, str(worker_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(payload, timeout=hard_timeout_ms / 1000)
        except subprocess.TimeoutExpired as exc:
            self._terminate_render_process(process)
            stdout, stderr = process.communicate()
            detail = (stderr or stdout or "no child output").strip()[-1000:]
            raise FrontendThumbnailRenderError(
                f"封面工具台渲染超过硬超时 {hard_timeout_ms}ms: {detail}"
            ) from exc

        if process.returncode != 0:
            detail = (stderr or stdout or f"exit={process.returncode}").strip()[-2000:]
            raise FrontendThumbnailRenderError(f"封面工具台子进程失败: {detail}")

        output = Path(output_path).expanduser().resolve()
        if not output.is_file():
            raise FrontendThumbnailRenderError(f"封面工具台子进程未生成文件: {output}")
        return str(output)

    def _render_workbench_thumbnail_direct(
        self,
        *,
        output_path: str,
        format_name: str = "vertical",
        main_title: str,
        sub_title: str = "",
        footer_text: str = "听见资本",
        institution: str = "深度研报",
        emotion: str = "bull",
        image_path: Optional[str] = None,
        image_offset_x: float = 0,
        image_offset_y: float = 0,
        image_scale: float = 1,
        image_anchor: str = "",
        title_max_lines: Optional[int] = None,
        title_min_size: Optional[int] = None,
        subtitle_max_lines: Optional[int] = None,
        subtitle_min_size: Optional[int] = None,
        show_guides: bool = False,
        show_footer: bool = True,
        quality: float = 0.95,
        expected_size: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Render a cover/background with thumbnail-workbench.html's canvas logic."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            raise FrontendThumbnailRenderError("缺少 playwright 依赖，无法使用封面工具台渲染器") from exc

        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "format": format_name or "vertical",
            "showGuides": bool(show_guides),
            "showFooter": bool(show_footer),
            "inst": institution or "深度研报",
            "mainTitle": str(main_title or ""),
            "subTitle": str(sub_title or ""),
            "footerText": str(footer_text or "听见资本"),
            "emotion": emotion if emotion in {"pro", "macro", "bull", "bear"} else "bull",
            "imageDataUrl": self._image_data_url(image_path) if image_path else "",
            "imageOffsetX": image_offset_x or 0,
            "imageOffsetY": image_offset_y or 0,
            "imageScale": image_scale or 1,
            "imageAnchor": image_anchor or "",
            "titleMaxLines": title_max_lines,
            "titleMinSize": title_min_size,
            "subtitleMaxLines": subtitle_max_lines,
            "subtitleMinSize": subtitle_min_size,
            "mimeType": "image/jpeg",
            "quality": max(0.1, min(1.0, float(quality or 0.95))),
        }

        timeout_ms = int(os.getenv("REPORT_COVER_RENDER_TIMEOUT_MS", "30000"))
        with sync_playwright() as playwright:
            browser = self._launch_browser(playwright)
            context = None
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    device_scale_factor=1,
                )
                context.set_default_timeout(timeout_ms)
                context.route("**/*", _route_local_render_request)
                data_url = self._render_workbench_with_context(context, payload)
            finally:
                if context is not None:
                    context.close()
                browser.close()

        self._write_data_url(data_url, output, expected_size=expected_size)
        return str(output)

    @staticmethod
    def _terminate_render_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            process.terminate()
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()

    def _launch_browser(self, playwright):
        kwargs: Dict[str, Any] = {"headless": True}
        chrome_path = (
            os.getenv("REPORT_COVER_RENDER_CHROME_PATH")
            or os.getenv("PUBLISHER_COVER_CHROME_PATH")
            or os.getenv("DOUBAO_BROWSER_TTS_CHROME_PATH")
            or os.getenv("REPORT_PDF_GEMINI_CHROME_PATH")
            or os.getenv("GEMINI_BROWSER_CHROME_PATH")
            or self._default_chrome_path()
            or ""
        ).strip()
        if chrome_path:
            expanded = str(Path(chrome_path).expanduser())
            if not Path(expanded).exists():
                raise FrontendThumbnailRenderError(f"封面渲染 Chrome 路径不存在: {expanded}")
            kwargs["executable_path"] = expanded
            # Playwright 1.48 still sends --headless=old for branded Chrome.
            # Recent Chrome versions removed old headless, so launch headed and
            # pass the modern headless flag ourselves.
            kwargs["headless"] = False
            kwargs["args"] = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"]

        channel = os.getenv("REPORT_COVER_RENDER_CHROME_CHANNEL", "").strip()
        if channel and "executable_path" not in kwargs:
            kwargs["channel"] = channel

        return playwright.chromium.launch(**kwargs)

    def _default_chrome_path(self) -> str:
        for candidate in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ):
            if Path(candidate).exists():
                return candidate
        return ""

    def _image_data_url(self, image_path: Optional[str]) -> str:
        if not image_path:
            return ""
        path = Path(image_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FrontendThumbnailRenderError(f"封面截图不存在: {path}")
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _render_workbench_with_context(self, context, payload: Dict[str, Any]) -> str:
        page = context.new_page()
        try:
            last_error: Optional[Exception] = None
            for url in self._frontend_page_urls("thumbnail-workbench.html"):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=int(os.getenv("REPORT_COVER_PAGE_TIMEOUT_MS", "25000")))
                    page.wait_for_function("() => typeof window.renderWorkflowThumbnail === 'function'")
                    result = page.evaluate("(payload) => window.renderWorkflowThumbnail(payload)", payload)
                    return result.get("dataUrl") if isinstance(result, dict) else ""
                except Exception as exc:
                    last_error = exc
            raise FrontendThumbnailRenderError(f"无法调用封面工具台页面: {last_error}")
        finally:
            page.close()

    def _frontend_page_urls(self, page_name: str) -> List[str]:
        urls: List[str] = []
        local_path = self.project_root / "src" / "frontend" / page_name
        if local_path.exists():
            urls.append(local_path.resolve().as_uri())
        base_url = (
            os.getenv("REPORT_COVER_FRONTEND_BASE_URL")
            or os.getenv("MP3_VIDEO_FRONTEND_BASE_URL")
            or os.getenv("FRONTEND_BASE_URL")
            or "http://localhost:8888"
        ).strip()
        if base_url and base_url.lower() not in {"file", "file://"}:
            urls.append(f"{base_url.rstrip('/')}/{page_name.lstrip('/')}")
        return urls

    def _write_data_url(self, data_url: str, output: Path, *, expected_size: Optional[Tuple[int, int]] = None) -> None:
        match = re.match(r"^data:image/(?:jpeg|jpg|png);base64,(.+)$", data_url or "")
        if not match:
            raise FrontendThumbnailRenderError("封面工具台未返回有效图片")
        output.write_bytes(base64.b64decode(match.group(1)))
        if expected_size:
            try:
                from PIL import Image

                with Image.open(output) as image:
                    size = image.size
            except Exception as exc:
                raise FrontendThumbnailRenderError(f"封面工具台输出无法读取: {output}") from exc
            if size != expected_size:
                raise FrontendThumbnailRenderError(
                    f"封面工具台输出尺寸异常: {size[0]}x{size[1]}，期望 {expected_size[0]}x{expected_size[1]}"
                )

    def _html_shell(self) -> str:
        return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body { margin: 0; padding: 0; background: #000; }
    canvas { display: block; }
  </style>
</head>
<body>
  <canvas id="cover" width="1920" height="1080"></canvas>
</body>
</html>"""

    def _render_script(self) -> str:
        return """async ({ imageDataUrl, config, quality }) => {
  const canvas = document.getElementById('cover');
  const ctx = canvas.getContext('2d');
  const custom = new Image();
  custom.crossOrigin = 'anonymous';
  const bg = new Image();
  const loadImage = (img, src) => new Promise((resolve, reject) => {
    if (!src) return resolve();
    img.onload = () => resolve();
    img.onerror = () => reject(new Error('image load failed'));
    img.src = src;
  });
  await loadImage(custom, imageDataUrl);
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  window.CanvasRenderer.drawCover(ctx, canvas, { custom, bg }, config);
  return canvas.toDataURL('image/jpeg', quality || 0.95);
}"""

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]


frontend_thumbnail_renderer = FrontendThumbnailRenderer()


def _route_local_render_request(route):
    url = route.request.url
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        route.abort()
        return
    route.continue_()
