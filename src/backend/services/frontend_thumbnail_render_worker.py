"""Isolated browser renderer used by FrontendThumbnailRenderer."""

import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.frontend_thumbnail_renderer import FrontendThumbnailRenderer  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        renderer = FrontendThumbnailRenderer(Path(payload["project_root"]))
        render_kwargs = dict(payload["render_kwargs"])
        if render_kwargs.get("expected_size"):
            render_kwargs["expected_size"] = tuple(render_kwargs["expected_size"])
        renderer._render_workbench_thumbnail_direct(**render_kwargs)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
