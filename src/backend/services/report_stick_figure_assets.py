import hashlib
from pathlib import Path

from services.report_visual_scheme import (
    DEFAULT_IMAGE_GEMINI_URL,
    DEFAULT_VISUAL_SCHEME_KEY,
    scene_image_prompt_for_scheme,
    visual_scheme,
)


FEATURE_NAME = "report_stick_figure_image"
QUEUE_NAME = "report_pdf_gemini"
IMAGE_STYLE_PROFILE_VERSION = "whiteboard_stick_figure_zh_v1"
IMAGE_STYLE_DESCRIPTION = "黑白极简白板火柴人、少量点缀色、中文标签、16:9"
IMAGE_STYLE_SUFFIX = (
    "Whiteboard animation style, simple black line art on pure white background, "
    "hand-drawn doodle, stick figure, minimalist, no shading, clean and sharp. "
    "Use minimal spot color accents (e.g., a single touch of red, green, or yellow) "
    "only for emphasizing key data, directional arrows, or crucial elements. --ar 16:9"
)


def image_gemini_url(visual_scheme_key: str = DEFAULT_VISUAL_SCHEME_KEY) -> str:
    return visual_scheme(visual_scheme_key, require_configured=False).image_gemini_url or DEFAULT_IMAGE_GEMINI_URL


def scene_image_prompt(
    visual_prompt: str,
    script_text: str,
    visual_scheme_key: str = DEFAULT_VISUAL_SCHEME_KEY,
) -> str:
    """Bind a Gem scene description to its selected visual-scheme contract."""
    return scene_image_prompt_for_scheme(
        visual_prompt,
        script_text,
        scheme_key=visual_scheme_key,
    )


def scene_image_prompt_hash(
    visual_prompt: str,
    script_text: str,
    visual_scheme_key: str = DEFAULT_VISUAL_SCHEME_KEY,
) -> str:
    scheme = visual_scheme(visual_scheme_key, require_configured=False)
    effective = scene_image_prompt(visual_prompt, script_text, visual_scheme_key)
    payload = f"{scheme.key}\0{scheme.image_profile_version}\0{effective}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scene_image_path(workflow_task_id: str, sequence: int) -> str:
    root = Path(__file__).resolve().parents[3] / "data" / "processed" / "report_stick_figure_video_workflow" / str(workflow_task_id) / "images"
    root.mkdir(parents=True, exist_ok=True)
    filename = "000_title.png" if int(sequence) == 0 else f"{int(sequence):03d}.png"
    return str(root / filename)
