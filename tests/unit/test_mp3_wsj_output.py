from pathlib import Path

from PIL import Image

from services import mp3_video_workflow as workflow
from services.video_editor import _video_output_size


def test_wsj_manual_titles_render_into_distinct_4_3_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "artifact_path_for_job", lambda job, name, ext, date: str(tmp_path / f"{name}.{ext}"))
    automatic = workflow.generate_wsj_visual("job", {"title": "算力产业新变化"})
    manual = workflow.regenerate_visual_artifact(
        "job", "wsj_cover", plan={"title": "算力产业新变化"}, title_text="机构配置新动向\n市场估值变化",
    )
    for artifact in (automatic, manual):
        with Image.open(artifact["path"]) as image:
            assert image.size == (2048, 1536)
    assert manual["manual_title_lines"] == ["机构配置新动向", "市场估值变化"]
    assert Path(automatic["path"]).read_bytes() != Path(manual["path"]).read_bytes()


def test_wsj_video_format_does_not_change_existing_video_sizes():
    assert workflow._publisher_video_spec_for_label("wsj_video") == {"role": "wsj_4_3", "aspect_ratio": "4:3"}
    assert _video_output_size({"aspect_ratio": "4:3"}) == (1440, 1080)
    assert _video_output_size({}) == (1920, 1080)
    assert _video_output_size({}, is_vertical=True) == (1080, 1920)


def test_wsj_parent_submits_shared_image_and_records_done_video(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    cover = {"path": "/wsj/current.png", "generated_at": "2026-11-17T09:00:00Z"}
    child = SimpleNamespace(id="child", status="completed")
    submit = Mock(return_value=child)
    update = Mock()
    for name, value in {
        "_fresh_result": lambda _: {},
        "_reusable_video_summary": lambda *args, **kwargs: (None, None),
        "_active_child_from_result": lambda *args, **kwargs: None,
        "_raise_if_parent_canceled": Mock(),
        "_emit": Mock(),
        "_record_child": Mock(),
        "_task_public_summary": Mock(),
        "_wait_child": Mock(return_value=child),
        "_publisher_result_summary": lambda _: {"video_path": "/wsj/video.mp4"},
        "create_publisher_video_task": submit,
        "_update_result": update,
    }.items():
        monkeypatch.setattr(workflow, name, value)

    summary = workflow._run_wsj_video(
        "parent", user_id="user", audio={}, plan={"title": "本期标题"}, cover=cover,
        generate_subtitles=False, audio_signature="audio", force_video_regenerate_at=None,
    )
    assert submit.call_args.kwargs["image_path"] == summary["cover_path"] == cover["path"]
    assert submit.call_args.kwargs["aspect_ratio"] == "4:3"
    update.assert_called_once_with("parent", {"wsj_video": summary})


def test_replacing_wsj_cover_invalidates_only_wsj_download():
    from api.mp3_video_workflow import _video_by_key_from_result

    old = {"video_path": "/old.mp4", "cover_path": "/old.png"}
    landscape = {"video_path": "/landscape.mp4"}
    result = {"artifacts": {"wsj_cover": {"path": "/old.png"}}, "wsj_video": old, "landscape_video": landscape}
    assert _video_by_key_from_result(result, "wsj") == old
    result["artifacts"]["wsj_cover"]["path"] = "/new.png"
    assert _video_by_key_from_result(result, "wsj") is None
    assert _video_by_key_from_result(result, "landscape") == landscape


def test_wsj_has_three_title_rows_and_preserves_fixed_right_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "artifact_path_for_job", lambda *_: str(tmp_path / "output.png"))
    title = "红利风格交易到哪了？空运旺季兑现，油价回落形成收入成本剪刀差"
    artifact = workflow.generate_wsj_visual("job", {"title": title})
    assert len(artifact["title_lines"]) == 3
    assert all(artifact["title_lines"])
    assert "".join(artifact["title_lines"]) == title

    chart = (1190, 465, 1950, 1135)
    template_path = workflow.project_root() / "src/frontend/assets/images/wsj-closed-door-cover.png"
    with Image.open(template_path) as template, Image.open(artifact["path"]) as output:
        assert template.convert("RGB").crop(chart).tobytes() == output.crop(chart).tobytes()

    manual = ["机构配置新动向", "市场估值变化", "行业商业化进展"]
    assert workflow._wsj_title_rows(title, manual) == manual


def test_video_audio_speeds_keep_same_cut_and_distinct_signatures():
    audio = {"path": "/test.mp3", "duration_seconds": 100}
    plan = {"audio": {"start_seconds": 10, "end_seconds": 70, "speed": 1.2}}
    roles = ("xiaojunjun_landscape", "ciketc_vertical", "wsj_4_3")
    segments = [workflow.audio_segment_from_plan(audio, plan, role=role) for role in roles]
    assert [segment["speed"] for segment in segments] == [1.18, 1.20, 1.22]
    assert {(segment["start"], segment["end_offset"]) for segment in segments} == {(10, 30)}
    assert len({workflow._audio_segment_signature(segment) for segment in segments}) == 3
    override = workflow._apply_workflow_audio_range_override(
        plan, {"audio_range_override": {"start_seconds": 10, "end_seconds": 70, "speed": 1.0}},
    )
    assert [workflow.audio_segment_from_plan(audio, override, role=role)["speed"] for role in roles] == [1.0] * 3


def test_nine_section_response_preserves_wsj_title_and_three_lines():
    text = """1. 标题（主版本）
AI商业化打开增长空间
2. 不同角度标题版本
软件收入增长待验证
3. 精简短标题
AI收入增长
4. 黄金片段
起点：00:02:20.000
终点：00:36:07.000
5. 金句
模型能力带动收入增长
6. 小君君视频标题（斜杠格式）
内资闭门会 / 芯片需求存在分歧 / 2026-11-16
7. 此刻此声视频标题（竖线格式）
内资闭门会 | 软件财报验证临近 | 2026-11-16
8. WSJ视频标题（竖线格式）
内资闭门会 | 数字员工打开收入空间 | 2026-11-16
9. WSJ封面三行文案
```text
Anthropic商业化进入新阶段
数字员工收入有望明年放量
关注模型能力向收入转化
```
"""
    from api.mp3_video_workflow import _ds_extract_payload
    plan = workflow._local_plan_from_direct_ds_response(text, audio={"duration_seconds": 2400})
    assert plan["wsj_video_title"] == "内资闭门会 | 数字员工打开收入空间 | 2026-11-16"
    assert plan["wsj_title_lines"] == ["Anthropic商业化进入新阶段", "数字员工收入有望明年放量", "关注模型能力向收入转化"]
    assert "8." not in plan["ciketc_video_title"]
    extract = _ds_extract_payload({"plan": plan, "initial_response_text": text})
    assert extract["title_8"] == plan["wsj_video_title"]
    assert extract["wsj_title_lines"] == plan["wsj_title_lines"]
    assert workflow._publisher_video_title(plan, role="wsj_4_3") == plan["wsj_video_title"]
