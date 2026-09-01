#!/usr/bin/env python3
"""Build, validate, or safely exercise a phase-one report-video director package.

Production systems remain side-effect free. ``run-test`` can render media only
inside an explicit isolated test directory; it never submits or publishes
production tasks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.report_video_director import (  # noqa: E402
    build_director_package,
    evaluate_full_render_gate,
    validate_director_package,
)
from services.report_video_director_test_runner import (  # noqa: E402
    run_isolated_director_full_render,
    run_isolated_director_test,
)


def _read_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(payload, path: str | None):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="研报视频阶段一导演包与隔离端到端测试工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="从现有文案物料和证据映射生成导演包")
    build_parser.add_argument("--input", required=True, help="输入 JSON：material/evidence_map/options")
    build_parser.add_argument("--output", help="输出 JSON；省略时写 stdout")

    validate_parser = subparsers.add_parser("validate", help="校验导演包并评估完整渲染关口")
    validate_parser.add_argument("--input", required=True, help="导演包 JSON")
    validate_parser.add_argument("--gate-context", help="可选 JSON：preview_artifacts/human_review")
    validate_parser.add_argument("--output", help="输出 JSON；省略时写 stdout")

    run_parser = subparsers.add_parser("run-test", help="隔离运行关键帧、2-3 段样片和测试合成片")
    run_parser.add_argument("--input", required=True, help="输入 JSON：material/evidence_map")
    run_parser.add_argument("--pdf", required=True, help="明确的测试 PDF")
    run_parser.add_argument("--output-root", required=True, help="隔离输出目录")
    run_parser.add_argument("--test-task-id", required=True, help="必须以 test: 开头")
    run_parser.add_argument("--output", help="输出 manifest JSON；省略时写 stdout")

    full_parser = subparsers.add_parser("run-full-test", help="隔离运行完整真实音频成片")
    full_parser.add_argument("--input", required=True, help="输入 JSON：material/evidence_map")
    full_parser.add_argument("--pdf", required=True, help="明确的真实测试 PDF")
    full_parser.add_argument("--output-root", required=True, help="隔离输出目录")
    full_parser.add_argument("--test-task-id", required=True, help="必须以 test: 开头")
    full_parser.add_argument("--output", help="输出 manifest JSON；省略时写 stdout")

    args = parser.parse_args()
    payload = _read_json(args.input)
    if args.command == "build":
        material = payload.get("material")
        evidence_map = payload.get("evidence_map") or []
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        result = build_director_package(material, evidence_map, **options)
        _write_json(result, args.output)
        return 0 if result["validation"]["valid"] else 2
    if args.command == "run-test":
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        result = run_isolated_director_test(
            payload.get("material"),
            payload.get("evidence_map") or [],
            pdf_path=args.pdf,
            output_root=args.output_root,
            test_task_id=args.test_task_id,
            target_segment_chars=options.get("target_segment_chars", 130),
        )
        _write_json(result, args.output)
        return 0 if result.get("status") == "completed" else 2
    if args.command == "run-full-test":
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        result = run_isolated_director_full_render(
            payload.get("material"),
            payload.get("evidence_map") or [],
            pdf_path=args.pdf,
            output_root=args.output_root,
            test_task_id=args.test_task_id,
            target_segment_chars=options.get("target_segment_chars", 130),
            audio_alignment=payload.get("audio_alignment"),
        )
        _write_json(result, args.output)
        return 0 if result.get("status") == "completed" else 2

    validation = validate_director_package(payload)
    gate_context = _read_json(args.gate_context) if args.gate_context else {}
    gate = evaluate_full_render_gate(
        payload,
        preview_artifacts=gate_context.get("preview_artifacts"),
        human_review=gate_context.get("human_review"),
    )
    _write_json({"validation": validation, "full_render_gate": gate}, args.output)
    return 0 if validation["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
