import unittest
import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"


def _python_files(root):
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


class DbProjectionDisciplineTest(unittest.TestCase):
    def test_db_aware_functions_never_publish_directly_to_celery(self):
        offenders = []
        db_names = {"db", "PublicationTask", "ZsxqWorkflowRun", "ZsxqSyncRun"}
        for path in _python_files(BACKEND_DIR):
            if "migrations" in path.parts:
                continue
            try:
                tree = ast.parse(
                    path.read_text(encoding="utf-8", errors="ignore"),
                    filename=str(path),
                )
            except SyntaxError:
                continue
            functions = (
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            for function in functions:
                names = {
                    node.id for node in ast.walk(function)
                    if isinstance(node, ast.Name)
                }
                if not names.intersection(db_names):
                    continue
                for node in ast.walk(function):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "apply_async"
                    ):
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} ({function.name})"
                        )

        self.assertEqual([], offenders)

    def test_celery_publish_never_follows_uncommitted_session_mutation(self):
        offenders = []
        mutation_methods = {
            "add",
            "add_all",
            "flush",
            "bulk_save_objects",
            "bulk_insert_mappings",
            "bulk_update_mappings",
            "execute",
        }
        for path in _python_files(BACKEND_DIR):
            if "migrations" in path.parts:
                continue
            try:
                tree = ast.parse(
                    path.read_text(encoding="utf-8", errors="ignore"),
                    filename=str(path),
                )
            except SyntaxError:
                continue
            functions = (
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            for function in functions:
                mutations = []
                commits = []
                publishes = []
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    owner = node.func.value
                    if (
                        isinstance(owner, ast.Attribute)
                        and isinstance(owner.value, ast.Name)
                        and owner.value.id == "db"
                        and owner.attr == "session"
                    ):
                        if node.func.attr in mutation_methods:
                            mutations.append(node.lineno)
                        elif node.func.attr == "commit":
                            commits.append(node.lineno)
                    if node.func.attr == "apply_async":
                        publishes.append(node.lineno)

                for publish_line in publishes:
                    last_mutation = max(
                        (line for line in mutations if line < publish_line),
                        default=-1,
                    )
                    last_commit = max(
                        (line for line in commits if line < publish_line),
                        default=-1,
                    )
                    if last_mutation > last_commit:
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{publish_line} "
                            f"({function.name}, mutation={last_mutation}, commit={last_commit})"
                        )

        self.assertEqual([], offenders)

    def test_external_document_remote_io_uses_detached_snapshots(self):
        service = (
            BACKEND_DIR / "services" / "external_document_service.py"
        ).read_text(encoding="utf-8")
        fetch = service.split("def fetch_document(", 1)[1].split(
            "\n    def summarize_document",
            1,
        )[0]
        summarize = service.split("def summarize_document(", 1)[1].split(
            "\n    def submit_tts",
            1,
        )[0]
        submit_tts = service.split("def submit_tts(", 1)[1].split(
            "\n    def backfill",
            1,
        )[0]

        self.assertIn("remote_document = _document_remote_snapshot(document)", fetch)
        self.assertLess(
            fetch.index("release_db_session_quietly()"),
            fetch.index("self._fetch_feishu_document(remote_document"),
        )
        self.assertIn("remote_document = _document_remote_snapshot(document)", summarize)
        self.assertLess(
            summarize.index("release_db_session_quietly()"),
            summarize.index("self._deepseek_summary(remote_document)"),
        )
        self.assertIn("apply_async_after_db_release(", submit_tts)
        self.assertNotIn(".apply_async(", submit_tts)

        api = (
            BACKEND_DIR / "apis" / "v1" / "external_documents.py"
        ).read_text(encoding="utf-8")
        submit_backfill = api.split("def submit_external_document_backfill(", 1)[1].split(
            "\n\n@bp.route(\"/backfill/tasks/<task_id>\"",
            1,
        )[0]
        self.assertLess(
            submit_backfill.index("db.session.commit()"),
            submit_backfill.index("apply_async_after_db_release("),
        )
        self.assertNotIn(".apply_async(", submit_backfill)

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

    def test_external_document_shell_lookup_stays_narrow_and_idempotent(self):
        service_path = BACKEND_DIR / "services" / "external_document_service.py"
        model_path = BACKEND_DIR / "models" / "external_document.py"
        service = service_path.read_text(encoding="utf-8")
        model = model_path.read_text(encoding="utf-8")
        body = service.split("def _upsert_document_shells(", 1)[1].split(
            "\n    def _upsert_document_shell(", 1
        )[0]
        scan = service.split("def scan_topic_record(", 1)[1].split(
            "\n    def scan_existing_topics", 1
        )[0]
        lookup = body.split("new_documents = []", 1)[0]

        self.assertIn(".options(load_only(", lookup)
        self.assertIn("ExternalDocument.url_hash.in_(url_hashes)", lookup)
        self.assertIn("ZsxqTopicExternalDocument.url_hash.in_(url_hashes)", scan)
        self.assertNotIn(".filter_by(", scan)
        self.assertNotIn("_upsert_document_shell(link)", scan)
        self.assertIn("if new_documents:", body)
        self.assertIn("db.session.flush()", body)
        self.assertNotIn("document.updated_at =", body)
        self.assertNotIn("content_text", lookup)
        self.assertNotIn("raw_payload", lookup)
        self.assertIn(
            'db.UniqueConstraint("url_hash", name="uq_external_documents_url_hash")',
            model,
        )
        self.assertNotIn(
            "url_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)",
            model,
        )

    def test_external_document_single_shell_wrapper_reuses_batch_lookup(self):
        service_path = BACKEND_DIR / "services" / "external_document_service.py"
        service = service_path.read_text(encoding="utf-8")
        body = service.split("def _upsert_document_shell(", 1)[1].split(
            "\n    def _documents_by_id", 1
        )[0]

        self.assertIn("self._upsert_document_shells((link,))", body)

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
        reference_columns = text.split("OWNED_TASK_REFERENCE_COLUMNS = (", 1)[1].split(
            ")", 1
        )[0]
        for function_name, next_route, projection in (
            ("download_audio", "download_result", "report_audio_wav_path"),
            ("download_result", "download_video", "report_audio_output_path"),
            ("download_video", "download_vertical_video", "report_audio_video_path"),
            ("download_cover", "download_keyframe", "report_audio_cover_path"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(f"def {next_route}(", 1)[0]
            self.assertIn("_cached_owned_media_path", body)
            self.assertIn("_get_owned_asset_reference", body)
            self.assertTrue(projection in body or projection in reference_columns)
            self.assertNotIn("task.result", body)

        media_cache = text.split("def _cached_owned_media_path(", 1)[1].split(
            "\n\n@report_audio_workflow_bp.route", 1
        )[0]
        self.assertIn("_REPORT_AUDIO_MEDIA_PATH_CACHE", media_cache)
        self.assertIn("REPORT_AUDIO_MEDIA_PATH_CACHE_TTL_SECONDS", media_cache)

        detail = text.split("def get_job(", 1)[1].split("def get_child_job(", 1)[0]
        self.assertIn("_REPORT_AUDIO_JOB_DETAIL_CACHE", detail)
        self.assertIn("_job_detail_version", detail)

        media_lookup = text.split("def _get_owned_media_task(", 1)[1].split(
            "\ndef _get_owned_keyframe_task", 1
        )[0]
        self.assertIn("report_audio_vertical_cover_path", reference_columns)
        self.assertIn("report_audio_podcast_cover_path", reference_columns)
        self.assertIn("report_audio_bilibili_cover_path", reference_columns)
        self.assertNotIn("PublicationTask.result", media_lookup)

        for function_name, next_function in (
            ("download_vertical_cover", "download_podcast_cover"),
            ("download_podcast_cover", "download_bilibili_cover"),
            ("download_bilibili_cover", "download_cover"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(
                f"def {next_function}(", 1
            )[0]
            self.assertIn("_get_owned_media_task", body)
            self.assertNotIn("task.result", body)

    def test_report_script_material_hot_progress_updates_are_atomic(self):
        path = BACKEND_DIR / "tasks" / "report_script_material_workflow.py"
        text = path.read_text(encoding="utf-8")
        update_body = text.split("def _update_result(", 1)[1].split(
            "\ndef _normalize_for_tts_with_retries", 1
        )[0]
        emit_body = text.split("def _emit(", 1)[1].split(
            "\ndef _clear_waiting_child", 1
        )[0]

        for body in (update_body, emit_body):
            postgres_branch = body.split("if _is_postgresql_session():", 1)[1].split(
                "return", 1
            )[0]
            self.assertIn("UPDATE public.publication_tasks", postgres_branch)
            self.assertNotIn("_load_task(", postgres_branch)

    def test_bilibili_upload_list_never_loads_multiple_payload_rows(self):
        path = BACKEND_DIR / "services" / "bilibili_publish.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def bilibili_upload_children(", 1)[1].split(
            "\ndef bilibili_upload_child_payload", 1
        )[0]
        self.assertIn("PublicationTaskBilibiliUpload", body)
        self.assertIn(".join(", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("PublicationTask.result", body)
        self.assertNotIn("for candidate_id", body)

        payload = text.split("def bilibili_upload_child_payload(", 1)[1].split(
            "\ndef cancel_bilibili_upload_child", 1
        )[0]
        self.assertIn("_charging_pay_from_projection", payload)
        self.assertNotIn("task.params", payload)
        self.assertNotIn("task.result", payload)

        api_path = BACKEND_DIR / "api" / "mp3_video_workflow.py"
        api_text = api_path.read_text(encoding="utf-8")
        submit = api_text.split("def submit_bilibili_upload(", 1)[1].split(
            "\ndef cancel_bilibili_upload", 1
        )[0]
        cancel = api_text.split("def cancel_bilibili_upload(", 1)[1].split(
            "\ndef cancel_job", 1
        )[0]
        self.assertIn("_clear_job_detail_cache(job_id)", submit)
        self.assertIn("_clear_job_detail_cache(job_id)", cancel)
        self.assertNotIn("_clear_job_detail_cache(task.id)", submit)
        self.assertNotIn("_clear_job_detail_cache(task.id)", cancel)

    def test_bilibili_worker_executes_from_structured_projection(self):
        task_path = BACKEND_DIR / "tasks" / "publisher.py"
        text = task_path.read_text(encoding="utf-8")
        body = text.split("def run_bilibili_upload_only(", 1)[1]

        self.assertIn("_load_bilibili_upload_context_with_retries", body)
        self.assertIn("result_patch=result_patch", body)
        self.assertNotIn("TASK_PAYLOAD_COLUMNS", body)
        self.assertNotIn("task.params", body)
        self.assertNotIn("task.result", body)
        self.assertNotIn("bili_params", body)

        service_path = BACKEND_DIR / "services" / "bilibili_publish.py"
        service_text = service_path.read_text(encoding="utf-8")
        context = service_text.split("def bilibili_upload_execution_context(", 1)[1].split(
            "\ndef enqueue_bilibili_upload_task", 1
        )[0]
        self.assertIn("PublicationTaskBilibiliUpload", context)
        self.assertNotIn("PublicationTask.params", context)
        self.assertNotIn("PublicationTask.result", context)

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
        pending_repair = text.split("def repair_stale_publisher_pending_tasks_once(", 1)[1].split(
            "\ndef cleanup_publisher_children_of_terminal_parents_once", 1
        )[0]
        self.assertIn("stale_before = datetime.utcnow() - timedelta", pending_repair)
        self.assertIn("PublicationTask.updated_at <= stale_before", pending_repair)
        self.assertLess(
            pending_repair.index("PublicationTask.updated_at <= stale_before"),
            pending_repair.index("_load_repair_task(row.id"),
        )
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
        for column in (
            "report_audio_vertical_cover_path",
            "report_audio_podcast_cover_path",
            "report_audio_bilibili_cover_path",
        ):
            self.assertIn(column, update_body)
        self.assertIn("_cached_workflow_result", text)
        self.assertIn("@_workflow_result_scope", text)

    def test_zsxq_default_local_list_does_not_fallback_to_db_raw_payload(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")

        self.assertIn("allow_db_fallback=False", text)
        self.assertIn("include_raw_payload=True", text)
        self.assertIn("_zsxq_image_context", text)

    def test_zsxq_scheduler_pending_run_check_uses_narrow_projection(self):
        service_path = BACKEND_DIR / "services" / "zsxq_system_sync_test.py"
        text = service_path.read_text(encoding="utf-8")
        body = text.split("def _pending_system_sync_work(", 1)[1].split(
            "\ndef _recover_stale_sync_runs", 1
        )[0]
        projection = body.split(".with_entities(", 1)[1].split(")\n            .filter", 1)[0]

        self.assertIn('ZsxqSyncRun.id.label("id")', projection)
        self.assertIn('ZsxqSyncRun.status.label("status")', projection)
        self.assertIn('ZsxqSyncRun.updated_at.label("updated_at")', projection)
        self.assertNotIn("ZsxqSyncRun.requested_tabs", projection)
        self.assertNotIn("ZsxqSyncRun.result", projection)

    def test_zsxq_clean_dispatch_uses_structured_run_download_ledger(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")
        pdf_body = text.split("def submit_pdf_clean_for_run(", 1)[1].split("\ndef set_run_pdf_clean_result", 1)[0]
        audio_body = text.split("def submit_audio_clean_for_run(", 1)[1].split("\ndef set_run_audio_clean_result", 1)[0]

        self.assertNotIn("ZsxqSyncRun.query.get", text)
        self.assertNotIn("downloaded_pdf_files_for_run(run_id)", pdf_body)
        self.assertIn('_downloaded_files_for_run(run, {"pdf"})', pdf_body)
        self.assertIn("_content_pipeline_run_for_submission(run_id, run_context)", pdf_body)
        self.assertNotIn("downloaded_audio_files_for_run(run_id)", audio_body)
        self.assertIn("_downloaded_files_for_run(run, AUTO_VIDEO_AUDIO_EXTENSIONS)", audio_body)
        self.assertIn("_content_pipeline_run_for_submission(run_id, run_context)", audio_body)

        downloaded_files = text.split("def _downloaded_files_for_run(", 1)[1].split(
            "\n    def _downloaded_pdf_files_from_run_result", 1
        )[0]
        self.assertIn("ZsxqSyncRunDownload.run_id", downloaded_files)
        self.assertIn("ZsxqSyncRunDownload.file_ext", downloaded_files)
        self.assertIn('getattr(run, "download_ledger_complete", False)', downloaded_files)
        self.assertNotIn("ZsxqSyncRunDownload.file_ext.in_", downloaded_files)

        merge_body = text.split("def _merge_run_content_pipeline_result(", 1)[1].split(
            "\n    def _patch_run_content_pipeline_result", 1
        )[0]
        self.assertIn('getattr(run, "_zsxq_cached_result", None)', merge_body)
        self.assertIn("_patch_run_content_pipeline_result", merge_body)
        self.assertNotIn("_update_run_progress_result", merge_body)
        self.assertNotIn("db.session.commit()", merge_body)
        publish_context = text.split("PUBLISH_TASK_CONTEXT_COLUMNS = (", 1)[1].split(")", 1)[0]
        self.assertIn("PublicationTask.feature", publish_context)
        self.assertIn("PublicationTask.source_task_id", publish_context)
        self.assertIn("PublicationTask.updated_at", publish_context)

        download_loop = text.split("def _download_topic_attachments(", 1)[1].split(
            "\n    def _download_attachment_file", 1
        )[0]
        download_update = text.split("def _update_attachment_download_state(", 1)[1].split(
            "\n    def _upsert_cursor", 1
        )[0]
        self.assertIn('record.error_message != reason', download_loop)
        self.assertIn('record.local_path != existing_path', download_loop)
        self.assertIn("is_distinct_from(value)", download_update)

        workflow_path = BACKEND_DIR / "services" / "zsxq_workflow_service.py"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        clean_stage = workflow_text.split("def _submit_clean_stage(", 1)[1].split(
            "\n    def _submit_one_clean_task", 1
        )[0]
        clean_refresh = workflow_text.split("def _refresh_clean_stage(", 1)[1].split(
            "\n    def _submit_publish_stage", 1
        )[0]
        self.assertIn("run_context = {}", clean_stage)
        self.assertEqual(clean_stage.count("run_context=run_context"), 2)
        self.assertIn("WORKFLOW_TASK_STATE_COLUMNS", clean_refresh)
        self.assertNotIn("WORKFLOW_CLEAN_TASK_COLUMNS", clean_refresh)
        self.assertNotIn("PublicationTask.result", clean_refresh)

    def test_zsxq_completion_consumers_reuse_one_run_result_snapshot(self):
        asset_path = BACKEND_DIR / "services" / "report_video_asset_service.py"
        task_path = BACKEND_DIR / "tasks" / "zsxq_sync.py"
        asset_text = asset_path.read_text(encoding="utf-8")
        task_text = task_path.read_text(encoding="utf-8")

        from_run = asset_text.split("def _candidate_items_from_run(", 1)[1].split(
            "\n    def _candidate_items_from_date", 1
        )[0]
        task_name = asset_text.split("def _task_name(", 1)[1].split(
            "\n    def _summary", 1
        )[0]
        self.assertNotIn("db.session.get(ZsxqSyncRun", asset_text)
        self.assertIn('cleanable.get("run")', from_run)
        self.assertIn('normalized.get("_run_label")', task_name)
        self.assertIn("_compact_sync_delivery_result", task_text)
        self.assertIn("countdown=delay_seconds", task_text)

    def test_content_pipeline_submission_never_reuses_expired_task_entity(self):
        for name in ("pdf_clean_task_service.py", "audio_clean_task_service.py"):
            text = (BACKEND_DIR / "services" / "content_pipeline" / name).read_text(encoding="utf-8")
            self.assertIn("payload = task.to_dict()", text)
            self.assertIn("task_id=task_id", text)
            after_commit = text.split("db.session.commit()", 1)[1]
            self.assertNotIn("task.to_dict()", after_commit)
            self.assertNotIn("task=task", after_commit)

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

    def test_report_video_parse_does_not_reuse_expired_full_row(self):
        task_path = BACKEND_DIR / "tasks" / "report_video.py"
        text = task_path.read_text(encoding="utf-8")
        body = text.split("def parse_report_pdf_task(", 1)[1].split(
            "\n\n@celery_app.task", 1
        )[0]
        after_start_commit = body.split("db.session.remove()", 1)[1]

        self.assertIn("load_only(*REPORT_VIDEO_REPORT_PARSE_COLUMNS)", body)
        self.assertIn("_refresh_task_parse_status_by_id(task_key)", body)
        self.assertNotIn("ReportVideoReport.query.get", body)
        self.assertNotIn("_refresh_task_parse_status(report.task)", body)
        self.assertNotIn("report.", after_start_commit)

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

        self.assertNotIn("result = jsonb_set", emit_body)
        self.assertNotIn("CAST(:patch AS jsonb)", emit_body)
        self.assertNotIn("CAST(:entry AS jsonb)", emit_body)

        fresh_result_body = text.split("def _fresh_result(", 1)[1].split("\ndef _clear_invalid", 1)[0]
        self.assertIn("_cached_workflow_result", fresh_result_body)
        self.assertIn("@_workflow_result_scope", text)

    def test_mp3_notifications_reuse_runtime_payload_snapshot(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        body = text.split("def _queue_notification(", 1)[1].split(
            "\ndef _send_direct_mp3_workflow_failure_email", 1
        )[0]
        status_columns = text.split("TASK_NOTIFICATION_STATUS_COLUMNS = (", 1)[1].split(
            ")", 1
        )[0]

        self.assertNotIn("PublicationTask.params", status_columns)
        self.assertNotIn("PublicationTask.result", status_columns)
        self.assertIn("_cached_workflow_params(task_id)", body)
        self.assertIn("_cached_workflow_result(task_id)", body)
        self.assertIn("*TASK_NOTIFICATION_STATUS_COLUMNS", body)
        self.assertIn("jsonb_build_object", body)

    def test_mp3_retry_and_terminal_paths_do_not_reload_parent_result(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        retry = text.split("def _record_retry_attempt(", 1)[1].split(
            "\ndef _is_retryable_gemini_error", 1
        )[0]
        process_job = text.split(
            "def process_job(task_id: str, wait_hint: Optional[Dict[str, Any]] = None) -> bool:", 1
        )[1].split(
            "\ndef _generate_gemini_with_retries", 1
        )[0]

        self.assertNotIn("_load_task(", retry)
        self.assertIn("_update_result(task_id", retry)
        self.assertNotIn("*TASK_RESULT_COLUMNS, PublicationTask.status", process_job)
        self.assertEqual(2, process_job.count("_without_waiting_state(_fresh_result(task_key))"))
        self.assertLess(process_job.index("task_key = task.id"), process_job.index("if not audio_path"))

    def test_mp3_wait_continuations_do_not_reload_json_payloads(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        task_path = BACKEND_DIR / "tasks" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        task_text = task_path.read_text(encoding="utf-8")
        resume = text.split("def _resume_waiting_child_lightweight(", 1)[1].split(
            "\n\n@_workflow_result_scope", 1
        )[0]
        wait_child = text.split("def _wait_child(", 1)[1].split(
            "\ndef _publisher_child_repair_due", 1
        )[0]
        deadline = wait_child.split("child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)")[-1]
        defer = text.split("def _defer_parent_retry(", 1)[1].split(
            "\ndef _is_publisher_video_label", 1
        )[0]

        self.assertIn("*TASK_WAITING_STATUS_COLUMNS", resume)
        self.assertIn("waiting_child_updated_at == expected_token", resume)
        self.assertIn("args=[str(task_id), next_hint]", resume)
        self.assertNotIn("*TASK_WORKFLOW_COLUMNS", resume)
        disk_recovery = resume.split("if disk_video:", 1)[1].split(
            "if child and child.status not in TERMINAL_STATUSES and repair_due:",
            1,
        )[0]
        self.assertNotIn("*TASK_PUBLIC_COLUMNS", disk_recovery)
        self.assertNotIn("*TASK_PUBLIC_COLUMNS", deadline.split("if child_status in TERMINAL_STATUSES", 1)[0])
        self.assertIn("args=[parent_id, waiting_child]", defer)
        self.assertIn("wait_hint=None", task_text)
        self.assertLess(
            resume.index("parent_progress = int(parent.progress or 0)"),
            resume.index("maybe_requeue_stale_publisher_child"),
        )

        recovery = text.split("def _recover_completed_publisher_child_from_disk(", 1)[1].split(
            "\ndef _publisher_child_disk_video_path",
            1,
        )[0]
        postgres_recovery = recovery.split('bind.dialect.name == "postgresql"', 1)[1].split(
            "return recovered",
            1,
        )[0]
        self.assertIn("UPDATE public.publication_tasks", postgres_recovery)
        self.assertIn("COALESCE(result, '{}'::jsonb)", postgres_recovery)
        self.assertNotIn("dict(child.result", postgres_recovery)

    def test_mp3_list_reads_are_bounded_cached_and_degrade_to_stale_data(self):
        text = (BACKEND_DIR / "api" / "mp3_video_workflow.py").read_text(encoding="utf-8")
        list_jobs = text.split("def list_jobs(current_user):", 1)[1].split(
            "\n\n@mp3_video_workflow_bp.route(\"/jobs/<job_id>\"",
            1,
        )[0]

        self.assertIn("_cached_job_list(cache_key)", list_jobs)
        self.assertIn("_set_job_list_statement_timeout()", list_jobs)
        self.assertIn("func.count(PublicationTask.id)", list_jobs)
        self.assertNotIn(".paginate(", list_jobs)
        self.assertIn("allow_stale=True", list_jobs)
        self.assertIn('"database_busy"', list_jobs)

    def test_runtime_guard_postgres_defer_does_not_select_result_payload(self):
        text = (BACKEND_DIR / "services" / "runtime_guard.py").read_text(encoding="utf-8")
        active_count = text.split("def _publication_task_active_count(", 1)[1].split(
            "\n\ndef _passive_running_steps",
            1,
        )[0]
        postgres_defer = text.split("def _mark_publication_task_deferred_postgresql(", 1)[1].split(
            "\n\ndef ",
            1,
        )[0]

        self.assertIn("func.count(PublicationTask.id)", active_count)
        self.assertNotIn("query.count()", active_count)
        self.assertIn("UPDATE public.publication_tasks", postgres_defer)
        self.assertIn("jsonb_array_elements", postgres_defer)
        self.assertIn("LIMIT 19", postgres_defer)

    def test_mp3_duplicate_cleanup_does_not_load_each_payload(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        cleanup = text.split("def _cancel_duplicate_publisher_tasks(", 1)[1].split(
            "\ndef _revoke_celery_task", 1
        )[0]

        self.assertNotIn("_publisher_payload_tasks", text)
        self.assertIn("UPDATE public.publication_tasks", cleanup)
        self.assertNotIn("task.params", cleanup)
        self.assertNotIn("task.result", cleanup)

    def test_mp3_child_flow_does_not_reuse_expired_orm_payloads(self):
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        text = service_path.read_text(encoding="utf-8")
        start_task = text.split("def _start_task(", 1)[1].split("\ndef _emit", 1)[0]
        wait_child = text.split("def _wait_child(", 1)[1].split(
            "\ndef _publisher_child_repair_due", 1
        )[0]
        sync_title = text.split("def _sync_publisher_video_task_title(", 1)[1].split(
            "\ndef _find_existing_publisher_video_task", 1
        )[0]
        create_child = text.split("def create_publisher_video_task(", 1)[1].split(
            "\ndef _publisher_video_title", 1
        )[0]

        after_start_commit = start_task.split("db.session.commit()", 1)[1]
        self.assertIn("task_id = str(task.id)", start_task)
        self.assertNotIn("task.id", after_start_commit)
        self.assertIn("child_summary = _task_public_summary(child)", wait_child)
        self.assertIn("_record_child(parent_id, label, child_summary)", wait_child)
        self.assertIn("if disk_video or repair_due:", wait_child)
        self.assertNotIn("db.session.commit()", sync_title)
        self.assertIn("db.session.flush()", create_child)
        self.assertIn("child_id = str(child.id)", create_child)

    def test_mp3_api_owned_task_lookup_does_not_use_query_get(self):
        api_path = BACKEND_DIR / "api" / "mp3_video_workflow.py"
        text = api_path.read_text(encoding="utf-8")
        owned_task_body = text.split("def _get_owned_task(", 1)[1].split("\ndef _download_result_for_owned_task", 1)[0]

        self.assertNotIn(".get(job_id)", owned_task_body)
        self.assertIn("PublicationTask.id == str(job_id)", owned_task_body)
        self.assertIn("PublicationTask.updated_at", owned_task_body)

        detail = text.split("def get_job(", 1)[1].split(
            "\n\n@mp3_video_workflow_bp.route(\"/jobs/<job_id>/bilibili-uploads\"", 1
        )[0]
        lightweight_lookup = "include_params=False,\n            include_result=False"
        self.assertIn("_set_job_detail_statement_timeout()", detail)
        self.assertIn(lightweight_lookup, detail)
        self.assertLess(detail.index(lightweight_lookup), detail.index("task = _get_owned_task(current_user"))
        self.assertIn("_cached_job_detail(cache_key, version=version)", detail)
        self.assertIn("allow_stale=True", detail)
        self.assertIn("database_busy", detail)

    def test_mp3_list_and_publisher_reuse_do_not_batch_json_payloads(self):
        api_path = BACKEND_DIR / "api" / "mp3_video_workflow.py"
        service_path = BACKEND_DIR / "services" / "mp3_video_workflow.py"
        api_text = api_path.read_text(encoding="utf-8")
        service_text = service_path.read_text(encoding="utf-8")

        list_columns = api_text.split("JOB_LIST_COLUMNS = (", 1)[1].split(")", 1)[0]
        self.assertIn("PublicationTask.mp3_audio_filename", list_columns)
        self.assertIn("PublicationTask.mp3_audio_path", list_columns)
        self.assertNotIn("PublicationTask.params", list_columns)
        self.assertNotIn("PublicationTask.result", list_columns)

        audio_summary = api_text.split("def _audio_summary_from_task(", 1)[1].split(
            "\ndef _job_payload", 1
        )[0]
        self.assertIn("task.mp3_audio_filename", audio_summary)
        self.assertNotIn("task.params", audio_summary)
        self.assertNotIn("task.result", audio_summary)

        match_columns = service_text.split("TASK_PUBLISHER_MATCH_COLUMNS = (", 1)[1].split(
            ")", 1
        )[0]
        self.assertIn("PublicationTask.publisher_child_role", match_columns)
        self.assertIn("PublicationTask.publisher_audio_signature", match_columns)
        self.assertNotIn("PublicationTask.params", match_columns)
        self.assertNotIn("PublicationTask.result", match_columns)

        finder = service_text.split("def _find_existing_publisher_video_task(", 1)[1].split(
            "\ndef _publisher_child_role", 1
        )[0]
        self.assertIn("load_only(*TASK_PUBLISHER_MATCH_COLUMNS)", finder)
        self.assertIn("PublicationTask.publisher_child_role == role", finder)
        self.assertIn("PublicationTask.source_task_id == parent_id", finder)
        self.assertNotIn("PublicationTask.parent_task_id == parent_id", finder)
        self.assertNotIn("load_only(*TASK_PUBLIC_COLUMNS)", finder)

        process_job = service_text.split(
            "def process_job(task_id: str, wait_hint: Optional[Dict[str, Any]] = None) -> bool:", 1
        )[1].split(
            "\ndef _generate_gemini_with_retries", 1
        )[0]
        after_start = process_job.split("_start_task(task)", 1)[1]
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])task\.id\b", after_start))

        self.assertNotIn("load_json_fallback_rows", api_text)
        bilibili_children = api_text.split("def _bilibili_upload_children(", 1)[1].split(
            "\ndef _bilibili_upload_child_payload", 1
        )[0]
        self.assertIn("shared_bilibili_upload_children", bilibili_children)
        self.assertNotIn("PublicationTask.params", bilibili_children)
        self.assertNotIn("PublicationTask.result", bilibili_children)
        self.assertNotIn("db.session.query", bilibili_children)

    def test_material_child_wait_throttles_parent_status_polling(self):
        path = BACKEND_DIR / "tasks" / "report_script_material_workflow.py"
        text = path.read_text(encoding="utf-8")
        wait_body = text.split("def _wait_child(", 1)[1].split("\ndef _complete_material_task", 1)[0]

        self.assertIn("REPORT_SCRIPT_MATERIAL_PARENT_POLL_SECONDS", wait_body)
        self.assertIn("if now >= next_parent_check:", wait_body)
        self.assertIn("next_parent_check = now + parent_poll_seconds", wait_body)

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

    def test_report_workflow_child_creation_does_not_reload_json_after_commit(self):
        material_text = (
            BACKEND_DIR / "tasks" / "report_script_material_workflow.py"
        ).read_text(encoding="utf-8")
        audio_text = (
            BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        ).read_text(encoding="utf-8")

        material_create = material_text.split("def _create_gemini_task(", 1)[1].split(
            "\ndef _reusable_material_for_pdf", 1
        )[0]
        self.assertIn("child_snapshot = _task_snapshot(child)", material_create)
        self.assertIn("_persist_child_celery_task_id(child_id, async_result.id)", material_create)
        self.assertIn("return child_snapshot", material_create)
        self.assertNotIn("_load_task(child_id, *TASK_CHILD_COLUMNS)", material_create)

        audio_boundaries = (
            ("def _create_gemini_task(", "\ndef _manual_gemini_payload"),
            ("def _create_doubao_tts_task(", "\ndef _doubao_queue_name"),
            ("def _create_publisher_video_task(", "\ndef _find_existing_publisher_video_task"),
        )
        for start, end in audio_boundaries:
            with self.subTest(create_function=start):
                create_body = audio_text.split(start, 1)[1].split(end, 1)[0]
                self.assertIn("SimpleNamespace(**_task_state_snapshot(child))", create_body)
                self.assertIn("_persist_child_celery_task_id(child_id, async_result.id)", create_body)
                self.assertIn("return child_snapshot", create_body)
                self.assertNotIn("_load_task(child_id, *TASK_CHILD_COLUMNS)", create_body)

        for source in (material_text, audio_text):
            helper = source.split("def _persist_child_celery_task_id(", 1)[1].split(
                "\ndef ", 1
            )[0]
            self.assertIn("synchronize_session=False", helper)
            self.assertNotIn("_load_task(", helper)

    def test_stick_figure_asset_poll_reuses_initialized_structure(self):
        path = BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py"
        text = path.read_text(encoding="utf-8")
        ensure = text.split("def _ensure_scene_assets(", 1)[1].split(
            "\ndef _scene_structure_cache_get", 1
        )[0]
        get_assets = text.split("def get_scene_assets(", 1)[1].split(
            "\n\n@report_stick_figure_video_workflow_bp.route", 1
        )[0]
        submit_images = text.split("def submit_scene_images(", 1)[1].split(
            "\n\n@report_stick_figure_video_workflow_bp.route", 1
        )[0]
        bundle = text.split("def _scene_asset_bundle(", 1)[1]

        self.assertLess(ensure.index("_scene_structure_cache_get"), ensure.index("_completed_storyboard"))
        self.assertIn("assets = _sync_tts_assets(assets)", get_assets)
        self.assertLess(
            get_assets.index("_cached_legacy_scene_bundle(parent_snapshot)"),
            get_assets.index("_ensure_scene_assets(parent)"),
        )
        self.assertNotIn("assets = _scene_assets(parent_snapshot.id, storyboard_id)", get_assets)
        self.assertIn("force_refresh=True", submit_images)
        self.assertIn("_scene_segments_by_id(storyboard.id, segment_ids)", bundle)
        self.assertIn("_cache_scene_media_assets(assets)", bundle)

        scene_assets = text.split("def _scene_assets(", 1)[1].split(
            "\ndef _latest_video_task", 1
        )[0]
        self.assertIn("load_only(*SCENE_ASSET_COLUMNS)", scene_assets)
        status_assets = text.split("def _scene_asset_status_rows(", 1)[1].split(
            "\ndef _latest_video_task", 1
        )[0]
        status_bundle = text.split("def _scene_asset_status_bundle(", 1)[1].split(
            "\ndef _scene_asset_state_item", 1
        )[0]
        self.assertIn("load_only(*SCENE_ASSET_STATUS_COLUMNS)", status_assets)
        self.assertNotIn("SCENE_ASSET_COLUMNS", status_assets)
        self.assertNotIn("_scene_segments_by_id", status_bundle)
        self.assertNotIn("_cache_scene_media_assets", status_bundle)
        status_payload = text.split("def _scene_asset_status_payload(", 1)[1].split(
            "\ndef _latest_video_task", 1
        )[0]
        self.assertNotIn("_sync_tts_assets", status_payload)

        submit_tts = text.split("def submit_scene_tts(", 1)[1].split(
            "\n\n@report_stick_figure_video_workflow_bp.route", 1
        )[0]
        prepare_tts = text.split("def _prepare_tts_asset(", 1)[1].split(
            "\ndef _enqueue_tts_assets", 1
        )[0]
        self.assertIn("segments = _scene_segments_by_id(", submit_tts)
        self.assertIn("scene_count=scene_count", submit_tts)
        self.assertNotIn("db.session.get(ReportVisualPlanSegment", prepare_tts)
        self.assertNotIn("ReportStickFigureSceneAsset.query.filter_by", prepare_tts)
        self.assertIn('"chunk_total": int(asset.scene_count or 0)', prepare_tts)

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
        self.assertIn('values["publisher_bilibili_cover_path"]', body)
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

    def test_platform_credential_markers_update_without_loading_secret_payload(self):
        path = BACKEND_DIR / "services" / "platform_credential_service.py"
        text = path.read_text(encoding="utf-8")
        writer = text.split("def _update_credential_state(", 1)[1].split(
            "\ndef mark_credential_success", 1
        )[0]
        success = text.split("def mark_credential_success(", 1)[1].split(
            "\ndef mark_credential_error", 1
        )[0]
        error = text.split("def mark_credential_error(", 1)[1].split(
            "\ndef is_auth_error", 1
        )[0]

        self.assertIn(".update(", writer)
        self.assertIn("exc.connection_invalidated", writer)
        for body in (writer, success, error):
            self.assertNotIn(".query.get(", body)
            self.assertNotIn("encrypted_payload", body)

    def test_platform_credential_resolution_snapshots_before_commit(self):
        path = BACKEND_DIR / "services" / "platform_credential_service.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def _resolve_credential_record_uncached(", 1)[1].split(
            "\ndef resolve_credential_records", 1
        )[0]

        self.assertLess(body.index("account_snapshot = SimpleNamespace"), body.index("db.session.commit()"))
        after_commit = body.split("db.session.commit()", 1)[1]
        self.assertIn("account_snapshot.encrypted_payload", after_commit)
        self.assertNotIn("account.encrypted_payload", after_commit)

    def test_explicit_platform_credential_resolution_cache_is_bounded_and_invalidated(self):
        path = BACKEND_DIR / "services" / "platform_credential_service.py"
        text = path.read_text(encoding="utf-8")
        wrapper = text.split("def resolve_credential_record(", 1)[1].split(
            "\ndef _resolve_credential_record_uncached", 1
        )[0]

        self.assertIn("if not account_id", wrapper)
        self.assertIn("time.monotonic()", wrapper)
        self.assertIn("_CREDENTIAL_RESOLUTION_CACHE_LOCK", wrapper)
        self.assertIn("_resolve_credential_record_uncached", wrapper)
        self.assertIn("_invalidate_credential_resolution_cache(saved_account_id)", text)
        self.assertIn("_invalidate_credential_resolution_cache(deleted_account_id)", text)

    def test_local_auth_bypass_cache_refresh_is_single_flight(self):
        path = BACKEND_DIR / "utils" / "auth_utils.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def _get_local_bypass_user():", 1)[1].split(
            "\ndef get_current_user", 1
        )[0]

        lock_index = body.index("with _DEV_FALLBACK_USER_CACHE_LOCK:")
        cache_index = body.index("_get_cached_dev_fallback_user()")
        query_index = body.index("_light_user_query()")
        store_index = body.index("_cache_dev_fallback_user(admin_user)")
        self.assertLess(lock_index, cache_index)
        self.assertLess(cache_index, query_index)
        self.assertLess(query_index, store_index)
        self.assertIn("RLock", text)
        self.assertIn('_float_env("DEV_FALLBACK_USER_CACHE_TTL", 300)', text)

    def test_zsxq_workflow_schema_check_is_one_compact_query(self):
        path = BACKEND_DIR / "services" / "zsxq_workflow_service.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def ensure_schema(self):", 1)[1].split(
            "\n    def create_system_workflow", 1
        )[0]

        self.assertEqual(1, body.count("db.session.execute("))
        self.assertIn("information_schema.columns", body)
        self.assertIn("jsonb_agg(column_name ORDER BY ordinal_position)", body)
        self.assertNotIn("COALESCE(ARRAY(", body)
        self.assertNotIn("inspect(", body)
        self.assertNotIn("get_columns(", body)

        for function_name, next_name in (
            ("_find_existing_system_workflow", "_find_existing_manual_workflow"),
            ("_find_existing_manual_workflow", "_commit_and_payload"),
        ):
            lookup = text.split(f"def {function_name}(", 1)[1].split(
                f"\n    def {next_name}", 1
            )[0]
            self.assertIn("db.session.query(ZsxqWorkflowRun.id)", lookup)
            self.assertNotIn("ZsxqWorkflowRun.query\n            .filter", lookup.split("if not existing:", 1)[0])

    def test_new_zsxq_workflow_is_enqueued_from_created_payload(self):
        workflow_path = BACKEND_DIR / "services" / "zsxq_workflow_service.py"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        enqueue_body = workflow_text.split("def enqueue_workflow_task(", 1)[1].split(
            "\n    def start_workflow", 1
        )[0]

        self.assertIn("workflow_payload=None", enqueue_body)
        self.assertIn("ZsxqWorkflowRun.stage_results: stage_results", enqueue_body)
        self.assertIn("synchronize_session=False", enqueue_body)

        for relative_path in (
            "apis/v1/zsxq.py",
            "services/zsxq_system_sync_test.py",
        ):
            source = (BACKEND_DIR / relative_path).read_text(encoding="utf-8")
            self.assertIn("workflow_payload=workflow", source)

    def test_browser_publish_children_extract_json_keys_server_side(self):
        path = BACKEND_DIR / "services" / "browser_publish_jobs.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def browser_publish_children(", 1)[1].split(
            "\ndef cancel_browser_publish_task", 1
        )[0]

        self.assertIn("params[\"title\"].as_string()", body)
        self.assertIn("PublicationTask.result[\"publish_status\"].as_string()", body)
        self.assertNotIn("PublicationTask.params.label", body)
        self.assertNotIn("PublicationTask.result.label", body)
        self.assertNotIn("browser_publish_payload", body)

    def test_zsxq_hot_writes_do_not_reload_full_json_rows(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        task_path = BACKEND_DIR / "tasks" / "zsxq_sync.py"
        service_text = service_path.read_text(encoding="utf-8")
        task_text = task_path.read_text(encoding="utf-8")

        comments = service_text.split("def _upsert_topic_comments(", 1)[1].split(
            "\n    def _find_existing_downloaded_attachment", 1
        )[0]
        terminal = task_text.split("def _terminal_delivery_result(", 1)[1].split(
            "\ndef _is_retryable_upstream_error", 1
        )[0]

        self.assertIn("load_only(", comments)
        self.assertIn("ZsxqTopicComment.comment_id", comments)
        self.assertNotIn("getattr(record, key)", comments)
        self.assertNotIn("db.session.get(ZsxqAttachment", service_text)
        self.assertIn("ZsxqSyncRun.status.label", terminal)
        self.assertNotIn("db.session.get(", terminal)

        run_id_helper = service_text.split("def _cached_sync_run_id(", 1)[1].split(
            "\n    def _update_run_progress_result", 1
        )[0]
        topic_create = service_text.split("def _create_topic_record(", 1)[1].split(
            "\n    def _update_existing_topic", 1
        )[0]
        topic_update = service_text.split("def _update_existing_topic(", 1)[1].split(
            "\n    def _upsert_attachments", 1
        )[0]
        self.assertIn('getattr(run, "__dict__", {}).get("id")', run_id_helper)
        self.assertNotIn('getattr(run, "id"', run_id_helper)
        self.assertNotIn("run.id", topic_create)
        self.assertNotIn("run.id", topic_update)

    def test_zsxq_auto_script_material_reuse_is_one_lightweight_row(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")
        body = text.split("def _find_existing_auto_script_material_task(", 1)[1].split(
            "\n    def _is_script_material_only_report_audio_task", 1
        )[0]

        self.assertIn(".options(load_only(", body)
        self.assertIn("PublicationTask.status.in_", body)
        self.assertIn(".first()", body)
        self.assertNotIn(".all()", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("PublicationTask.result", body)

    def test_zsxq_commit_loops_keep_payload_rows_out_of_implicit_refreshes(self):
        service_path = BACKEND_DIR / "services" / "zsxq_sync_service.py"
        text = service_path.read_text(encoding="utf-8")

        auto_video = text.split("def _submit_auto_video_dispatch_for_run(", 1)[1].split(
            "\n    def _submit_auto_script_material_dispatch_for_run", 1
        )[0]
        auto_script = text.split("def _submit_auto_script_material_dispatch_for_run(", 1)[1].split(
            "\n    def _new_auto_video_dispatch_summary", 1
        )[0]
        for body in (auto_video, auto_script):
            self.assertIn(".with_entities(*AUTO_DISPATCH_ATTACHMENT_COLUMNS)", body)
            self.assertIn("run = SimpleNamespace(", body)
            self.assertNotIn("ZsxqAttachment.raw_payload", body)

        download = text.split("def _download_topic_attachments(", 1)[1].split(
            "\n    def _download_attachment_file", 1
        )[0]
        self.assertIn(".with_entities(*ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS)", download)
        self.assertIn("_load_attachment_download_payload(record_id)", download)
        self.assertIn("fresh_payloads.get(str(record.attachment_key))", download)
        self.assertNotIn("record.raw_payload", download)

        topic_update = text.split("def _update_existing_topic(", 1)[1].split(
            "\n    def _load_existing_attachment_snapshots", 1
        )[0]
        self.assertIn("update(ZsxqTopic)", topic_update)
        self.assertIn("synchronize_session=False", topic_update)
        self.assertNotIn("getattr(record", topic_update)

    def test_zsxq_publish_does_not_reuse_expired_task_after_commit(self):
        path = BACKEND_DIR / "tasks" / "zsxq_publish.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def _run_publish_clean_output_task_impl(", 1)[1].split(
            "\ndef _update_attachment_publish_status", 1
        )[0]

        self.assertIn("_update_task_fields(", body)
        self.assertNotIn("task = _load_task(task_key", body)
        self.assertNotIn("db.session.expunge(task)", body)
        self.assertIn("_update_attachment_publish_status(_attachment_publish_snapshot(", body)
        self.assertIn("_notify_workflow_run(workflow_run_id, task_key)", body)
        self.assertNotIn("_update_attachment_publish_status(task_key)", body)

        after_publish = body.split("publish_result = publish_clean_output(", 1)[1]
        self.assertNotIn("_load_task(", after_publish)
        self.assertNotIn("task.result", after_publish)

    def test_report_audio_retry_snapshots_response_before_commit(self):
        path = BACKEND_DIR / "api" / "report_audio_workflow.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def retry_job(", 1)[1].split("\ndef _get_owned_task", 1)[0]

        first_commit = body.index("db.session.commit()")
        self.assertLess(body.index("payload = _job_payload(task)"), first_commit)
        after_commit = body[first_commit:]
        self.assertNotIn("_job_payload(task)", after_commit)
        self.assertIn("args=[task_key]", after_commit)
        self.assertIn("task_id=task_key", after_commit)
        self.assertNotIn("task=task", after_commit)
        self.assertNotIn("task.celery_task_id", after_commit)

    def test_media_projection_repair_uses_the_real_text_task_id_type(self):
        path = PROJECT_ROOT / "scripts" / "maintenance" / "repair_publication_task_media_projections.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn("WHERE id = ANY(%s::text[])", text)
        self.assertNotIn("%s::uuid[]", text)
        self.assertIn("report_audio_video_path", text)
        self.assertIn("report_audio_cover_path", text)
        self.assertIn("report_audio_landscape_keyframe_path", text)

    def test_optimized_video_sync_uses_configured_redis_and_db_side_snapshot_insert(self):
        base = (BACKEND_DIR / "config" / "base.py").read_text(encoding="utf-8")
        path = BACKEND_DIR / "services" / "optimized_video_sync_service.py"
        text = path.read_text(encoding="utf-8")
        snapshot = text.split(
            "def _create_video_data_records_batch(",
            1,
        )[1].split("\n    def _is_recent_sync", 1)[0]

        self.assertIn("os.environ.get('REDIS_URL')", base)
        self.assertIn("redis.Redis.from_url(", text)
        self.assertIn("BaseConfig.REDIS_URL", text)
        self.assertIn("pg_advisory_xact_lock", snapshot)
        self.assertIn("INSERT INTO public.video_data", snapshot)
        self.assertIn("NOT EXISTS", snapshot)
        self.assertNotIn("VideoData.query", snapshot)
        self.assertNotIn("bulk_insert_mappings", snapshot)
        self.assertIn("_sync_state_lock = threading.Lock()", text)
        self.assertIn("OPTIMIZED_VIDEO_SYNC_MAX_WORKERS", text)

    def test_report_audio_core_writers_project_landscape_keyframe(self):
        path = BACKEND_DIR / "tasks" / "report_audio_workflow.py"
        text = path.read_text(encoding="utf-8")
        update_body = text.split("def _update_result(", 1)[1].split(
            "\ndef _record_child",
            1,
        )[0]

        self.assertIn("report_audio_landscape_keyframe_path", update_body)
        self.assertIn(
            "{video,keyframes,landscape_cover,path}",
            update_body,
        )
        self.assertIn(
            "PublicationTask.report_audio_landscape_keyframe_path",
            text,
        )

    def test_stick_figure_latest_video_lookup_never_reads_result_json(self):
        path = BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py"
        text = path.read_text(encoding="utf-8")
        columns = text.split("VIDEO_TASK_COLUMNS = (", 1)[1].split(")", 1)[0]
        latest = text.split("def _latest_video_task(", 1)[1].split(
            "\ndef _video_task_by_id", 1
        )[0]
        video_file = text.split("def video_file(", 1)[1].split(
            "\ndef _owned_parent", 1
        )[0]

        self.assertNotIn("PublicationTask.result", columns)
        self.assertNotIn("PublicationTask.params", columns)
        self.assertNotIn("PublicationTask.result", latest)
        self.assertNotIn("_latest_video_task", video_file)
        self.assertNotIn("_video_task_result", video_file)
        self.assertIn("video_output_path(job_id)", video_file)
        self.assertIn("subtitle_output_path(job_id)", video_file)
        self.assertIn("db.session.query(PublicationTask.result)", text)

    def test_stick_figure_enqueue_loops_snapshot_orm_rows_before_commit(self):
        path = BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py"
        text = path.read_text(encoding="utf-8")

        for function_name, next_name in (
            ("submit_scene_images", "submit_scene_tts"),
            ("submit_scene_tts", "retry_scene_image"),
            ("submit_video", "get_video"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(
                f"\ndef {next_name}", 1
            )[0]
            self.assertIn("parent_snapshot = _scene_parent_snapshot(parent)", body)

        image_body = text.split("def submit_scene_images(", 1)[1].split(
            "\ndef submit_scene_tts", 1
        )[0]
        tts_body = text.split("def submit_scene_tts(", 1)[1].split(
            "\ndef retry_scene_image", 1
        )[0]
        self.assertIn("assets = [_scene_asset_enqueue_snapshot(asset) for asset in assets]", image_body)
        self.assertIn("_scene_asset_enqueue_snapshot(", tts_body)
        self.assertIn("for asset in assets", tts_body)

        enqueue_image = text.split("def _enqueue_image_asset(", 1)[1].split(
            "\ndef _prepare_tts_asset",
            1,
        )[0]
        first_commit = enqueue_image.index("db.session.commit()")
        self.assertLess(enqueue_image.index("ReportStickFigureSceneAsset.image_task_id: task_id"), first_commit)
        self.assertLess(enqueue_image.index("celery_task_id=broker_task_id"), first_commit)
        self.assertLess(first_commit, enqueue_image.index("maybe_guarded_apply_async("))
        self.assertIn("broker_task_id=broker_task_id", enqueue_image)
        self.assertIn('ReportStickFigureSceneAsset.image_status: "failed"', enqueue_image)

        after_image_commit = enqueue_image.split("db.session.commit()", 1)[1]
        self.assertNotIn("parent.", after_image_commit)
        self.assertNotIn("asset.", after_image_commit)

        enqueue_tts_batch = text.split("def _enqueue_tts_assets(", 1)[1].split(
            "\ndef _enqueue_tts_asset",
            1,
        )[0]
        first_commit = enqueue_tts_batch.index("db.session.commit()")
        self.assertLess(enqueue_tts_batch.index("db.session.flush()"), first_commit)
        self.assertLess(enqueue_tts_batch.index("bulk_update_mappings("), first_commit)
        self.assertLess(first_commit, enqueue_tts_batch.index("maybe_guarded_apply_async("))
        self.assertIn("broker_task_id=item[\"broker_task_id\"]", enqueue_tts_batch)
        self.assertIn("_invalidate_scene_media_cache(prepared[0][\"parent_id\"])", enqueue_tts_batch)
        self.assertIn("eligible.append(asset)", tts_body)
        self.assertIn("_enqueue_tts_assets(parent_snapshot, eligible)", tts_body)

        enqueue_tts_one = text.split("def _enqueue_tts_asset(", 1)[1].split(
            "\ndef _sync_tts_assets",
            1,
        )[0]
        self.assertIn("_enqueue_tts_assets(parent, [asset])", enqueue_tts_one)

    def test_stick_figure_asset_access_reuses_narrow_owner_snapshot(self):
        path = BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py"
        text = path.read_text(encoding="utf-8")
        columns = text.split("SCENE_PARENT_COLUMNS = (", 1)[1].split(")", 1)[0]
        body = text.split("def _owned_parent(", 1)[1].split(
            "\ndef _scene_parent_snapshot", 1
        )[0]

        self.assertNotIn("PublicationTask.params", columns)
        self.assertNotIn("PublicationTask.result", columns)
        self.assertIn("load_only(*SCENE_PARENT_COLUMNS)", body)
        self.assertIn("_scene_parent_access_cache.get(cache_key)", body)
        self.assertIn("visible_user_ids", body)
        self.assertIn("threading.RLock()", text)

        asset_body = text.split("def _owned_scene_asset(", 1)[1].split(
            "\ndef _enqueue_image_asset", 1
        )[0]
        self.assertIn("fresh=False", asset_body)
        self.assertIn("load_only(*SCENE_MEDIA_ASSET_COLUMNS)", asset_body)
        self.assertIn("_scene_media_workflow_cache", asset_body)
        self.assertIn("_cache_scene_media_assets(assets)", asset_body)

        retry_image = text.split("def retry_scene_image(", 1)[1].split(
            "\n\n@report_stick_figure_video_workflow_bp.route", 1
        )[0]
        retry_tts = text.split("def retry_scene_tts(", 1)[1].split(
            "\n\n@report_stick_figure_video_workflow_bp.route", 1
        )[0]
        self.assertIn("fresh=True", retry_image)
        self.assertIn("fresh=True", retry_tts)

    def test_content_pipeline_progress_writes_do_not_reload_result_json(self):
        path = BACKEND_DIR / "tasks" / "content_pipeline.py"
        text = path.read_text(encoding="utf-8")

        for function_name, next_name in (
            ("_run_pdf_clean_task_impl_guarded", "run_audio_clean_task"),
            ("_run_audio_clean_task_impl_guarded", "_run_content_pipeline_task_with_guard"),
        ):
            body = text.split(f"def {function_name}(", 1)[1].split(f"def {next_name}(", 1)[0]
            callback = body.split("def update_progress(event):", 1)[1].split(
                "result = adapter.run_batch(", 1
            )[0]
            self.assertIn("_update_task_progress(", callback)
            self.assertNotIn("_load_task(", callback)
            self.assertNotIn("fresh.result", callback)
            self.assertIn("batch_id=task_key", body)

        update_fields = text.split("def _update_task_fields(", 1)[1].split(
            "\ndef _update_task_progress", 1
        )[0]
        self.assertIn(".update(update_values, synchronize_session=False)", update_fields)
        self.assertNotIn("_load_task(", update_fields)

        enqueue = text.split("def _enqueue_auto_publish_task(", 1)[1].split(
            "\ndef _notify_workflow_from_pipeline_task", 1
        )[0]
        self.assertIn("task_id", enqueue)
        self.assertIn("source_task_id=task_key", enqueue)
        self.assertNotIn("task.result", enqueue)
        self.assertNotIn("task.id", enqueue)

    def test_browser_progress_callbacks_do_not_rewrite_result_json(self):
        for relative in ("tasks/doubao_browser_tts.py", "tasks/gemini_browser.py"):
            text = (BACKEND_DIR / relative).read_text(encoding="utf-8")
            callback = text.split("def update_progress(event):", 1)[1].split("\n    try:", 1)[0]

            self.assertIn("PublicationTask.current_step", callback, relative)
            self.assertIn("PublicationTask.progress", callback, relative)
            self.assertNotIn("PublicationTask.result", callback, relative)

            terminal = text.split("def update_progress(event):", 1)[1]
            self.assertTrue(
                "fresh.result = final_result" in terminal
                or "PublicationTask.result: final_result" in terminal,
                relative,
            )

    def test_report_audio_hot_reads_have_bounded_timeout_and_no_window_count(self):
        text = (BACKEND_DIR / "api" / "report_audio_workflow.py").read_text(encoding="utf-8")
        page_query = text.split("def _query_jobs_page(", 1)[1].split(
            "\n\n@report_audio_workflow_bp.route(\"/jobs\"",
            1,
        )[0]
        list_jobs = text.split("def list_jobs(", 1)[1].split(
            "\n\n@report_audio_workflow_bp.route(\"/jobs/<job_id>\"",
            1,
        )[0]
        stats = text.split("def pdf_asset_workflow_stats(", 1)[1].split(
            "\n\n@report_audio_workflow_bp.route(\"/pdf-assets/<asset_id>/preview\"",
            1,
        )[0]

        self.assertNotIn(".over()", page_query)
        self.assertIn("func.count(PublicationTask.id)", page_query)
        self.assertIn("_set_hot_read_statement_timeout()", list_jobs)
        self.assertIn("_stale_success_payload", list_jobs)
        self.assertIn("_set_hot_read_statement_timeout()", stats)
        self.assertIn("_stale_success_payload", stats)

    def test_report_audio_polling_is_single_flight(self):
        text = (PROJECT_ROOT / "src" / "frontend" / "js" / "report-audio-workflow-test.js").read_text(
            encoding="utf-8"
        )
        body = text.split("async refreshJobs() {", 1)[1].split(
            "\n    async refreshJob(",
            1,
        )[0]

        self.assertIn("if (this.pollInFlight || this.historyLoading) return", body)
        self.assertIn("this.pollInFlight = true", body)
        self.assertIn("if (this.jobDate && this.jobDate < today) return", body)
        self.assertIn("finally", body)
        self.assertIn("this.pollInFlight = false", body)

    def test_material_history_polling_stops_when_all_jobs_are_terminal(self):
        text = (PROJECT_ROOT / "src" / "frontend" / "js" / "report-script-material-workflow-test.js").read_text(
            encoding="utf-8"
        )
        body = text.split("resetPollTimer() {", 1)[1].split("\n    render() {", 1)[0]

        self.assertIn("const hasActiveJob", body)
        self.assertIn("if (hasActiveJob) this.loadHistory", body)
        self.assertNotIn("\n            this.loadHistory", body)

    def test_stick_figure_progress_does_not_refresh_expired_asset(self):
        text = (BACKEND_DIR / "tasks" / "report_stick_figure_assets.py").read_text(encoding="utf-8")
        body = text.split("def _generate_scene_image(", 1)[1].split(
            "\ndef _update_progress",
            1,
        )[0]
        after_first_commit = body.split(
            '_update_progress(task_id, asset_id, "prepare_browser", 3)',
            1,
        )[1]

        self.assertIn("load_only(", body)
        self.assertIn("ReportStickFigureSceneAsset.image_task_id == task_id", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("task.params", body)
        self.assertNotIn("db.session.get(ReportStickFigureSceneAsset", body)
        self.assertIn("update_business_task=False", body)
        self.assertNotIn("asset.", after_first_commit)

    def test_stick_figure_asset_polling_is_single_flight_and_rate_limited(self):
        text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")
        schedule = text.split("schedulePolling() {", 1)[1].split(
            "\n    async pollStoryboard()",
            1,
        )[0]
        poll_assets = text.split("async pollAssets() {", 1)[1].split(
            "\n    async generateStoryboard",
            1,
        )[0]

        self.assertIn("setTimeout(()=>this.pollAssets(),10000)", schedule)
        self.assertIn("await this.loadAssets(true,true)", poll_assets)
        self.assertIn("this.schedulePolling()", poll_assets)
        self.assertIn("view=status", text)
        self.assertIn("mergeAssetStatus(result.data)", text)
        html = (
            PROJECT_ROOT / "src" / "frontend" / "report-stick-figure-video-workflow-detail.html"
        ).read_text(encoding="utf-8")
        self.assertIn("lightweight-status-poll-v2", html)

    def test_retention_cleanup_filters_structured_candidates_before_payload_reads(self):
        text = (
            BACKEND_DIR / "services" / "content_pipeline" / "retention_cleanup_service.py"
        ).read_text(encoding="utf-8")
        body = text.split("def _protected_paths(", 1)[1].split(
            "\n    def _is_protected",
            1,
        )[0]
        candidate_query = body.split("task_rows = (", 1)[1].split("for row in task_rows", 1)[0]

        self.assertIn("PublicationTask.feature.in_(RETENTION_PROTECTED_TASK_FEATURES)", candidate_query)
        self.assertIn("PublicationTask.content_clean_output_dir", candidate_query)
        self.assertNotIn("PublicationTask.params", candidate_query)
        self.assertNotIn("PublicationTask.result", candidate_query)
        self.assertNotIn('PublicationTask.params["feature"]', body)
        self.assertIn("PublicationTask.id == row.id", body)

    def test_business_repair_releases_session_before_celery_inspection(self):
        text = (BACKEND_DIR / "services" / "publisher_queue_repair.py").read_text(encoding="utf-8")
        body = text.split("def maybe_requeue_stale_publication_task(", 1)[1].split(
            "\ndef repair_stale_publication_tasks_once",
            1,
        )[0]

        self.assertLess(
            body.index("_release_repair_db_session()"),
            body.index("_celery_message_visible("),
        )
        self.assertIn("_release_repair_db_session()\n        active_logger.warning", body)
        after_publish = body.split("async_result = submit_result[\"async_result\"]", 1)[1]
        self.assertNotIn("_load_repair_task(", after_publish)
        self.assertIn("PublicationTask.updated_at == snapshot.updated_at", after_publish)
        self.assertIn("compare_and_swap.update({", after_publish)

    def test_image_queue_repair_uses_long_grace_and_never_loads_params(self):
        text = (BACKEND_DIR / "services" / "publisher_queue_repair.py").read_text(encoding="utf-8")
        scan_columns = text.split("PUBLISHER_REPAIR_SCAN_COLUMNS = (", 1)[1].split(")", 1)[0]
        repair_loop = text.split("def repair_stale_publication_tasks_once(", 1)[1].split(
            "\ndef maybe_repair_stale_business_processing_task",
            1,
        )[0]

        self.assertNotIn("PublicationTask.params", scan_columns)
        self.assertIn('"report_stick_figure_image": 1800', text)
        self.assertIn("_pending_repair_grace_seconds(row.feature)", repair_loop)

    def test_publisher_parent_cleanup_uses_indexable_terminal_parent_subquery(self):
        path = BACKEND_DIR / "services" / "publisher_queue_repair.py"
        text = path.read_text(encoding="utf-8")
        body = text.split("def cleanup_publisher_children_of_terminal_parents_once(", 1)[1].split(
            "\ndef maybe_repair_stale_publisher_processing_task", 1
        )[0]

        self.assertIn("terminal_parent_ids", body)
        self.assertIn("PublicationTask.parent_task_id.in_(terminal_parent_ids)", body)
        self.assertIn("PublicationTask.source_task_id.in_(terminal_parent_ids)", body)
        self.assertNotIn("func.coalesce", body)
        self.assertNotIn("PublicationTask.query", body)


if __name__ == "__main__":
    unittest.main()
