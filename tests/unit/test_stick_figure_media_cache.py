import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_stick_figure_video_workflow as workflow_api  # noqa: E402


class _Field:
    def __eq__(self, other):
        return ("eq", other)

    def asc(self):
        return self


class _FakeAssetQuery:
    def __init__(self, assets):
        self.assets = assets
        self.all_calls = 0
        self._lock = threading.Lock()

    def options(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        with self._lock:
            self.all_calls += 1
        return self.assets


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, key):
        self.values.pop(key, None)


class StickFigureMediaCacheTest(unittest.TestCase):
    def setUp(self):
        workflow_api._scene_media_asset_cache.clear()
        workflow_api._scene_media_workflow_cache.clear()
        workflow_api._scene_structure_cache.clear()
        workflow_api._scene_segment_cache.clear()
        workflow_api._clear_scene_status_bundle_cache()
        workflow_api._clear_active_workflow_cache()
        workflow_api._clear_legacy_scene_bundle_cache()
        workflow_api._clear_terminal_workflow_cache()

    def tearDown(self):
        workflow_api._scene_media_asset_cache.clear()
        workflow_api._scene_media_workflow_cache.clear()
        workflow_api._scene_structure_cache.clear()
        workflow_api._scene_segment_cache.clear()
        workflow_api._clear_scene_status_bundle_cache()
        workflow_api._clear_active_workflow_cache()
        workflow_api._clear_legacy_scene_bundle_cache()
        workflow_api._clear_terminal_workflow_cache()

    def test_auto_whole_audio_alignment_degrades_when_no_reusable_wav_exists(self):
        with patch.object(
            workflow_api,
            "_ensure_whole_audio_track",
            side_effect=ValueError("没有找到同一研报已完成且文件有效的整篇 WAV"),
        ), patch.object(
            workflow_api,
            "_enqueue_whole_audio_alignment",
            side_effect=AssertionError("missing WAV must not enqueue alignment"),
        ):
            result = workflow_api._auto_enqueue_whole_audio_alignment(
                SimpleNamespace(id="workflow-1"),
                SimpleNamespace(id="storyboard-1"),
            )

        self.assertFalse(result["available"])
        self.assertFalse(result["queued"])

    def test_auto_whole_audio_alignment_reuses_idempotent_enqueue(self):
        track = SimpleNamespace(
            id="track-1",
            alignment_task_id="alignment-1",
            status="available",
        )
        with patch.object(
            workflow_api,
            "_ensure_whole_audio_track",
            return_value=track,
        ), patch.object(
            workflow_api,
            "_enqueue_whole_audio_alignment",
            return_value=SimpleNamespace(id="alignment-1", status="pending"),
        ) as enqueue:
            result = workflow_api._auto_enqueue_whole_audio_alignment(
                SimpleNamespace(id="workflow-1"),
                SimpleNamespace(id="storyboard-1"),
            )

        enqueue.assert_called_once()
        self.assertTrue(result["available"])
        self.assertTrue(result["queued"])
        self.assertEqual("alignment-1", result["job_id"])

    def test_concurrent_media_reads_bulk_load_each_workflow_once(self):
        workflow_id = "workflow-1"
        assets = [
            SimpleNamespace(
                id=f"asset-{index}",
                workflow_task_id=workflow_id,
                storyboard_task_id="storyboard-1",
                segment_id=f"segment-{index}",
                sequence=index,
                script_text_hash=f"hash-{index}",
                image_status="completed",
                image_attempt=1,
                image_path=f"/tmp/{index}.png",
                tts_status="completed",
                tts_attempt=1,
                audio_path=f"/tmp/{index}.wav",
                timeline_start_seconds=float(index),
                timeline_end_seconds=float(index + 1),
                timeline_confidence=1.0,
                timeline_source="unit_test",
            )
            for index in range(31)
        ]
        query = _FakeAssetQuery(assets)

        class FakeSceneAsset:
            id = _Field()
            workflow_task_id = _Field()
            sequence = _Field()

        FakeSceneAsset.query = query
        parent = SimpleNamespace(id=workflow_id)

        with patch.object(workflow_api, "ReportStickFigureSceneAsset", FakeSceneAsset):
            with ThreadPoolExecutor(max_workers=16) as executor:
                loaded = list(executor.map(
                    lambda index: workflow_api._owned_scene_asset(parent, f"asset-{index}"),
                    range(31),
                ))

        self.assertEqual([f"asset-{index}" for index in range(31)], [item.id for item in loaded])
        self.assertEqual(1, query.all_calls)

    def test_completed_video_manifest_is_a_bounded_detail_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timeline.json"
            path.write_text(json.dumps({
                "schema_version": "stick-video-v1",
                "scene_count": 31,
                "duration_seconds": 12.5,
                "render_options": {"speed": 1.25},
            }), encoding="utf-8")

            with patch.object(workflow_api, "timeline_manifest_path", return_value=str(path)):
                payload = workflow_api._video_manifest("workflow-1")

        self.assertEqual(31, payload["scene_count"])
        self.assertEqual(1.25, payload["render_options"]["speed"])

    def test_missing_or_invalid_video_manifest_returns_empty_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timeline.json"
            with patch.object(workflow_api, "timeline_manifest_path", return_value=str(path)):
                self.assertEqual({}, workflow_api._video_manifest("workflow-1"))
                path.write_text("not-json", encoding="utf-8")
                self.assertEqual({}, workflow_api._video_manifest("workflow-1"))

    def test_scene_structure_survives_process_memory_cache_loss(self):
        fake_redis = _FakeRedis()
        cache_key = ("workflow-1", "source-1")
        segments = [
            SimpleNamespace(
                id="segment-1",
                scene_title="第一幕",
                script_text="正文",
                visual_prompt="白板火柴人",
            )
        ]

        with patch.object(workflow_api, "_scene_structure_redis_client", return_value=fake_redis):
            workflow_api._scene_structure_cache_set(cache_key, "storyboard-1", segments)
            workflow_api._scene_structure_cache.clear()
            workflow_api._scene_segment_cache.clear()
            storyboard_id = workflow_api._scene_structure_cache_get(cache_key)

        self.assertEqual("storyboard-1", storyboard_id)
        self.assertEqual(
            "正文",
            workflow_api._scene_segment_cache["storyboard-1"]["segments"]["segment-1"].script_text,
        )
        self.assertEqual(
            workflow_api.SCENE_STRUCTURE_REDIS_TTL_SECONDS,
            next(iter(fake_redis.ttls.values())),
        )

    def test_completed_workflow_cache_skips_database_and_survives_memory_loss(self):
        fake_redis = _FakeRedis()
        user = SimpleNamespace(id="user-1")
        payload = {
            "success": True,
            "job_id": "workflow-1",
            "status": "succeeded",
            "data": {"asset_summary": {"images_completed": 31, "tts_completed": 31}},
        }

        with patch.object(workflow_api, "_scene_structure_redis_client", return_value=fake_redis):
            workflow_api._cache_terminal_workflow(user, "workflow-1", payload)
            workflow_api._terminal_workflow_cache.clear()

            with Flask(__name__).app_context():
                with patch.object(
                    workflow_api.PublicationTask,
                    "query",
                    side_effect=AssertionError("cached request must not query publication_tasks"),
                ):
                    response = workflow_api.get_job.__wrapped__(user, "workflow-1")

        self.assertEqual(payload, response.get_json())
        self.assertTrue(fake_redis.values)

    def test_terminal_workflow_cache_is_user_scoped_and_invalidatable(self):
        fake_redis = _FakeRedis()
        first_user = SimpleNamespace(id="user-1")
        second_user = SimpleNamespace(id="user-2")
        payload = {"success": True, "job_id": "workflow-1", "status": "succeeded", "data": {}}

        with patch.object(workflow_api, "_scene_structure_redis_client", return_value=fake_redis):
            workflow_api._cache_terminal_workflow(first_user, "workflow-1", payload)
            self.assertIsNone(workflow_api._cached_terminal_workflow(second_user, "workflow-1"))
            workflow_api._clear_terminal_workflow_cache(first_user, "workflow-1")
            self.assertIsNone(workflow_api._cached_terminal_workflow(first_user, "workflow-1"))

    def test_active_workflow_cache_is_user_scoped_and_invalidatable(self):
        first_user = SimpleNamespace(id="user-1")
        second_user = SimpleNamespace(id="user-2")
        payload = {"success": True, "job_id": "workflow-1", "status": "running"}

        workflow_api._cache_active_workflow(first_user, "workflow-1", payload)

        self.assertEqual(payload, workflow_api._cached_active_workflow(first_user, "workflow-1"))
        self.assertIsNone(workflow_api._cached_active_workflow(second_user, "workflow-1"))
        workflow_api._clear_terminal_workflow_cache(first_user, "workflow-1")
        self.assertIsNone(workflow_api._cached_active_workflow(first_user, "workflow-1"))

    def test_tts_batch_persists_once_before_dispatch_and_invalidates_once(self):
        parent = SimpleNamespace(id="workflow-1", user_id="user-1")
        assets = [SimpleNamespace(sequence=1), SimpleNamespace(sequence=2)]
        prepared = [
            {
                "task_id": f"task-{sequence}",
                "broker_task_id": f"broker-{sequence}",
                "parent_id": "workflow-1",
                "asset_id": f"asset-{sequence}",
                "sequence": sequence,
                "attempt": 1,
            }
            for sequence in (1, 2)
        ]
        session = MagicMock()

        with patch.object(workflow_api, "db", SimpleNamespace(session=session)), patch.object(
            workflow_api,
            "_prepare_tts_asset",
            side_effect=prepared,
        ), patch.object(
            workflow_api,
            "maybe_guarded_apply_async",
            side_effect=lambda *args, **kwargs: {
                "async_result": SimpleNamespace(id=kwargs["broker_task_id"])
            },
        ) as dispatch, patch.object(
            workflow_api,
            "_invalidate_scene_media_cache",
        ) as invalidate:
            jobs, errors = workflow_api._enqueue_tts_assets(parent, assets)

        self.assertEqual([], errors)
        self.assertEqual(["task-1", "task-2"], [job["job_id"] for job in jobs])
        session.flush.assert_called_once_with()
        session.bulk_update_mappings.assert_called_once()
        session.commit.assert_called_once_with()
        self.assertEqual(2, dispatch.call_count)
        invalidate.assert_called_once_with("workflow-1")

    def test_status_poll_uses_lightweight_rows_without_reinitializing_structure(self):
        user = SimpleNamespace(id="user-1")
        parent = SimpleNamespace(
            id="workflow-1",
            user_id="user-1",
            source_task_id="source-1",
            task_title="测试任务",
            task_video_path=None,
            report_audio_pdf_attachment_id=None,
        )
        assets = [SimpleNamespace(id="asset-1")]
        payload = {"storyboard_job_id": "storyboard-1", "items": [{"id": "asset-1"}]}

        with Flask(__name__).test_request_context(
            "/?view=status&storyboard_job_id=storyboard-1"
        ):
            with patch.object(workflow_api, "_owned_parent", return_value=parent), patch.object(
                workflow_api, "_scene_asset_status_rows", return_value=assets
            ) as status_rows, patch.object(
                workflow_api, "_scene_asset_status_bundle", return_value=payload
            ), patch.object(
                workflow_api, "_video_payload", return_value={"status": "processing"}
            ), patch.object(
                workflow_api,
                "_audio_strategy_payload",
                return_value={
                    "recommended_mode": "whole_audio",
                    "available": True,
                    "ready": False,
                    "status": "processing",
                },
            ) as audio_strategy, patch.object(
                workflow_api,
                "_scene_asset_summary",
                return_value={"images_active": 1, "tts_active": 0},
            ), patch.object(
                workflow_api, "_sync_tts_assets",
                side_effect=AssertionError("status polling must read the source-maintained scene projection"),
            ), patch.object(
                workflow_api, "_ensure_scene_assets",
                side_effect=AssertionError("status polling must not rebuild scene structure"),
            ):
                response = workflow_api.get_scene_assets.__wrapped__(user, "workflow-1")

        self.assertTrue(response.get_json()["success"])
        status_rows.assert_called_once_with("workflow-1", "storyboard-1")
        audio_strategy.assert_called_once()

    def test_status_poll_reuses_cached_payload_and_mutation_invalidates_it(self):
        parent = SimpleNamespace(id="workflow-1")
        assets = [SimpleNamespace(id="asset-1")]
        payload = {
            "workflow_job_id": "workflow-1",
            "storyboard_job_id": "storyboard-1",
            "summary": {"images_active": 0, "tts_active": 0},
            "items": [{"id": "asset-1"}],
        }

        with patch.object(
            workflow_api, "_scene_asset_status_rows", return_value=assets
        ) as status_rows, patch.object(
            workflow_api, "_sync_tts_assets", return_value=assets
        ), patch.object(
            workflow_api, "_scene_asset_status_bundle", return_value=payload
        ):
            first = workflow_api._scene_asset_status_payload(parent, "storyboard-1")
            second = workflow_api._scene_asset_status_payload(parent, "storyboard-1")
            workflow_api._invalidate_scene_media_cache("workflow-1")
            third = workflow_api._scene_asset_status_payload(parent, "storyboard-1")

        self.assertEqual(payload, first)
        self.assertEqual(payload, second)
        self.assertEqual(payload, third)
        self.assertEqual(2, status_rows.call_count)

    def test_full_scene_bundle_prewarms_compact_status_cache(self):
        full_payload = {
            "workflow_job_id": "workflow-1",
            "storyboard_job_id": "storyboard-1",
            "video": {"status": "pending"},
            "audio_strategy": {"ready": False},
            "summary": {"images_active": 0, "tts_active": 0},
            "items": [{
                "id": "asset-1",
                "sequence": 1,
                "scene_title": "仅完整详情需要",
                "script_text": "完整脚本",
                "visual_prompt": "完整提示词",
                "image": {"status": "completed", "progress": 100},
                "tts": {"status": "failed", "progress": 85},
                "timeline": {"source": None},
            }],
        }

        workflow_api._cache_legacy_scene_bundle(full_payload)
        with patch.object(
            workflow_api,
            "_scene_asset_status_rows",
            side_effect=AssertionError("full load must prewarm the next status poll"),
        ):
            status_payload = workflow_api._scene_asset_status_payload(
                SimpleNamespace(id="workflow-1"),
                "storyboard-1",
            )

        self.assertEqual("asset-1", status_payload["items"][0]["id"])
        self.assertEqual("failed", status_payload["items"][0]["tts"]["status"])
        self.assertNotIn("scene_title", status_payload["items"][0])
        self.assertNotIn("script_text", status_payload["items"][0])
        self.assertNotIn("visual_prompt", status_payload["items"][0])

    def test_status_bundle_preserves_all_dynamic_business_fields(self):
        parent = SimpleNamespace(id="workflow-1")
        storyboard = SimpleNamespace(id="storyboard-1")
        assets = [
            SimpleNamespace(
                id=f"asset-{sequence}",
                sequence=sequence,
                image_status="failed" if sequence == 0 else "completed",
                image_progress=100,
                image_attempt=2,
                image_bytes=123,
                image_width=1920,
                image_height=1080,
                image_error="image failed" if sequence == 0 else None,
                tts_status="completed",
                tts_progress=100,
                tts_attempt=1,
                audio_bytes=456,
                audio_seconds=3.5,
                tts_error=None,
                timeline_source="whole_audio",
                timeline_start_seconds=1.0,
                timeline_end_seconds=4.5,
                timeline_confidence=0.98,
            )
            for sequence in range(2)
        ]
        audio_strategy = {
            "available": True,
            "ready": True,
            "aligned_scene_count": 1,
        }

        with patch.object(workflow_api, "_video_payload", return_value={"status": "processing"}), patch.object(
            workflow_api, "_audio_strategy_payload", return_value=audio_strategy
        ):
            payload = workflow_api._scene_asset_status_bundle(parent, storyboard, assets)

        self.assertEqual("image failed", payload["items"][0]["image"]["error"])
        self.assertEqual(1920, payload["items"][1]["image"]["width"])
        self.assertEqual(3.5, payload["items"][1]["tts"]["seconds"])
        self.assertEqual(0.98, payload["items"][1]["timeline"]["confidence"])
        self.assertEqual("processing", payload["video"]["status"])
        self.assertTrue(payload["summary"]["audio_ready"])
        self.assertEqual(1, payload["summary"]["body_total"])

    def test_legacy_full_poll_merges_lightweight_state_with_static_scene_content(self):
        parent = SimpleNamespace(id="workflow-1", source_task_id="source-1")
        full_payload = {
            "workflow_job_id": "workflow-1",
            "storyboard_job_id": "storyboard-1",
            "image_style": {"profile": "v1"},
            "items": [{
                "id": "asset-1",
                "sequence": 1,
                "scene_title": "静态标题",
                "script_text": "静态正文",
                "visual_prompt": "静态提示词",
                "image": {"status": "queued", "progress": 0},
                "tts": {"status": "pending", "progress": 0},
                "timeline": {},
            }],
        }
        status_payload = {
            "workflow_job_id": "workflow-1",
            "storyboard_job_id": "storyboard-1",
            "video": {"status": "processing"},
            "summary": {"images_completed": 1},
            "items": [{
                "id": "asset-1",
                "sequence": 1,
                "image": {"status": "completed", "progress": 100},
                "tts": {"status": "pending", "progress": 0},
                "timeline": {},
            }],
        }
        workflow_api._cache_legacy_scene_bundle(full_payload)

        with patch.object(
            workflow_api, "_completed_storyboard", return_value=SimpleNamespace(id="storyboard-1")
        ), patch.object(
            workflow_api, "_scene_asset_status_payload", return_value=status_payload
        ) as status_loader, patch.object(
            workflow_api, "_scene_asset_status_rows",
            side_effect=AssertionError("legacy repeat poll must use cached status payload"),
        ), patch.object(
            workflow_api, "_scene_assets",
            side_effect=AssertionError("legacy repeat poll must not load full scene rows"),
        ):
            merged = workflow_api._cached_legacy_scene_bundle(parent)

        self.assertEqual("静态正文", merged["items"][0]["script_text"])
        self.assertEqual("completed", merged["items"][0]["image"]["status"])
        self.assertEqual("v1", merged["image_style"]["profile"])
        status_loader.assert_called_once_with(parent, "storyboard-1")


if __name__ == "__main__":
    unittest.main()
