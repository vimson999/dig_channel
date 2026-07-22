"""Versioned visual-scheme contracts for report storyboard and image generation.

A scheme is selected once by the parent workflow and then propagated through
storyboard generation, image retries and video rendering.  Keep contracts
immutable once published: changing a contract requires a new profile version.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Iterable


DEFAULT_VISUAL_SCHEME_KEY = "stick_figure"
DEFAULT_STORYBOARD_GEMINI_URL = "https://gemini.google.com/gem/293c6e0735aa"
DEFAULT_DORAEMON_STORYBOARD_GEMINI_URL = "https://gemini.google.com/gem/f9fb1e11c799"
DEFAULT_IMAGE_GEMINI_URL = "https://gemini.google.com/app"
_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class ReportVisualScheme:
    key: str
    name: str
    description: str
    storyboard_gemini_url: str
    image_gemini_url: str
    image_contract: str
    image_profile_version: str
    configured: bool = True

    def public_payload(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "image_profile": self.image_profile_version,
            "configured": bool(self.configured and self.storyboard_gemini_url),
        }


def _builtin_schemes() -> list[ReportVisualScheme]:
    doraemon_url = str(
        os.getenv("REPORT_DORAEMON_STORYBOARD_GEMINI_URL")
        or DEFAULT_DORAEMON_STORYBOARD_GEMINI_URL
    ).strip()
    return [
        ReportVisualScheme(
            key=DEFAULT_VISUAL_SCHEME_KEY,
            name="白板火柴人",
            description="黑白极简白板火柴人、少量点缀色、中文标签、16:9",
            storyboard_gemini_url=str(
                os.getenv("REPORT_STICK_FIGURE_STORYBOARD_GEMINI_URL")
                or DEFAULT_STORYBOARD_GEMINI_URL
            ).strip(),
            image_gemini_url=str(
                os.getenv("REPORT_STICK_FIGURE_IMAGE_GEMINI_URL")
                or DEFAULT_IMAGE_GEMINI_URL
            ).strip(),
            image_contract="whiteboard_stick_figure_zh",
            image_profile_version="whiteboard_stick_figure_zh_v1",
        ),
        ReportVisualScheme(
            key="doraemon",
            name="哆啦A梦复古二次元",
            description="高频切镜、经典复古日漫角色与神奇道具、明亮配色、中文标签、16:9",
            storyboard_gemini_url=doraemon_url,
            image_gemini_url=str(
                os.getenv("REPORT_DORAEMON_IMAGE_GEMINI_URL")
                or DEFAULT_IMAGE_GEMINI_URL
            ).strip(),
            image_contract="doraemon_retro_anime_zh",
            image_profile_version="doraemon_retro_anime_zh_16_9_v1",
            configured=bool(doraemon_url),
        ),
    ]


def _configured_schemes() -> Iterable[ReportVisualScheme]:
    """Load optional extra schemes from a deployment-level JSON registry.

    REPORT_VISUAL_SCHEMES_JSON is configuration, not task data.  It accepts a
    compact array of objects with key/name/storyboard_gemini_url and one of the
    supported image contracts.  Invalid entries are ignored atomically.
    """

    raw = str(os.getenv("REPORT_VISUAL_SCHEMES_JSON") or "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()
        contract = str(row.get("image_contract") or "gem_guided_zh").strip()
        storyboard_url = str(row.get("storyboard_gemini_url") or "").strip()
        if not _KEY_RE.fullmatch(key) or contract not in {
            "whiteboard_stick_figure_zh",
            "doraemon_retro_anime_zh",
            "gem_guided_zh",
        }:
            continue
        if not storyboard_url.startswith(("https://", "http://")):
            continue
        profile = str(row.get("image_profile_version") or f"{contract}_v1").strip()[:80]
        result.append(ReportVisualScheme(
            key=key,
            name=str(row.get("name") or key).strip()[:80],
            description=str(row.get("description") or row.get("name") or key).strip()[:300],
            storyboard_gemini_url=storyboard_url[:600],
            image_gemini_url=str(row.get("image_gemini_url") or DEFAULT_IMAGE_GEMINI_URL).strip()[:600],
            image_contract=contract,
            image_profile_version=profile,
        ))
    return result


def visual_schemes() -> tuple[ReportVisualScheme, ...]:
    by_key = {scheme.key: scheme for scheme in _builtin_schemes()}
    for scheme in _configured_schemes():
        by_key[scheme.key] = scheme
    default = by_key.pop(DEFAULT_VISUAL_SCHEME_KEY)
    return (default, *sorted(by_key.values(), key=lambda item: item.name))


def visual_scheme(key: str | None = None, *, require_configured: bool = True) -> ReportVisualScheme:
    normalized = str(key or DEFAULT_VISUAL_SCHEME_KEY).strip().lower()
    scheme = next((item for item in visual_schemes() if item.key == normalized), None)
    if not scheme:
        raise ValueError(f"未知视觉方案：{normalized}")
    if require_configured and (not scheme.configured or not scheme.storyboard_gemini_url):
        raise ValueError(f"视觉方案“{scheme.name}”尚未配置分镜 Gem 地址")
    return scheme


def visual_scheme_payloads() -> list[dict]:
    return [scheme.public_payload() for scheme in visual_schemes()]


def scene_image_prompt_for_scheme(
    visual_prompt: str,
    script_text: str,
    *,
    scheme_key: str = DEFAULT_VISUAL_SCHEME_KEY,
) -> str:
    scheme = visual_scheme(scheme_key, require_configured=False)
    visual_prompt = str(visual_prompt or "").strip()
    script_text = str(script_text or "").strip()
    if not visual_prompt:
        raise ValueError("分镜图片提示词不能为空")
    if scheme.image_contract == "whiteboard_stick_figure_zh":
        suffix = (
            "Whiteboard animation style, simple black line art on pure white background, "
            "hand-drawn doodle, stick figure, minimalist, no shading, clean and sharp. "
            "Use minimal spot color accents (e.g., a single touch of red, green, or yellow) "
            "only for emphasizing key data, directional arrows, or crucial elements. --ar 16:9"
        )
        contract = """- Preserve the subject, action, visual metaphor, composition, and key data from the original storyboard semantics.
- Ignore conflicting style words such as photorealistic, cinematic, 3D render, volumetric lighting, realistic texture, or 8k realism.
- Render the scene as a Chinese self-media whiteboard animation with simple hand-drawn stick figures and clear visual storytelling.
- Every visible text label in the image must be concise Simplified Chinese. Derive labels only from the Chinese narration context.
- Keep the background pure white, lines predominantly black, and use only one small spot color where emphasis is necessary."""
    elif scheme.image_contract == "doraemon_retro_anime_zh":
        suffix = (
            "Classic Doraemon anime style, art style of Fujiko F. Fujio, retro 1990s "
            "Japanese 2D animation, cel shading, bright and nostalgic primary colors, "
            "cute and round character designs, clear bold outlines, simple flat coloring, "
            "whimsical and imaginative atmosphere. --ar 16:9"
        )
        contract = """- Preserve every subject, action, business fact, data point, visual metaphor, prop, composition, and Chinese label from the original storyboard scene.
- Use expressive retro Japanese anime characters: a shocked bespectacled boy in a T-shirt and shorts, a cute round robotic cat with round hands, and whimsical science-fiction gadgets whenever the original storyboard calls for them.
- Favor vivid visual metaphors and exaggerated comic reactions; never replace them with stick figures, whiteboard diagrams, abstract dashboards, photorealism, 3D rendering, or realistic textures.
- Every visible text label must be concise Simplified Chinese. Do not render English labels, English UI text, subtitles, prompt text, or explanatory paragraphs.
- Use bright nostalgic primary colors, clean bold outlines, simple flat cel coloring, a playful 1990s Japanese animation composition, and a clear 16:9 landscape frame.
- Keep recurring characters, proportions, costumes, palette, and line style consistent across scenes."""
    else:
        suffix = "Create one clean 16:9 landscape storyboard image."
        contract = """- Follow the original Gem prompt's character design, art direction, composition, actions, props, color palette, and visual metaphor exactly.
- Do not convert the scene to a whiteboard, stick-figure, photorealistic, 3D, or any other unrelated style.
- Every visible text label in the image must be concise Simplified Chinese and must agree with the Chinese narration context.
- Keep recurring characters visually consistent across scenes."""
    return f"""Create exactly one image for this storyboard scene.

[Original storyboard scene semantics and style]
{visual_prompt}

[Chinese narration context]
{script_text}

[Mandatory rendering contract]
{contract}
- Produce one frame with no border, watermark, UI chrome, or explanatory paragraph.

{suffix}"""
