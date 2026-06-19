import base64
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


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
