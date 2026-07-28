import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRun, ZsxqTopic  # noqa: E402


class TaskProjectionModelTest(unittest.TestCase):
    def test_zsxq_topic_payload_hash_matches_postgres_jsonb_text(self):
        topic = ZsxqTopic(
            group_id="g",
            tab_id="t",
            topic_id="topic",
            raw_payload={"long_key": 1, "a": {"zz": 2, "b": 3}},
        )

        self.assertEqual(topic.raw_payload_hash, "4980f2e2724efb21f18e87bf5557f3e4")

    def test_publication_task_projects_content_clean_terminal_result(self):
        task = PublicationTask(
            params={"feature": "content_pdf_clean"},
            result={
                "local_output_dir": "/tmp/cleaned",
                "files_count": 4,
                "success_count": 3,
                "failed_count": 1,
                "message": "清洗完成",
                "cleaned_files": [{"path": f"/tmp/{index}.pdf"} for index in range(3)],
            },
        )

        self.assertEqual(task.content_clean_output_dir, "/tmp/cleaned")
        self.assertEqual(task.content_clean_files_count, 4)
        self.assertEqual(task.content_clean_success_count, 3)
        self.assertEqual(task.content_clean_failed_count, 1)
        self.assertEqual(task.content_clean_message, "清洗完成")

    def test_publication_task_projection_from_report_audio_payload(self):
        task = PublicationTask(
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "pdf": {
                        "attachment_id": "asset-1",
                        "filename": "sample.pdf",
                        "bytes": 123,
                        "topic_title": "原始标题",
                    },
                    "metadata": {"title": "元数据标题"},
                    "video": {"title": "视频标题"},
                },
            },
            result={
                "started_at": "2026-06-17T01:02:03Z",
                "pdf": {"filename": "clean.pdf", "bytes": 456, "topic_title": "结果标题"},
                "structured_result": {
                    "cover_title": "封面标题",
                    "cover_subtitle": "封面副标题",
                    "title": "结构化标题",
                },
                "audio": {"wav_path": "/tmp/audio.wav", "seconds": 12.5, "bytes": 789},
                "video": {
                    "enabled": "true",
                    "status": "completed",
                    "publisher_task_id": "pub-1",
                    "message": "ok",
                    "video_path": "/tmp/video.mp4",
                    "cover_path": "/tmp/cover.png",
                    "vertical_cover_path": "/tmp/vertical-cover.jpg",
                    "keyframes": {
                        "landscape_cover": {"path": "/tmp/landscape-keyframe.jpg"},
                        "podcast_cover": {"path": "/tmp/podcast-cover.jpg"},
                        "bilibili_cover": {"path": "/tmp/bilibili-cover.jpg"},
                    },
                },
                "output_path": "/tmp/result.json",
            },
        )

        self.assertEqual(task.feature, "report_pdf_audio_workflow")
        self.assertEqual(task.user_id, "1")
        self.assertEqual(task.report_audio_pdf_attachment_id, "asset-1")
        self.assertEqual(task.report_audio_pdf_filename, "sample.pdf")
        self.assertEqual(task.report_audio_result_pdf_filename, "clean.pdf")
        self.assertEqual(task.report_audio_cover_title, "封面标题")
        self.assertEqual(task.report_audio_cover_subtitle, "封面副标题")
        self.assertEqual(task.report_audio_wav_path, "/tmp/audio.wav")
        self.assertTrue(task.report_audio_video_enabled)
        self.assertEqual(task.report_audio_video_path, "/tmp/video.mp4")
        self.assertEqual(task.report_audio_landscape_keyframe_path, "/tmp/landscape-keyframe.jpg")
        self.assertEqual(task.report_audio_vertical_cover_path, "/tmp/vertical-cover.jpg")
        self.assertEqual(task.report_audio_podcast_cover_path, "/tmp/podcast-cover.jpg")
        self.assertEqual(task.report_audio_bilibili_cover_path, "/tmp/bilibili-cover.jpg")
        self.assertEqual(task.task_title, "元数据标题")

    def test_publication_task_projection_prefers_filename_over_generic_topic_title(self):
        task = PublicationTask(
            params={
                "feature": "report_pdf_audio_workflow",
                "report_audio_workflow": {
                    "pdf": {
                        "attachment_id": "asset-1",
                        "filename": "野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf",
                        "topic_title": "#外资研报",
                    },
                },
            },
            result={},
        )

        self.assertEqual(task.task_title, "野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf")

    def test_publication_task_projection_from_script_material_payload(self):
        task = PublicationTask(
            params={
                "feature": "report_script_material_workflow",
                "user_id": "system",
                "source_feature": "zsxq_attachment",
                "source_task_id": "asset-2",
                "report_script_material_workflow": {
                    "pdf": {
                        "attachment_id": "asset-2",
                        "filename": "material.pdf",
                        "bytes": 321,
                        "topic_title": "基础物料标题",
                    },
                    "metadata": {"title": "物料元数据标题"},
                    "video": {"enabled": False},
                },
            },
            result={
                "structured_result": {
                    "title": "结构化物料标题",
                    "cover_title": "封面",
                    "final_script_for_tts": "这是一段基础文案。",
                },
                "video": {"enabled": False, "status": "skipped"},
            },
        )

        self.assertEqual(task.feature, "report_script_material_workflow")
        self.assertEqual(task.source_task_id, "asset-2")
        self.assertEqual(task.report_audio_pdf_attachment_id, "asset-2")
        self.assertEqual(task.report_audio_pdf_filename, "material.pdf")
        self.assertEqual(task.report_audio_pdf_topic_title, "基础物料标题")
        self.assertFalse(task.report_audio_video_enabled)
        self.assertEqual(task.task_title, "物料元数据标题")
        self.assertEqual(task.final_script_chars, len("这是一段基础文案。"))

    def test_publication_task_projection_defaults_legacy_publisher_feature(self):
        task = PublicationTask(params={"bilibili": {"title": "发布标题"}}, result={"video_path": "/tmp/a.mp4"})

        self.assertEqual(task.feature, "publisher")
        self.assertEqual(task.task_title, "发布标题")
        self.assertEqual(task.task_video_path, "/tmp/a.mp4")

    def test_publication_task_projects_gemini_response_chars_without_whitespace(self):
        task = PublicationTask(
            params={"feature": "report_pdf_gemini"},
            result={"final_response_text": "第一段。\n  第二段。"},
        )

        self.assertEqual(task.gemini_response_chars, len("第一段。第二段。"))

    def test_publication_task_projects_publisher_match_fields(self):
        task = PublicationTask(
            params={
                "feature": "publisher",
                "video": {"enabled": True},
                "vertical_video": {"enabled": False, "preview_seconds": 10},
                "metadata": {"render_mode": "preview_10s"},
            },
            result={},
        )

        self.assertTrue(task.publisher_landscape_enabled)
        self.assertFalse(task.publisher_vertical_enabled)
        self.assertEqual(task.publisher_render_mode, "preview_10s")
        self.assertEqual(task.publisher_preview_seconds, 10)

    def test_doubao_projection_is_not_gated_by_publisher_fields(self):
        task = PublicationTask(
            params={
                "feature": "doubao_browser_tts",
                "doubao_browser_tts": {
                    "metadata": {"text_hash": "a" * 64, "chunk_index": 2},
                },
            },
            result={},
        )

        self.assertEqual(task.doubao_tts_text_hash, "a" * 64)
        self.assertEqual(task.doubao_tts_chunk_index, 2)
        self.assertIsNone(task.publisher_render_mode)

    def test_mp3_audio_summary_projection_preserves_list_fields(self):
        task = PublicationTask(
            params={
                "feature": "mp3_video_workflow",
                "source_task_id": "audio-1",
                "mp3_video_workflow": {
                    "audio": {
                        "filename": "memo.mp3",
                        "topic_title": "调研纪要",
                        "tab_label": "调研纪要 MP3",
                        "path": "/tmp/memo.mp3",
                        "download_count": "23",
                        "duration_seconds": "125.5",
                    },
                },
            },
            result={},
        )

        self.assertEqual(task.mp3_audio_filename, "memo.mp3")
        self.assertEqual(task.mp3_audio_topic_title, "调研纪要")
        self.assertEqual(task.mp3_audio_tab_label, "调研纪要 MP3")
        self.assertEqual(task.mp3_audio_path, "/tmp/memo.mp3")
        self.assertEqual(task.mp3_audio_download_count, 23)
        self.assertEqual(task.mp3_audio_duration_seconds, 125.5)

    def test_publisher_mp3_match_projection_preserves_reuse_contract(self):
        task = PublicationTask(
            params={
                "feature": "publisher",
                "aspect_ratio": "9:16",
                "generate_subtitles": False,
                "metadata": {
                    "role": "ciketc_vertical",
                    "audio_signature": "sig-1",
                },
                "video": {"enabled": True},
            },
            result={"bilibili_cover_path": "/tmp/publisher-bilibili-cover.jpg"},
        )

        self.assertEqual(task.publisher_child_role, "ciketc_vertical")
        self.assertEqual(task.publisher_aspect_ratio, "9:16")
        self.assertFalse(task.publisher_generate_subtitles)
        self.assertEqual(task.publisher_audio_signature, "sig-1")
        self.assertEqual(task.publisher_bilibili_cover_path, "/tmp/publisher-bilibili-cover.jpg")

    def test_zsxq_attachment_file_ext_projection(self):
        attachment = ZsxqAttachment(
            group_id="g",
            tab_id="t",
            topic_id="topic",
            attachment_key="k",
            name="研报.PDF",
            local_path="/tmp/other.txt",
        )

        self.assertEqual(attachment.file_ext, "pdf")

    def test_zsxq_sync_run_projection_from_system_result(self):
        run = ZsxqSyncRun(
            group_id="28888112822211",
            mode="sys_manual",
            status="running",
            result={
                "system_sync": {
                    "batch_id": "manual-20260617-1",
                    "attempt_id": "attempt-1",
                    "round": "2",
                    "max_rounds": "5",
                    "trigger": "manual",
                    "queued_at": "2026-06-17T01:02:03Z",
                    "max_download_files": "3",
                },
                "topics_fetched": "12",
                "topics_new": 2,
                "attachments_downloaded": "3",
                "download_limit_reached": "true",
            },
        )

        self.assertEqual(run.system_batch_id, "manual-20260617-1")
        self.assertEqual(run.system_attempt_id, "attempt-1")
        self.assertEqual(run.system_round, 2)
        self.assertEqual(run.system_max_rounds, 5)
        self.assertEqual(run.system_trigger, "manual")
        self.assertEqual(run.system_max_download_files, 3)
        self.assertEqual(run.topics_fetched, 12)
        self.assertEqual(run.topics_new, 2)
        self.assertEqual(run.attachments_downloaded, 3)
        self.assertTrue(run.download_limit_reached)

    def test_zsxq_sync_run_projection_falls_back_to_partial_progress(self):
        run = ZsxqSyncRun(
            group_id="28888112822211",
            mode="sys_manual",
            result={
                "system_sync": {"batch_id": "batch-1"},
                "partial": {
                    "topics_fetched": "4",
                    "topics_existing": "3",
                    "attachments_seen": "8",
                    "download_limit_reached": "false",
                },
            },
        )

        self.assertEqual(run.system_batch_id, "batch-1")
        self.assertEqual(run.topics_fetched, 4)
        self.assertEqual(run.topics_existing, 3)
        self.assertEqual(run.attachments_seen, 8)
        self.assertFalse(run.download_limit_reached)


if __name__ == "__main__":
    unittest.main()
