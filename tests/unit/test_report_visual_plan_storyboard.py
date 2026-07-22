import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import report_visual_plan_service as service
from services.gemini_browser import GeminiBrowserWorker
from services.report_stick_figure_assets import IMAGE_STYLE_SUFFIX, scene_image_path, scene_image_prompt, scene_image_prompt_hash
from services.report_visual_scheme import visual_scheme, visual_scheme_payloads
from api.report_stick_figure_video_workflow import _completed_image_asset_can_adopt_prompt_hash


class ReportVisualPlanStoryboardTest(unittest.TestCase):
    def material(self):
        script = "第一段讲需求增长。\n第二段讲产品升级，利润率提升。最后提示客户集中风险。"
        return {
            "title": "测试研报",
            "final_script_for_tts": script,
            "structured_result": {"final_script_for_tts": script},
        }

    def test_normalizer_preserves_every_source_character(self):
        script = self.material()["final_script_for_tts"]
        ds_payload = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "title_scene": {
                "scene_title": "标题镜头",
                "visual_prompt": "测试研报标题画面",
                "estimated_seconds": 3,
            },
            "segments": [
                {
                    "scene_title": "需求",
                    "script_text": "第一段讲需求增长。",
                    "visual_prompt": "数据中心需求增长的财经信息图",
                    "estimated_seconds": 5,
                },
                {
                    "scene_title": "产品与风险",
                    "script_text": "第二段讲产品升级，利润率提升。最后提示客户集中风险。",
                    "visual_prompt": "产品升级与风险警示的分屏画面",
                    "estimated_seconds": 9,
                },
            ],
        }
        gemini_text = "测试研报标题画面\n数据中心需求增长的财经信息图\n产品升级与风险警示的分屏画面"
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual("".join(item["script_text"] for item in result["segments"]), script)
        self.assertEqual(result["covered_script_chars"], len(script))
        self.assertEqual(result["segment_count"], 3)
        self.assertEqual(result["title_scene"]["script_text"], "测试研报")

    def test_incomplete_ds_result_is_rejected(self):
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "标题画面"},
            "segments": [{
                "scene_title": "只有开头",
                "script_text": "第一段讲需求增长。",
                "visual_prompt": "需求增长画面",
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            with self.assertRaisesRegex(ValueError, "未完整覆盖"):
                service.normalize_storyboard_response(self.material(), "标题画面\n需求增长画面")

    def test_storyboard_prompt_requires_full_coverage(self):
        prompt = service.storyboard_prompt(self.material())
        self.assertIn("不得省略", prompt)
        self.assertIn(self.material()["final_script_for_tts"], prompt)
        self.assertIn("禁止翻译或改写", prompt)
        self.assertIn("必须先单独输出一个‘标题镜头’", prompt)
        self.assertIn("不得省略标题镜头", prompt)

    def test_normalizer_must_preserve_gem_prompt_language_and_wording(self):
        english_prompt = "Cinematic data center, blue fiber links, ultra-detailed, 8k"
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "Editorial title card, clean typography"},
            "segments": [{
                "scene_title": "完整文案",
                "script_text": self.material()["final_script_for_tts"],
                "visual_prompt": english_prompt,
                "estimated_seconds": 12,
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            result = service.normalize_storyboard_response(
                self.material(),
                f"Editorial title card, clean typography\nPrompt:\n{english_prompt}",
            )
        self.assertEqual(result["segments"][0]["visual_prompt"], english_prompt)

    def test_normalizer_rejects_translated_or_rewritten_prompt(self):
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "Editorial title card"},
            "segments": [{
                "scene_title": "完整文案",
                "script_text": self.material()["final_script_for_tts"],
                "visual_prompt": "蓝色光纤连接的数据中心",
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            with self.assertRaisesRegex(ValueError, "改写或翻译"):
                service.normalize_storyboard_response(
                    self.material(),
                    "Editorial title card\nCinematic data center with blue fiber links",
                )

    def test_hot_submission_uses_projection_not_task_json(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        text = api_path.read_text(encoding="utf-8")
        body = text.split("def _resolve_storyboard_source", 1)[1].split("\ndef create_preview", 1)[0]
        self.assertIn("PublicationTask.final_script_chars", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("PublicationTask.result", body)

    def test_task_result_keeps_only_summary_not_segment_list(self):
        task_path = BACKEND_DIR / "tasks" / "report_visual_plan.py"
        text = task_path.read_text(encoding="utf-8")
        persist = text.split("def _persist_success", 1)[1].split("\ndef _fail", 1)[0]
        self.assertIn("ReportVisualPlanSegment(", persist)
        self.assertNotIn('"segments": segments', persist)

    def test_raw_gem_response_has_on_demand_endpoint_and_ui_entry(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        self.assertIn('/jobs/<job_id>/gemini-raw', api_text)
        self.assertIn('final_response_text', api_text)
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-script-material-workflow-test.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")
        self.assertIn('data-storyboard-raw', frontend_text)
        self.assertIn('/gemini-raw', frontend_text)

    def test_existing_storyboard_is_discovered_by_source(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        self.assertIn('/sources/<source_task_id>/latest', api_text)
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-script-material-workflow-test.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")
        self.assertIn('loadExistingStoryboard', frontend_text)
        self.assertIn('/latest', frontend_text)

    def test_completed_storyboard_polling_uses_immutable_cache(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        latest = api_text.split("def get_latest_storyboard_for_source(", 1)[1].split(
            "\n\n@report_visual_plan_bp.route", 1
        )[0]
        create = api_text.split("def create_storyboard_job(", 1)[1].split(
            "\ndef _resolve_storyboard_source", 1
        )[0]

        self.assertLess(latest.index("_cached_terminal_storyboard"), latest.index("PublicationTask.query"))
        self.assertIn('if task.status == "completed" and output:', latest)
        self.assertIn("_cache_terminal_storyboard", latest)
        self.assertIn(
            "_clear_terminal_storyboard_cache(source_cache_key, current_user=current_user)",
            create,
        )

    def test_completed_storyboard_job_detail_uses_immutable_cache(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        detail = api_text.split("def get_job(", 1)[1].split(
            "\n\n@report_visual_plan_bp.route", 1
        )[0]
        payload = api_text.split("def _storyboard_job_payload(", 1)[1].split(
            "\ndef _storyboard_output", 1
        )[0]

        self.assertLess(detail.index("_cached_terminal_storyboard_job"), detail.index("_get_owned_visual_task"))
        self.assertIn('_cache_terminal_storyboard_job(current_user, task.id, response_payload)', detail)
        self.assertIn("db.session.query(", payload)
        self.assertNotIn("ReportVisualPlanSegment.query.options", payload)

    def test_stick_video_detail_can_force_regenerate_storyboard(self):
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")

        self.assertIn("重新生成分镜方案", frontend_text)
        self.assertIn('data-generate-storyboard data-force="1"', frontend_text)
        self.assertIn("现有图片、语音和视频不会复用", frontend_text)
        self.assertIn("visual_scheme_key:this.visualScheme.key", frontend_text)

    def test_visual_schemes_bind_storyboard_and_image_contract_together(self):
        schemes = {item["key"]: item for item in visual_scheme_payloads()}
        self.assertIn("stick_figure", schemes)
        self.assertIn("doraemon", schemes)
        self.assertTrue(schemes["stick_figure"]["configured"])
        self.assertTrue(schemes["doraemon"]["configured"])

        whiteboard = scene_image_prompt("A robot cat points to a chart", "机器人讲解图表", "stick_figure")
        doraemon = scene_image_prompt("A robot cat points to a chart", "机器人讲解图表", "doraemon")
        self.assertIn("Render the scene as a Chinese self-media whiteboard animation", whiteboard)
        self.assertIn("retro 1990s Japanese 2D animation", doraemon)
        self.assertIn("Every visible text label must be concise Simplified Chinese", doraemon)
        self.assertIn("Classic Doraemon anime style", doraemon)
        self.assertNotIn("Render the scene as a Chinese self-media whiteboard animation", doraemon)
        self.assertNotEqual(
            scene_image_prompt_hash("same", "相同", "stick_figure"),
            scene_image_prompt_hash("same", "相同", "doraemon"),
        )

    def test_doraemon_scheme_is_enabled_by_configured_gem_url(self):
        with patch.dict("os.environ", {"REPORT_DORAEMON_STORYBOARD_GEMINI_URL": "https://gemini.google.com/gem/doraemon-test"}):
            scheme = visual_scheme("doraemon")
            self.assertEqual(scheme.storyboard_gemini_url, "https://gemini.google.com/gem/doraemon-test")
            self.assertEqual(scheme.image_contract, "doraemon_retro_anime_zh")

    def test_doraemon_scheme_uses_production_gem_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            scheme = visual_scheme("doraemon")
        self.assertEqual(
            scheme.storyboard_gemini_url,
            "https://gemini.google.com/gem/f9fb1e11c799",
        )
        self.assertEqual(scheme.image_profile_version, "doraemon_retro_anime_zh_16_9_v1")

    def test_stick_video_workflow_matches_audio_page_shape(self):
        frontend_root = BACKEND_DIR.parents[0] / "frontend"
        html = (frontend_root / "report-stick-figure-video-workflow.html").read_text(encoding="utf-8")
        script = (frontend_root / "js" / "report-stick-figure-video-workflow.js").read_text(encoding="utf-8")
        api = (BACKEND_DIR / "api" / "report_script_material_workflow.py").read_text(encoding="utf-8")
        self.assertIn("xl:grid-cols-[460px_minmax(0,1fr)]", html)
        self.assertIn('id="pdf-source-upload"', html)
        self.assertIn('id="pdf-source-asset"', html)
        self.assertIn('id="job-list"', html)
        self.assertIn('id="visual-scheme-select"', html)
        self.assertIn("visual_scheme_key", script)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("renderAssetTabs()", script)
        self.assertIn("button.setAttribute('aria-pressed'", script)
        self.assertIn("border-slate-900 bg-slate-900", script)
        self.assertIn("fa-check':'fa-plus", script)
        self.assertIn('data-asset-page="next"', script)
        self.assertIn("详情新页", script)
        self.assertIn('target="_blank" rel="noopener noreferrer"', script)
        submit_body = script.split("async submit(){", 1)[1].split("async loadJobs()", 1)[0]
        self.assertNotIn("location.href", submit_body)
        self.assertIn("自动投递分镜", submit_body)
        stick_api = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        material_task = (BACKEND_DIR / "tasks" / "report_script_material_workflow.py").read_text(encoding="utf-8")
        storyboard_task = (BACKEND_DIR / "tasks" / "report_visual_plan.py").read_text(encoding="utf-8")
        self.assertIn("_latest_material_task_for_asset", stick_api)
        self.assertIn("enqueue_report_visual_plan_for_material", stick_api)
        self.assertIn("_dispatch_auto_storyboard(workflow_task_id)", material_task)
        process_body = material_task.split("def _process_job(", 1)[1].split("def _dispatch_auto_storyboard(", 1)[0]
        self.assertGreater(
            process_body.index("_dispatch_auto_storyboard(workflow_task_id)"),
            process_body.index("_complete_material_task("),
        )
        self.assertIn("def enqueue_report_visual_plan_for_material(", storyboard_task)
        process_storyboard_body = storyboard_task.split("def _process_job(", 1)[1].split(
            "\ndef _update_status", 1
        )[0]
        self.assertGreater(
            process_storyboard_body.index("_dispatch_auto_scene_images(task_id)"),
            process_storyboard_body.index("_persist_success("),
        )
        auto_images_body = storyboard_task.split("def _dispatch_auto_scene_images(", 1)[1].split(
            "\ndef _sync_parent_storyboard_failure", 1
        )[0]
        self.assertIn("_ensure_scene_assets(parent_snapshot, force_refresh=True)", auto_images_body)
        self.assertIn("_enqueue_image_asset(parent_snapshot, asset)", auto_images_body)
        self.assertNotIn("PublicationTask.params", auto_images_body)
        self.assertNotIn("PublicationTask.result", auto_images_body)
        self.assertIn('request.files.get("pdf")', api)
        self.assertIn("_save_pdf_upload", api)

    def test_scene_assets_are_row_based_and_tasks_keep_only_references(self):
        model_text = (BACKEND_DIR / "models" / "report_stick_figure_asset.py").read_text(encoding="utf-8")
        api_text = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        image_task_text = (BACKEND_DIR / "tasks" / "report_stick_figure_assets.py").read_text(encoding="utf-8")
        frontend_text = (BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js").read_text(encoding="utf-8")
        self.assertIn("class ReportStickFigureSceneAsset", model_text)
        self.assertIn("visual_prompt_hash", model_text)
        self.assertIn('"scene_asset_id": asset_id', api_text)
        self.assertIn('"text_source": {"kind": "report_visual_plan_segment"', api_text)
        self.assertNotIn('"visual_prompt":', image_task_text)
        self.assertIn("续跑失败图片", frontend_text)
        self.assertIn("续跑失败语音", frontend_text)

    def test_scene_files_use_stable_zero_padded_sequence(self):
        self.assertTrue(scene_image_path("workflow-1", 0).endswith("/workflow-1/images/000_title.png"))
        self.assertTrue(scene_image_path("workflow-1", 7).endswith("/workflow-1/images/007.png"))

    def test_scene_image_prompt_binds_whiteboard_style_and_chinese_labels(self):
        raw = 'Photorealistic server room with label "Citi 149%", cinematic lighting, 8k'
        script = "花旗：中际旭创净利润复合增速149%"
        prompt = scene_image_prompt(raw, script)
        self.assertIn(raw, prompt)
        self.assertIn(script, prompt)
        self.assertIn("Every visible text label in the image must be concise Simplified Chinese", prompt)
        self.assertIn("Ignore conflicting style words such as photorealistic", prompt)
        self.assertTrue(prompt.endswith(IMAGE_STYLE_SUFFIX))

    def test_scene_image_prompt_hash_covers_style_and_chinese_context(self):
        first = scene_image_prompt_hash("same scene", "中文标签一")
        second = scene_image_prompt_hash("same scene", "中文标签二")
        self.assertNotEqual(first, second)

    def test_completed_pre_scheme_hash_image_can_be_adopted_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tempdir:
            image_path = Path(tempdir) / "000_title.png"
            image_path.write_bytes(b"completed-image")
            asset = SimpleNamespace(
                image_status="completed",
                image_task_id="image-task-1",
                image_path=str(image_path),
                image_bytes=image_path.stat().st_size,
                image_width=1280,
                image_height=720,
            )
            self.assertTrue(_completed_image_asset_can_adopt_prompt_hash(asset))

            asset.image_status = "failed"
            self.assertFalse(_completed_image_asset_can_adopt_prompt_hash(asset))

    def test_generated_image_fallback_replaces_existing_file(self):
        class ImageNode:
            def screenshot(self, path):
                Path(path).write_bytes(b"new-whiteboard-image")

        class ImageList:
            def nth(self, _index):
                return ImageNode()

        class Page:
            def evaluate(self, *_args):
                raise RuntimeError("blob fetch unavailable")

            def locator(self, selector):
                self.selector = selector
                return ImageList()

        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "000_title.png"
            output.write_bytes(b"old-photorealistic-image")
            result = worker._save_generated_image(
                Page(),
                {"src": "blob:new-image", "dom_index": 0, "width": 1280, "height": 720},
                str(output),
            )
            self.assertEqual(output.read_bytes(), b"new-whiteboard-image")
            self.assertEqual(result["saved_by"], "screenshot")
            self.assertFalse(output.with_suffix(".png.part").exists())

    def test_gemini_image_mode_supports_current_upload_and_tools_menu(self):
        browser_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(encoding="utf-8")
        self.assertIn("button[aria-label*='上传和工具']", browser_text)
        self.assertIn("button[aria-label*='Upload and tools']", browser_text)
        self.assertIn("[role='menuitemcheckbox']:has-text('{label}')", browser_text)
        self.assertIn('"制作图片"', browser_text)
        self.assertIn("tool_button = self._wait_for_visible_enabled(", browser_text)


if __name__ == "__main__":
    unittest.main()
