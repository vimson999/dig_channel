import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_audio_workflow as report_audio_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.report_audio_workflow import FEATURE_NAME  # noqa: E402


class ReportAudioWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmpdir.cleanup()

    def test_job_payload_exposes_audio_script_quality_issue(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf"},
                },
            },
            result={
                "structured_result": {
                    "provider": "deepseek",
                    "final_script_for_tts": "第一部分：开场与变量切换 这里才是正文。",
                },
                "tts_text": {"chars": 23, "chunks": 1},
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        self.assertEqual(payload["audio_script"]["provider"], "deepseek")
        self.assertEqual(payload["audio_script"]["chunks"], 1)
        self.assertIn("第一部分", payload["audio_script"]["text"])
        self.assertIn("结构", payload["audio_script"]["quality_issue"])

    def test_previewable_file_is_inline_only_for_preview_requests(self):
        media_path = Path(self.tmpdir.name) / "cover.jpg"
        media_path.write_bytes(b"jpeg-preview")

        with self.app.test_request_context("/?inline=1"):
            preview = report_audio_api._send_previewable_file(
                str(media_path),
                "image/jpeg",
                "cover.jpg",
            )
        with self.app.test_request_context("/"):
            download = report_audio_api._send_previewable_file(
                str(media_path),
                "image/jpeg",
                "cover.jpg",
            )

        self.assertTrue(preview.headers["Content-Disposition"].startswith("inline;"))
        self.assertTrue(preview.cache_control.no_store)
        self.assertTrue(preview.cache_control.no_cache)
        self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))

    def test_bilibili_cover_is_promoted_and_accepted_by_upload_resolver(self):
        root = Path(self.tmpdir.name)
        bilibili_cover_path = root / "bilibili-cover.jpg"
        bilibili_cover_path.write_bytes(b"jpeg-bilibili-cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {"pdf": {"filename": "sample.pdf"}},
            },
            result={
                "video": {
                    "enabled": True,
                    "status": "completed",
                    "bilibili_cover_path": str(bilibili_cover_path),
                    "keyframes": {
                        "bilibili_cover": {
                            "path": str(bilibili_cover_path),
                            "label": "B站封皮关键帧",
                            "ratio": "1146:717",
                            "width": 1146,
                            "height": 717,
                        },
                    },
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)
        cover_path, cover_key = report_audio_api._report_bilibili_cover_path(
            parent,
            "vertical",
            "bilibili_cover",
        )

        expected_url = f"/api/report-audio-workflows/jobs/{parent.id}/download-bilibili-cover"
        self.assertEqual(payload["download_bilibili_cover_url"], expected_url)
        self.assertEqual(payload["video"]["bilibili_cover"]["download_url"], expected_url)
        self.assertEqual(payload["video"]["bilibili_cover"]["ratio"], "1146:717")
        self.assertEqual(cover_path, str(bilibili_cover_path))
        self.assertEqual(cover_key, "bilibili_cover")

    def test_bilibili_defaults_pair_video_with_variant_title_and_cover(self):
        root = Path(self.tmpdir.name)
        landscape_cover = root / "landscape.jpg"
        bilibili_cover = root / "bilibili.jpg"
        landscape_cover.write_bytes(b"landscape")
        bilibili_cover.write_bytes(b"bilibili")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "report_audio_workflow": {"pdf": {"filename": "sample.pdf"}}},
            result={
                "structured_result": {
                    "landscape_title": "横屏标题：稳健完整",
                    "vertical_title": "竖屏标题：核心机会",
                    "cover_title": "旧版兼容标题",
                },
                "video": {
                    "cover_path": str(landscape_cover),
                    "bilibili_cover_path": str(bilibili_cover),
                },
            },
            created_at=datetime(2026, 7, 11, 12, 0, 0),
        )
        db.session.add(parent)
        db.session.commit()

        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "landscape"),
            "机构研报 / 横屏标题：稳健完整 / 2026-07-11",
        )
        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "vertical"),
            "机构研报 | 竖屏标题：核心机会 | 2026-07-11",
        )
        self.assertEqual(report_audio_api._report_bilibili_cover_path(parent, "landscape"), (str(landscape_cover), "cover"))
        self.assertEqual(report_audio_api._report_bilibili_cover_path(parent, "vertical"), (str(bilibili_cover), "bilibili_cover"))

    def test_bilibili_publish_title_extracts_bank_and_report_date(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {"pdf": {"filename": "野村-源杰科技首次覆盖-260708.pdf"}},
            },
            result={
                "structured_result": {
                    "landscape_title": "野村:源杰科技估值已透支\n目标价隐含下行空间",
                    "landscape_subtitle": "盈利复合增速114%",
                    "vertical_title": "光芯片盈利暴增114%\n源杰估值为何仍透支",
                    "vertical_subtitle": "目标价隐含3.5%下行",
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "landscape"),
            "野村 / 源杰科技估值已透支 / 目标价隐含下行空间 / 盈利复合增速114% / 2026-07-08",
        )
        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "vertical"),
            "野村 | 光芯片盈利暴增114% | 源杰估值为何仍透支 | 目标价隐含3.5%下行 | 2026-07-08",
        )

    def test_completed_scheme2_job_can_backfill_bilibili_cover_without_video_rerender(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        screenshot_path.write_bytes(b"jpeg-page")
        bilibili_cover_path = root / "bilibili-cover.jpg"
        bilibili_cover_path.write_bytes(b"jpeg-bilibili-cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf"},
                    "video": {"vertical_enabled": True, "vertical_video_scheme": "scheme2"},
                },
            },
            result={
                "structured_result": {
                    "cover_title": "方案二标题",
                    "cover_subtitle": "方案二副标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 9},
                "video": {"status": "completed", "vertical_video_scheme": "scheme2"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.thumbnail_service,
            "generate_workbench_bilibili_cover",
            return_value=str(bilibili_cover_path),
        ) as generate_cover:
            cover = report_audio_api._generate_bilibili_cover_for_task(parent)

        self.assertEqual(cover["path"], str(bilibili_cover_path))
        self.assertFalse(generate_cover.call_args.kwargs["show_footer"])
        db.session.refresh(parent)
        self.assertEqual(parent.result["video"]["bilibili_cover_path"], str(bilibili_cover_path))

    def test_vertical_backfill_creates_vertical_only_publisher_task(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "title": "大摩:十天内两度上调国巨",
                        "subtitle": "AI机柜电容产值飙升182%",
                        "sub_burn_enabled": True,
                    },
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {
                    "final_script_for_tts": "这是一段用于生成字幕的口播稿。",
                    "cover_title": "大摩:十天内两度上调国巨",
                    "cover_subtitle": "AI机柜电容产值飙升182%",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"video_path": str(root / "landscape.mp4")},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-vertical"),
        ) as apply_async:
            child = report_audio_api._create_vertical_video_backfill_task(parent, scheme="scheme2")

        apply_async.assert_called_once_with(args=[child.id], queue="render", retry=False)
        self.assertEqual(child.celery_task_id, "celery-vertical")
        self.assertEqual(child.params["feature"], "publisher")
        self.assertEqual(child.params["source_feature"], FEATURE_NAME)
        self.assertEqual(child.params["source_task_id"], parent.id)
        self.assertFalse(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertEqual(child.params["vertical_video"]["image_path"], str(screenshot_path))
        self.assertTrue(child.params["vertical_thumbnail"]["enabled"])
        self.assertTrue(child.params["bilibili_cover"]["enabled"])
        self.assertFalse(child.params["bilibili_cover"]["show_footer"])
        self.assertFalse(child.params["bilibili"]["enabled"])
        self.assertNotIn("thumbnail", child.params)
        self.assertEqual(child.result["metadata"]["source"], report_audio_api.VERTICAL_BACKFILL_SOURCE)

    def test_main_publisher_vertical_output_is_reused_as_downloadable_video(self):
        root = Path(self.tmpdir.name)
        landscape_path = root / "landscape.mp4"
        vertical_path = root / "vertical.mp4"
        screenshot_path = root / "page.jpg"
        landscape_path.write_bytes(b"landscape")
        vertical_path.write_bytes(b"vertical")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {"enabled": True, "vertical_enabled": True},
                },
            },
            result={
                "video": {
                    "enabled": True,
                    "status": "completed",
                    "video_path": str(landscape_path),
                    "vertical_enabled": True,
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        child = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_feature": FEATURE_NAME,
                "source_task_id": parent.id,
                "video": {"enabled": True},
                "vertical_video": {"enabled": True},
            },
            result={
                "source_task_id": parent.id,
                "video_path": str(landscape_path),
                "vertical_video_path": str(vertical_path),
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
            },
        )
        db.session.add(child)
        db.session.commit()

        video = report_audio_api._video_result_with_vertical_backfill(parent)
        self.assertEqual(video["vertical_video_path"], str(vertical_path))
        self.assertEqual(video["vertical_publisher_task_id"], child.id)
        self.assertEqual(report_audio_api._vertical_video_path_for_task(parent), str(vertical_path))

        public = report_audio_api._public_video(parent.id, video)
        self.assertEqual(
            public["vertical_video"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-vertical-video",
        )

        summaries = report_audio_api._vertical_child_summaries_for_parent_ids([parent.id])
        self.assertEqual(summaries[parent.id]["job_id"], child.id)
        self.assertEqual(
            summaries[parent.id]["vertical_video"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-vertical-video",
        )

    def test_regenerate_cover_updates_parent_result_and_keeps_history(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        old_cover_path = root / "old-cover.jpg"
        new_cover_path = root / "new-cover.jpg"
        screenshot_path.write_bytes(b"jpeg")
        old_cover_path.write_bytes(b"old")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "tag_text": "研报播客",
                        "overlay_opacity": "0.6",
                        "cover_transform": {"x": 1, "y": 2, "scale": 1.1},
                    },
                },
            },
            result={
                "structured_result": {
                    "cover_title": "旧标题",
                    "cover_subtitle": "旧副标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {
                    "video_path": str(root / "landscape.mp4"),
                    "cover_path": str(old_cover_path),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        def fake_generate_thumbnail(**kwargs):
            self.assertEqual(kwargs["main_title"], "新标题")
            self.assertEqual(kwargs["sub_title"], "新副标题")
            self.assertEqual(kwargs["custom_bg_path"], str(screenshot_path))
            self.assertEqual(kwargs["mode"], "screenshot")
            self.assertEqual(kwargs["transform"], {"x": 1, "y": 2, "scale": 1.1})
            new_cover_path.write_bytes(b"new")
            return str(new_cover_path)

        with patch.object(report_audio_api.thumbnail_service, "generate_thumbnail", side_effect=fake_generate_thumbnail):
            cover = report_audio_api._regenerate_cover_for_task(parent, title="新标题", subtitle="新副标题")

        self.assertEqual(cover["path"], str(new_cover_path))
        self.assertEqual(cover["title"], "新标题")
        self.assertEqual(cover["subtitle"], "新副标题")

        db.session.refresh(parent)
        result = parent.result
        self.assertEqual(result["video"]["cover_path"], str(new_cover_path))
        self.assertEqual(result["video"]["cover_title"], "新标题")
        self.assertEqual(result["video"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["structured_result"]["cover_title"], "新标题")
        self.assertEqual(result["structured_result"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["cover_regenerations"][-1]["cover_path"], str(old_cover_path))
        self.assertEqual(result["cover_regenerations"][-1]["title"], "旧标题")
        self.assertEqual(parent.report_audio_cover_title, "新标题")
        self.assertEqual(parent.report_audio_cover_subtitle, "新副标题")
        self.assertEqual(parent.report_audio_cover_path, str(new_cover_path))

    def test_regenerate_vertical_cover_uses_manual_copy_without_rerendering_video(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        vertical_cover_path = root / "vertical-cover.jpg"
        screenshot_path.write_bytes(b"jpeg")
        vertical_cover_path.write_bytes(b"vertical")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "野村-航天-260710.pdf"},
                    "video": {"vertical_video_scheme": "scheme2"},
                },
            },
            result={
                "structured_result": {"vertical_title": "旧标题"},
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"vertical_video_path": str(root / "vertical.mp4")},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.thumbnail_service,
            "generate_workbench_vertical_cover",
            return_value=str(vertical_cover_path),
        ) as generate_cover:
            cover = report_audio_api._generate_vertical_cover_for_task(
                parent,
                title="火箭回收成本暴降",
                subtitle="每公斤成本直逼 SpaceX",
            )

        self.assertEqual(generate_cover.call_args.kwargs["main_title"], "火箭回收成本暴降")
        self.assertEqual(generate_cover.call_args.kwargs["sub_title"], "每公斤成本直逼 SpaceX")
        self.assertEqual(cover["path"], str(vertical_cover_path))
        db.session.refresh(parent)
        self.assertEqual(parent.result["video"]["vertical_cover_path"], str(vertical_cover_path))
        self.assertEqual(parent.result["video"]["vertical_cover_title"], "火箭回收成本暴降")
        self.assertEqual(parent.result["video"]["vertical_cover_subtitle"], "每公斤成本直逼 SpaceX")

    def test_normalize_cover_text_preserves_manual_line_breaks(self):
        text = report_audio_api._normalize_cover_text("  第一行  标题 \r\n 第二行\t标题  \n\n第三行", 80)

        self.assertEqual(text, "第一行 标题\n第二行 标题\n第三行")

    def test_manual_cover_title_normalizes_to_institution_colon_format(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {"pdf": {"filename": "野村证券-中国航天-260710.pdf"}},
            },
            result={"structured_result": {"title": "商业航天成本拐点"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "野村证券：中国火箭回收重复使用的原创路线\n决定商业航天生死的根本不是订单",
        )

        self.assertEqual(
            title,
            "野村：中国火箭回收重复使用的原创路线\n决定商业航天生死的根本不是订单",
        )

    def test_job_summary_payload_includes_cover_subtitle_from_projection(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="sample.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="PDF 标题",
            result_pdf_topic_title=None,
            cover_title="封面主标题",
            cover_subtitle="第一行 副标题\n第二行 副标题",
            structured_title="结构化标题",
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=True,
            video_status="completed",
            video_publisher_task_id="publisher-1",
            video_message=None,
            video_path=None,
            cover_path="/tmp/cover.jpg",
            video_title=None,
            metadata_title=None,
            status="completed",
            current_step="done",
            progress=100,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(row)

        self.assertEqual(payload["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertEqual(payload["structured_result"]["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertEqual(payload["video"]["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertNotIn("children", payload)

    def test_job_summary_payload_prefers_pdf_filename_over_generic_topic_title(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="#外资研报",
            result_pdf_topic_title=None,
            cover_title=None,
            cover_subtitle=None,
            structured_title=None,
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=None,
            video_status=None,
            video_publisher_task_id=None,
            video_message=None,
            video_path=None,
            cover_path=None,
            video_title=None,
            metadata_title=None,
            status="pending",
            current_step="queued",
            progress=0,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(row)

        self.assertEqual(payload["title"], "野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf")
        self.assertEqual(payload["pdf"]["topic_title"], "#外资研报")

    def test_job_summary_payload_exposes_download_for_main_publisher_vertical_video(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="sample.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="PDF 标题",
            result_pdf_topic_title=None,
            cover_title="封面主标题",
            cover_subtitle=None,
            structured_title=None,
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=True,
            video_status="completed",
            video_publisher_task_id="publisher-1",
            video_message=None,
            video_path=None,
            cover_path=None,
            video_title=None,
            metadata_title=None,
            status="completed",
            current_step="done",
            progress=100,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(
            row,
            {
                "publisher_vertical_video": {
                    "job_id": "publisher-1",
                    "status": "completed",
                    "vertical_video": {
                        "bytes": 8,
                        "download_url": "/api/report-audio-workflows/jobs/job-1/download-vertical-video",
                    },
                }
            },
        )

        self.assertEqual(
            payload["download_vertical_video_url"],
            "/api/report-audio-workflows/jobs/job-1/download-vertical-video",
        )
        self.assertEqual(payload["video"]["vertical_video"]["bytes"], 8)
        self.assertTrue(payload["video"]["vertical_enabled"])


if __name__ == "__main__":
    unittest.main()
