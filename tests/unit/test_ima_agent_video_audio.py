import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from api import mp3_video_workflow as mp3_api
from apis.v1 import batch_transcription as batch_api
from models import db
from models.task import PublicationTask
from services import mp3_video_workflow as workflow
from tasks import publisher


OPTIONS = {
    "planner_mode": "local",
    "fade_in_seconds": 2,
    "offsets": {
        "xiaojunjun_landscape": 0,
        "ciketc_vertical": 10,
        "wsj_4_3": 20,
    },
}

VALID_NINE_SECTION_TEXT = """1. 标题
算力投资需求增加驱动供应链变化
2. 不同角度标题版本
资本开支增长带动产业需求兑现
3. 精简短标题
算力投资需求变化
4. 黄金片段（最精华连续区间）
起点：00:00:10.000
终点：00:01:30.000
5. 金句
- “算力资本开支扩张正在向光互联与液冷供应链全面传导。”（时间戳无法确定）
- “订单兑现节奏与估值性价比将成为下一阶段核心分水岭。”（时间戳无法确定）
- “头部厂商产能瓶颈决定未来两季度的盈利弹性上限。”（时间戳无法确定）
6. 小君君视频标题
内资闭门会 / 算力投资需求增加 / 2026-11-20
7. 此刻此声视频标题
内资闭门会 | 资本开支带动产业需求兑现 | 2026-11-20
8. WSJ视频标题
内资闭门会 | 算力投资：供应链需求变化 | 2026-11-20
9. WSJ封面三行文案
算力投资需求增加
资本开支持续变化
关注供应链兑现"""

VALID_ELEVEN_SECTION_TEXT = VALID_NINE_SECTION_TEXT + """
10. 小君君横屏封皮三行文案
算力投资需求全面升温
供应链订单步入兑现期
头部产能瓶颈放大弹性
11. 此刻此声横屏封皮三行文案
资本开支加速向上
光互联与液冷率先受益
盈利与估值迎双击窗口"""


def test_status_payload_advertises_agent_audio_capability_and_speeds():
    payload = workflow.status_payload()
    assert payload["ima_agent_audio_v1"] is True
    assert payload["video_speeds"] == {
        "xiaojunjun_landscape": 1.18,
        "ciketc_vertical": 1.2,
        "wsj_4_3": 1.22,
    }


def test_agent_offsets_and_fade_reach_segments_without_changing_speeds():
    plan = {"audio": {"start_seconds": 10, "end_seconds": 90}, "agent_audio_options": OPTIONS}
    segments = [
        workflow.audio_segment_from_plan({"path": "x.mp3", "duration_seconds": 100}, plan, role=r)
        for r in OPTIONS["offsets"]
    ]
    assert [s["start"] for s in segments] == [10, 20, 30]
    assert [s["end_offset"] for s in segments] == [10, 0, 0]
    assert [s["speed"] for s in segments] == [1.18, 1.2, 1.22]
    assert all(s["fade_in_seconds"] == 2 for s in segments)
    assert [s["source_offset_seconds"] for s in segments] == [0, 10, 20]
    assert workflow._audio_segment_signature(segments[0]) != workflow._audio_segment_signature(
        {**segments[0], "fade_in_seconds": 0}
    )


def test_fade_after_speed_is_final_output_seconds():
    command = publisher._build_audio_preprocess_command("in.mp3", "out.mp3", 10, 90, 1.22, 2)
    assert command[command.index("-filter:a") + 1] == "atempo=1.22,afade=t=in:st=0:d=2"
    plain_speed_cmd = publisher._build_audio_preprocess_command("in.mp3", "out.mp3", 0, None, 1.0, 2)
    assert plain_speed_cmd[plain_speed_cmd.index("-filter:a") + 1] == "afade=t=in:st=0:d=2"


def test_offset_beyond_end_fails_instead_of_resetting_to_zero():
    with pytest.raises(workflow.Mp3VideoWorkflowError):
        workflow.audio_segment_from_plan(
            {"path": "x", "duration_seconds": 25},
            {"audio": {"start_seconds": 10, "end_seconds": 20}, "agent_audio_options": OPTIONS},
            role="wsj_4_3",
        )


def test_local_mode_never_calls_deepseek_and_uses_execution_date_instead_of_placeholder(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("Unexpected model call")

    monkeypatch.setattr(workflow, "deepseek_plan", forbidden)
    legacy_unknown = VALID_NINE_SECTION_TEXT.replace("2026-11-20", "日期待确认")
    plan = workflow.plan_from_direct_ds_response(
        legacy_unknown,
        audio={"duration_seconds": 100},
        local_only=True,
    )
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", plan["date_text"])
    assert "日期待确认" not in plan["xiaojunjun_video_title"]
    assert "日期待确认" not in plan["ciketc_video_title"]
    assert "日期待确认" not in plan["finance_cover_subtitle"]
    assert "日期待确认" not in plan["cover_subtitle"]
    assert len(plan["finance_cover_subtitle"].splitlines()) == 3
    assert len(plan["xiaojunjun_subtitle"].splitlines()) == 3
    assert len(plan["wsj_title_lines"]) == 3


def test_eleven_section_explicit_three_line_covers_and_clean_infographic_quotes(monkeypatch):
    monkeypatch.setattr(workflow, "deepseek_plan", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    plan = workflow.plan_from_direct_ds_response(
        VALID_ELEVEN_SECTION_TEXT,
        audio={"duration_seconds": 100},
        local_only=True,
    )
    assert plan["date_text"] == "2026.11.20"
    assert plan["xiaojunjun_subtitle"].splitlines() == [
        "算力投资需求全面升温",
        "供应链订单步入兑现期",
        "头部产能瓶颈放大弹性",
    ]
    assert plan["finance_cover_subtitle"].splitlines() == [
        "资本开支加速向上",
        "光互联与液冷率先受益",
        "盈利与估值迎双击窗口",
    ]
    assert plan["wsj_title_lines"] == [
        "算力投资需求增加",
        "资本开支持续变化",
        "关注供应链兑现",
    ]
    points = plan["infographic_points"]
    assert len(points) == 3
    assert all("起点" not in pt and "终点" not in pt and "00:00" not in pt and "时间戳" not in pt for pt in points)
    assert any("光互联" in pt for pt in points)


def test_local_mode_extracts_date_from_section_6_when_section_7_missing_date(monkeypatch):
    monkeypatch.setattr(workflow, "deepseek_plan", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    text = VALID_NINE_SECTION_TEXT.replace(
        "内资闭门会 / 算力投资需求增加 / 2026-11-20",
        "内资闭门会 / 算力投资需求增加 / 2026-11-18",
    ).replace(
        "内资闭门会 | 资本开支带动产业需求兑现 | 2026-11-20",
        "内资闭门会 | 资本开支带动产业需求兑现 | 日期待确认",
    )
    plan = workflow.plan_from_direct_ds_response(text, audio={"duration_seconds": 100}, local_only=True)
    assert plan["date_text"] == "2026.11.18"


def test_local_mode_rejects_missing_wsj_sections(monkeypatch):
    monkeypatch.setattr(workflow, "deepseek_plan", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    truncated = VALID_NINE_SECTION_TEXT.split("8. WSJ视频标题")[0]
    with pytest.raises(workflow.Mp3VideoWorkflowError):
        workflow.plan_from_direct_ds_response(truncated, audio={"duration_seconds": 100}, local_only=True)


def test_create_job_validates_and_persists_agent_audio_options():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        user = SimpleNamespace(id="u-1", role="admin")
        base_payload = {
            "audio_asset_id": "pipeline:11111111-2222-3333-4444-555555555555",
            "input_source": "ds_response",
            "render_mode": "full_video",
            "generate_subtitles": False,
            "ds_response_text": VALID_ELEVEN_SECTION_TEXT,
            "agent_audio_options": OPTIONS,
        }
        invalid_variants = [
            {**base_payload, "input_source": "ima_transcript", "transcript_text": "x" * 30},
            {**base_payload, "agent_audio_options": {**OPTIONS, "planner_mode": "deepseek"}},
            {**base_payload, "agent_audio_options": {**OPTIONS, "extra": 1}},
            {**base_payload, "agent_audio_options": {**OPTIONS, "fade_in_seconds": True}},
            {**base_payload, "agent_audio_options": {**OPTIONS, "fade_in_seconds": 11}},
            {**base_payload, "agent_audio_options": {**OPTIONS, "offsets": {"xiaojunjun_landscape": 0}}},
            {**base_payload, "agent_audio_options": {**OPTIONS, "offsets": {**OPTIONS["offsets"], "wsj_4_3": -1}}},
        ]
        for bad in invalid_variants:
            with app.test_request_context("/api/mp3-video-workflows/jobs", method="POST", json=bad):
                resp, status_code = mp3_api.create_job.__wrapped__(user)
                assert status_code == 400, f"expected 400 for {bad}"
                assert resp.get_json()["success"] is False

        with (
            patch.object(
                mp3_api,
                "audio_meta_from_asset_reference",
                return_value={"path": "/tmp/a.mp3", "filename": "a.mp3", "duration_seconds": 100},
            ),
            patch.object(
                mp3_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-1"),
            ),
        ):
            with app.test_request_context("/api/mp3-video-workflows/jobs", method="POST", json=base_payload):
                resp = mp3_api.create_job.__wrapped__(user)
                assert resp.status_code == 200
                body = resp.get_json()
                assert body["success"] is True
                assert body["status"] == "queued"
                task = db.session.get(PublicationTask, body["job_id"])
                assert task.params[workflow.FEATURE_NAME]["agent_audio_options"] == OPTIONS
                detail = mp3_api._job_payload(task, bilibili_uploads=[])
                assert detail["request"]["agent_audio_options"] == OPTIONS

        db.session.remove()
        db.drop_all()


def test_publisher_result_summary_exposes_audio_segment_audit_fields():
    seg = {
        "path": "/tmp/a.mp3",
        "start": 20.0,
        "end_offset": 0.0,
        "speed": 1.2,
        "fade_in_seconds": 2.0,
        "source_offset_seconds": 10.0,
    }
    child = PublicationTask(
        id="child-1",
        status="completed",
        current_step="done",
        progress=100,
        params={"feature": "publisher", "aspect_ratio": "9:16", "audio_segments": [seg], "generate_subtitles": False},
        result={"video_path": "/tmp/out.mp4", "audio_segment": seg, "audio_signature": "sig123"},
    )
    summary = workflow._publisher_result_summary(child)
    assert summary["audio_segment"] == seg
    assert summary["video_path"] == "/tmp/out.mp4"


def test_batch_transcription_supports_explicit_file_paths_list():
    app = Flask(__name__)
    app.register_blueprint(batch_api.bp)
    client = app.test_client()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "a.mp3"
        b = root / "ignored.mp3"
        out_dir = root / "out"
        a.write_bytes(b"mp3-a")
        b.write_bytes(b"mp3-b")
        with patch.object(batch_api.threading, "Thread") as mock_thread:
            resp = client.post(
                "/api/v1/batch-transcription/start",
                json={
                    "input_dir": str(root),
                    "output_dir": str(out_dir),
                    "model_size": "medium",
                    "file_paths": [str(a)],
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["total"] == 1
            assert [f["source_path"] for f in data["files"]] == [str(a.resolve())]
            mock_thread.assert_called_once()

            bad = client.post(
                "/api/v1/batch-transcription/start",
                json={
                    "output_dir": str(out_dir),
                    "file_paths": [str(root / "missing.mp3")],
                },
            )
            assert bad.status_code == 400
