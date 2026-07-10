import unittest
import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"


def _python_files(root):
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


class DbProjectionDisciplineTest(unittest.TestCase):
    def test_publication_task_hot_paths_do_not_scan_params_json_text(self):
        offenders = []
        for path in _python_files(BACKEND_DIR):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "cast(PublicationTask.params" in text:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual([], offenders)

    def test_publication_task_bare_query_get_is_not_reintroduced(self):
        offenders = []
        for path in _python_files(BACKEND_DIR):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "PublicationTask.query.get" in text:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual([], offenders)

    def test_publication_task_load_only_does_not_use_legacy_query_get(self):
        offenders = []
        for path in _python_files(BACKEND_DIR):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "PublicationTask.query.options" not in text:
                continue
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "get" or not isinstance(node.func.value, ast.Call):
                    continue
                owner = node.func.value.func
                query = owner.value if isinstance(owner, ast.Attribute) else None
                model = query.value if isinstance(query, ast.Attribute) and query.attr == "query" else None
                if (
                    isinstance(owner, ast.Attribute)
                    and owner.attr == "options"
                    and isinstance(model, ast.Name)
                    and model.id == "PublicationTask"
                ):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

        self.assertEqual([], offenders)

    def test_report_audio_vertical_list_uses_structured_child_projections(self):
        api_path = BACKEND_DIR / "api" / "report_audio_workflow.py"
        text = api_path.read_text(encoding="utf-8")
        body = text.split("def _vertical_child_summary_groups(", 1)[1].split(
            "\ndef _vertical_child_priority", 1
        )[0]

        self.assertIn("publisher_vertical_enabled", body)
        self.assertIn("publisher_vertical_video_path", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("PublicationTask.result", body)

        child_query = text.split("def _child_task_summary_query(", 1)[1].split(
            "\ndef _dedupe_child_summary_rows", 1
        )[0]
        self.assertIn("func.substr(PublicationTask.error_message, 1, 500)", child_query)

        for function_name, next_name in (
            ("_latest_vertical_video_child", "_latest_matching_vertical_video_child"),
            ("_latest_matching_vertical_video_child", "_is_vertical_backfill_child"),
        ):
            latest = text.split(f"def {function_name}(", 1)[1].split(f"\ndef {next_name}", 1)[0]
            self.assertIn("_child_task_summary_query()", latest)
            self.assertIn("publisher_vertical_enabled", latest)
            self.assertNotIn("PublicationTask.params", latest)
            self.assertNotIn("PublicationTask.result", latest)

    def test_report_audio_media_downloads_use_parent_projection_columns(self):
        api_path = BACKEND_DIR / "api" / "report_audio_workflow.py"
        text = api_path.read_text(encoding="utf-8")
        for function_name, next_route, projection in (
            ("download_audio", "download_result", "report_audio_wav_path"),
            ("download_result", "download_video", "report_audio_output_path"),
            ("download_video", "download_vertical_video", "report_audio_video_path"),
            ("download_cover", "download_keyframe", "report_audio_cover_path"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(f"def {next_route}(", 1)[0]
            self.assertIn("_get_owned_task_reference", body)
            self.assertIn(projection, body)
            self.assertNotIn("task.result", body)

        detail = text.split("def get_job(", 1)[1].split("def get_child_job(", 1)[0]
        self.assertIn("_REPORT_AUDIO_JOB_DETAIL_CACHE", detail)
        self.assertIn("_job_detail_version", detail)

    def test_bilibili_upload_list_never_loads_multiple_payload_rows(self):
        path = BACKEND_DIR / "services" / "bilibili_publish.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def bilibili_upload_children(", 1)[1].split(
            "\ndef bilibili_upload_child_payload", 1
        )[0]
        candidate_query = body.split("for candidate_id in candidate_ids:", 1)[0]
        self.assertIn("db.session.query(PublicationTask.id)", candidate_query)
        self.assertNotIn("PublicationTask.params", candidate_query)
        self.assertNotIn("PublicationTask.result", candidate_query)
        self.assertIn("PublicationTask.id == candidate_id", body)

    def test_report_audio_tts_cache_matches_structured_hash(self):
        task_path = BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        text = task_path.read_text(encoding="utf-8")
        body = text.split("def _completed_tts_task_for_text(", 1)[1].split(
            "\ndef _active_tts_task_for_chunk", 1
        )[0]
        columns = text.split("TASK_TTS_MATCH_COLUMNS = (", 1)[1].split(")", 1)[0]

        self.assertIn("PublicationTask.doubao_tts_text_hash == expected_hash", body)
        self.assertNotIn("PublicationTask.params[", body)
        self.assertNotIn("PublicationTask.result[", body)
        self.assertNotIn("PublicationTask.params", columns)
        self.assertNotIn("PublicationTask.result", columns)

    def test_doubao_api_cache_does_not_scan_payload_json(self):
        api_path = BACKEND_DIR / "api" / "doubao_browser_tts.py"
        text = api_path.read_text(encoding="utf-8")
        body = text.split("def _completed_task_for_text(", 1)[1].split(
            "\ndef _task_text_hash", 1
        )[0]

        self.assertIn("PublicationTask.doubao_tts_text_hash == expected_hash", body)
        self.assertIn("PublicationTask.doubao_tts_wav_path", body)
        self.assertNotIn(".limit(scan_limit)", body)
        self.assertNotIn("for candidate in candidates", body)
        self.assertNotIn("candidate.params", body)
        self.assertNotIn("candidate.result", body)

    def test_publication_tasks_has_runtime_query_guard_and_audit_tool(self):
        middleware_path = BACKEND_DIR / "middleware" / "database_logger.py"
        log_manager_path = BACKEND_DIR / "utils" / "log_manager.py"
        audit_path = PROJECT_ROOT / "scripts" / "dev" / "analyze-publication-task-queries.py"
        middleware = middleware_path.read_text(encoding="utf-8")
        log_manager = log_manager_path.read_text(encoding="utf-8")

        self.assertIn('PUBLICATION_TASK_SLOW_MS", "500"', middleware)
        self.assertIn('categories.append("json_predicate")', middleware)
        self.assertIn('categories.append("multirow_payload")', middleware)
        self.assertIn('categories.append("error")', middleware)
        self.assertIn("publication_tasks_query.log", log_manager)
        self.assertTrue(audit_path.is_file())

    def test_gemini_reuse_uses_structured_chars_before_loading_one_payload(self):
        for relative in (
            "tasks/report_audio_workflow.py",
            "tasks/report_script_material_workflow.py",
        ):
            path = BACKEND_DIR / relative
            text = path.read_text(encoding="utf-8")
            completed = text.split("def _completed_gemini_task(", 1)[1].split(
                "\ndef _active_gemini_task", 1
            )[0]
            active = text.split("def _active_gemini_task(", 1)[1].split(
                "\ndef _valid_gemini_task_result" if relative == "tasks/report_audio_workflow.py" else "\ndef _wait_child",
                1,
            )[0]

            self.assertIn("PublicationTask.gemini_response_chars", completed)
            self.assertIn("db.session.query(PublicationTask.id)", completed)
            self.assertNotIn(".options(load_only(*TASK_CHILD_COLUMNS))", completed)
            self.assertIn("load_only(*TASK_CHILD_POLL_COLUMNS)", active)
            self.assertNotIn("load_only(*TASK_CHILD_COLUMNS)", active)

        gemini_worker = (BACKEND_DIR / "tasks" / "gemini_browser.py").read_text(encoding="utf-8")
        final_update = gemini_worker.split('final_result.update({', 1)[1].split(
            'logger.info("Report PDF Gemini task completed', 1
        )[0]
        self.assertIn("PublicationTask.gemini_response_chars", final_update)

    def test_report_audio_publisher_reuse_uses_structured_match_columns(self):
        path = BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        text = path.read_text(encoding="utf-8")
        child_columns = text.split("TASK_CHILD_COLUMNS = (", 1)[1].split(")", 1)[0]
        match_columns = text.split("TASK_PUBLISHER_MATCH_COLUMNS = (", 1)[1].split(")", 1)[0]
        finder = text.split("def _find_existing_publisher_video_task(", 1)[1].split(
            "\ndef _publisher_child_render_mode", 1
        )[0]

        self.assertNotIn("PublicationTask.params", child_columns)
        self.assertIn("PublicationTask.publisher_render_mode", match_columns)
        self.assertIn("PublicationTask.publisher_landscape_enabled", match_columns)
        self.assertIn("load_only(*TASK_PUBLISHER_MATCH_COLUMNS)", finder)
        self.assertNotIn("load_only(*TASK_CHILD_COLUMNS)", finder)

        existing_child = text.split("def _existing_child_from_result(", 1)[1].split(
            "\ndef _child_id_from_result", 1
        )[0]
        self.assertIn("TASK_PUBLISHER_MATCH_COLUMNS", existing_child)
        self.assertNotIn("TASK_CHILD_COLUMNS", existing_child)

        publisher_stage = text.split('if _video_stage_enabled(payload):', 1)[1].split(
            '\n        else:', 1
        )[0]
        self.assertIn("_load_task(publisher_task_id, *TASK_PUBLISHER_MATCH_COLUMNS)", publisher_stage)
        self.assertNotIn("_load_task(publisher_task_id, *TASK_CHILD_COLUMNS)", publisher_stage)

    def test_report_audio_active_wait_timeout_does_not_load_child_result(self):
        path = BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        text = path.read_text(encoding="utf-8")
        wait_child = text.split("def _wait_child(", 1)[1].split(
            "\ndef _child_inline_wait_seconds", 1
        )[0]
        timeout_branch = wait_child.split("if time.monotonic() >= deadline:", 1)[1].split(
            "raise ChildTaskStillRunning", 1
        )[0]
        self.assertIn("TASK_CHILD_POLL_COLUMNS", timeout_branch)
        self.assertNotIn("TASK_CHILD_COLUMNS", timeout_branch)

        process_job = text.split("def _process_job(", 1)[1].split(
            "\ndef _db_retry_count", 1
        )[0]
        self.assertLess(
            process_job.index("_load_task(task_id, *TASK_RESUME_COLUMNS)"),
            process_job.index("_load_task(task_id, *TASK_PAYLOAD_COLUMNS)"),
        )
        self.assertIn("_workflow_resume_cache", text)
        self.assertIn("waiting_label == \"publisher_video\"", process_job)

    def test_publisher_repair_scans_candidates_without_payload_json(self):
        path = BACKEND_DIR / "services" / "publisher_queue_repair.py"
        text = path.read_text(encoding="utf-8")
        candidate_columns = text.split("PUBLISHER_REPAIR_CANDIDATE_COLUMNS =", 1)[1].split(
            "PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS", 1
        )[0]

        self.assertIn('if column.key not in {"params", "result"}', candidate_columns)
        for function_name, next_name in (
            ("repair_stale_publisher_pending_tasks_once", "cleanup_publisher_children_of_terminal_parents_once"),
            ("repair_stale_publisher_processing_tasks_once", "maybe_requeue_stale_publication_task"),
            ("repair_stale_publication_tasks_once", "maybe_repair_stale_business_processing_task"),
            ("repair_stale_business_processing_tasks_once", "_mark_business_processing_repair_limit_failed"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(f"\ndef {next_name}", 1)[0]
            self.assertIn("db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)", body)
            self.assertIn("_load_repair_task(row.id", body)
            self.assertNotIn("options(load_only(*PUBLISHER_REPAIR_SCAN_COLUMNS))", body)

    def test_report_audio_parent_hot_updates_are_atomic_without_select_before_update(self):
        task_path = BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        text = task_path.read_text(encoding="utf-8")
        record_body = text.split("def _record_child(", 1)[1].split(
            "\ndef _record_child_attempt", 1
        )[0]
        update_body = text.split("def _update_result(", 1)[1].split(
            "\ndef _child_error_message", 1
        )[0]
        emit_body = text.split("def _emit(", 1)[1].split("\ndef _current_step_code", 1)[0]

        for body in (record_body, update_body, emit_body):
            self.assertIn("UPDATE public.publication_tasks", body)
            self.assertNotIn("_load_task(", body)
        self.assertIn("_cached_workflow_result", text)
        self.assertIn("@_workflow_result_scope", text)

    def test_zsxq_default_local_list_does_not_fallback_to_db_raw_payload(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")

        self.assertIn("allow_db_fallback=False", text)
        self.assertIn("include_raw_payload=True", text)
        self.assertIn("_zsxq_image_context", text)

    def test_zsxq_clean_dispatch_reuses_loaded_sync_result(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")
        pdf_body = text.split("def submit_pdf_clean_for_run(", 1)[1].split("\ndef set_run_pdf_clean_result", 1)[0]
        audio_body = text.split("def submit_audio_clean_for_run(", 1)[1].split("\ndef set_run_audio_clean_result", 1)[0]

        self.assertNotIn("ZsxqSyncRun.query.get", text)
        self.assertNotIn("downloaded_pdf_files_for_run(run_id)", pdf_body)
        self.assertIn("_downloaded_pdf_files_from_run_result(run)", pdf_body)
        self.assertNotIn("downloaded_audio_files_for_run(run_id)", audio_body)
        self.assertIn("_downloaded_audio_files_from_run_result(run)", audio_body)

    def test_zsxq_workflow_sync_poll_uses_explicit_projection(self):
        service_path = BACKEND_DIR / "services" / "zsxq_workflow_service.py"
        text = service_path.read_text(encoding="utf-8")

        self.assertNotIn("ZsxqSyncRun.query.get", text)
        self.assertIn("_load_sync_run_state", text)
        self.assertIn("ZSXQ_SYNC_RUN_STATE_COLUMNS", text)

    def test_zsxq_workflow_run_access_uses_projection_helper(self):
        service_path = BACKEND_DIR / "services" / "zsxq_workflow_service.py"
        task_path = BACKEND_DIR / "tasks" / "zsxq_workflow.py"
        service_text = service_path.read_text(encoding="utf-8")
        task_text = task_path.read_text(encoding="utf-8")

        self.assertNotIn("ZsxqWorkflowRun.query.get", service_text)
        self.assertIn("_load_workflow_run", service_text)
        self.assertIn("ZSXQ_WORKFLOW_STATE_COLUMNS", service_text)
        self.assertIn("ZSXQ_WORKFLOW_RUNTIME_COLUMNS", service_text)
        self.assertIn("record_sync_finished_and_advance", task_text)

    def test_report_video_api_report_detail_does_not_default_to_raw_meta(self):
        api_path = BACKEND_DIR / "apis" / "v1" / "report_video.py"
        text = api_path.read_text(encoding="utf-8")
        columns = text.split("REPORT_VIDEO_REPORT_API_COLUMNS = (", 1)[1].split(")", 1)[0]

        self.assertIn("ReportVideoReport.report_code", columns)
        self.assertNotIn("ReportVideoReport.raw_meta", columns)
        self.assertIn("selectinload(ReportVideoTask.reports).load_only(*REPORT_VIDEO_REPORT_API_COLUMNS)", text)

    def test_mp3_child_poll_projection_excludes_json_payloads(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        columns = text.split("TASK_PUBLIC_STATUS_COLUMNS = (", 1)[1].split(")", 1)[0]

        self.assertIn("PublicationTask.status", columns)
        self.assertNotIn("PublicationTask.params", columns)
        self.assertNotIn("PublicationTask.result", columns)

    def test_mp3_hot_result_updates_are_atomic_without_select_before_update(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")

        emit_body = text.split("def _emit(", 1)[1].split("\ndef _update_result", 1)[0]
        update_body = text.split("def _update_result(", 1)[1].split("\ndef _record_child", 1)[0]
        child_body = text.split("def _record_child(", 1)[1].split("\ndef _mark_failed", 1)[0]

        for body in (emit_body, update_body, child_body):
            self.assertIn("UPDATE public.publication_tasks", body)
            self.assertNotIn("_load_task(", body)

        fresh_result_body = text.split("def _fresh_result(", 1)[1].split("\ndef _clear_invalid", 1)[0]
        self.assertIn("_cached_workflow_result", fresh_result_body)
        self.assertIn("@_workflow_result_scope", text)

    def test_mp3_api_owned_task_lookup_does_not_use_query_get(self):
        api_path = BACKEND_DIR / "api" / "mp3_video_workflow.py"
        text = api_path.read_text(encoding="utf-8")
        owned_task_body = text.split("def _get_owned_task(", 1)[1].split("\ndef _download_result_for_owned_task", 1)[0]

        self.assertNotIn(".get(job_id)", owned_task_body)
        self.assertIn("PublicationTask.id == str(job_id)", owned_task_body)

    def test_mp3_bilibili_poll_does_not_load_parent_json(self):
        api_path = BACKEND_DIR / "api" / "mp3_video_workflow.py"
        frontend_path = PROJECT_ROOT / "src" / "frontend" / "js" / "mp3-video-workflow-detail.js"
        api_text = api_path.read_text(encoding="utf-8")
        frontend_text = frontend_path.read_text(encoding="utf-8")
        route_body = api_text.split("def get_bilibili_uploads(", 1)[1].split("\n\n@mp3_video_workflow_bp.route", 1)[0]
        poll_body = frontend_text.split("async refreshBilibiliUploads()", 1)[1].split("\n    async loadBiliCredentials", 1)[0]

        self.assertIn("include_params=False", route_body)
        self.assertIn("include_result=False", route_body)
        self.assertIn("/bilibili-uploads", poll_body)
        self.assertNotIn(
            "this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}`,",
            poll_body,
        )

    def test_material_reuse_batches_candidate_result_loads(self):
        offenders = []
        for relative in (
            "tasks/report_audio_workflow.py",
            "tasks/report_script_material_workflow.py",
        ):
            path = BACKEND_DIR / relative
            text = path.read_text(encoding="utf-8")
            if "_load_task(candidate.id, PublicationTask.result)" in text:
                offenders.append(relative)
            self.assertIn("_load_task_results", text)

        self.assertEqual([], offenders)

    def test_gemini_progress_does_not_reload_result_json(self):
        task_path = BACKEND_DIR / "tasks" / "gemini_browser.py"
        text = task_path.read_text(encoding="utf-8")

        self.assertIn("def _update_active_task", text)
        self.assertIn("nonlocal progress_result", text)
        self.assertNotIn("fresh = _load_task(job_id, *TASK_UPDATE_COLUMNS)", text)

    def test_publisher_state_updates_do_not_select_task_first(self):
        task_path = BACKEND_DIR / "tasks" / "publisher.py"
        text = task_path.read_text(encoding="utf-8")
        body = text.split("def update_task_record", 1)[1].split("def to_abs_path", 1)[0]

        self.assertIn(".update(values, synchronize_session=False)", body)
        self.assertNotIn("_load_task(", body)

    def test_publisher_rejects_stale_messages_before_loading_payload(self):
        task_path = BACKEND_DIR / "tasks" / "publisher.py"
        text = task_path.read_text(encoding="utf-8")
        body = text.split("def run_publication_pipeline(", 1)[1].split(
            "\ndef run_youtube_upload_only", 1
        )[0]

        self.assertLess(
            body.index("_load_task_with_retries(task_id, *TASK_START_COLUMNS)"),
            body.index("_load_task_with_retries(task_id, *TASK_PAYLOAD_COLUMNS)"),
        )
        start_columns = text.split("TASK_START_COLUMNS = (", 1)[1].split(")", 1)[0]
        self.assertNotIn("PublicationTask.params", start_columns)
        self.assertNotIn("PublicationTask.result", start_columns)

    def test_publisher_releases_db_session_before_bilibili_network_upload(self):
        task_path = BACKEND_DIR / "tasks" / "publisher.py"
        text = task_path.read_text(encoding="utf-8")

        chunks = text.split("resolve_bilibili_auth(")[1:]
        self.assertEqual(2, len(chunks))
        for chunk in chunks:
            before_uploader = chunk.split("BiliUploader(", 1)[0]
            self.assertIn("db.session.remove()", before_uploader)


if __name__ == "__main__":
    unittest.main()
