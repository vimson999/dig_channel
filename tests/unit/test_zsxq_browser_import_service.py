import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.zsxq_browser_import_service import (  # noqa: E402
    BrowserImportValidationError,
    BrowserSidebarImportService,
    IdempotencyConflictError,
)
from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import (  # noqa: E402
    ZsxqAttachment,
    ZsxqBrowserSubmission,
    ZsxqSyncRun,
    ZsxqSyncRunDownload,
    ZsxqTopic,
)


class ZsxqBrowserImportServiceTest(unittest.TestCase):
    def _service_and_downloads(self):
        tempdir = TemporaryDirectory()
        downloads = Path(tempdir.name) / "Downloads"
        downloads.mkdir()
        return tempdir, downloads, BrowserSidebarImportService(downloads_root=downloads)

    def _write_manifest(self, downloads, name, payload):
        path = downloads / "inbox" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _write_history_file(self, downloads, name, payload):
        path = downloads / "知识星球下载" / "inbox" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _history_entry(index, *, duplicate_suffix=False, count=11):
        suffix = " (1)" if duplicate_suffix else ""
        return {
            "filename": f"调研-{index:02d}{suffix}.mp3",
            "media_type": "audio/mpeg",
            "download_count": count,
            "published_at": f"2026-08-17T{index % 20:02d}:00:00+08:00",
            "source_url": f"https://wx.zsxq.com/topic/{index}",
            "downloaded_at": "2026-08-17T08:00:00+08:00",
            "success": True,
        }

    def test_recent_download_history_collapses_overlapping_result_and_submit_files(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        first_run = [self._history_entry(index, count=11) for index in range(13)]
        second_run = [
            self._history_entry(
                index,
                duplicate_suffix=index < 13,
                count=20 if index < 13 else 12,
            )
            for index in range(30)
        ]
        self._write_history_file(
            downloads,
            "sidebar-result-mp3-20260817.json",
            {"kind": "mp3", "files": first_run},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-104149.json",
            {
                "run_id": "sidebar-mp3-20260817T104149+0800",
                "kind": "mp3",
                "files": second_run,
            },
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-pdf-20260817-104149.json",
            {
                "run_id": "sidebar-pdf-20260817T104149+0800",
                "kind": "pdf",
                "files": [{"filename": "不应混入.pdf", "success": True}],
            },
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T12:00:00+08:00"),
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["summary"]["source_files"], 2)
        self.assertEqual(result["summary"]["records_seen"], 43)
        self.assertEqual(result["summary"]["unique_downloads"], 30)
        self.assertEqual(result["summary"]["duplicates_collapsed"], 13)
        self.assertEqual(len(result["files"]), 30)
        first = next(
            item for item in result["files"] if item["source_url"].endswith("/0")
        )
        self.assertEqual(first["filename"], "调研-00.mp3")
        self.assertEqual(first["download_count_at_download"], 11)
        self.assertEqual(first["latest_download_count"], 20)

    def test_recent_download_history_reports_malformed_recent_json_as_incomplete(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        path = (
            downloads / "知识星球下载" / "inbox" / "sidebar-submit-pdf-20260816-broken.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken-json", encoding="utf-8")

        result = service.recent_download_history(
            "pdf",
            days=3,
            now=datetime.fromisoformat("2026-08-17T12:00:00+08:00"),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["summary"]["source_files"], 1)
        self.assertEqual(result["summary"]["records_seen"], 0)
        self.assertEqual(result["errors"][0]["code"], "history_file_invalid")
        self.assertEqual(result["files"], [])

    def test_recent_download_history_fails_closed_when_receipts_cannot_be_queried(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-run-abcdef123456.json",
            {"kind": "mp3", "files": [self._history_entry(1)]},
        )

        def unavailable(_paths):
            raise RuntimeError("database unavailable")

        service._accepted_submission_manifest_paths = unavailable
        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T12:00:00+08:00"),
            require_submission_receipt=True,
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["files"], [])
        self.assertEqual(result["errors"][0]["code"], "history_receipt_lookup_failed")
        self.assertEqual(result["summary"]["trusted_source_files"], 0)

    def test_recent_download_history_applies_later_duplicate_observations(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        original = self._history_entry(1, count=11)
        observation = {
            **original,
            "download_count": 25,
            "latest_download_count": 25,
            "last_seen_at": "2026-08-17T12:00:00+08:00",
        }
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-080000.json",
            {"kind": "mp3", "files": [original]},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-120000.json",
            {"kind": "mp3", "files": [], "history_observations": [observation]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )

        self.assertEqual(result["summary"]["records_seen"], 2)
        self.assertEqual(result["summary"]["unique_downloads"], 1)
        self.assertEqual(result["files"][0]["download_count_at_download"], 11)
        self.assertEqual(result["files"][0]["latest_download_count"], 25)
        self.assertEqual(
            result["files"][0]["last_seen_at"], "2026-08-17T12:00:00+08:00"
        )

    def test_recent_history_collapses_equivalent_iso_timestamp_serializations(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        original = self._history_entry(1, count=11)
        original["published_at"] = "2026-08-17T13:39+08:00"
        duplicate = {**original, "published_at": "2026-08-17T13:39:00+08:00"}
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-080000.json",
            {"kind": "mp3", "files": [original]},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-120000.json",
            {"kind": "mp3", "files": [duplicate]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )

        self.assertEqual(result["summary"]["records_seen"], 2)
        self.assertEqual(result["summary"]["unique_downloads"], 1)
        self.assertEqual(result["summary"]["duplicates_collapsed"], 1)
        self.assertEqual(
            result["files"][0]["published_at_canonical"],
            "2026-08-17T05:39:00Z",
        )

    def test_recent_history_ignores_source_url_variants_for_fixed_tab_identity(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        original = self._history_entry(2, count=11)
        duplicate = {
            **original,
            "source_url": "https://wx.zsxq.com/tags/%E8%B0%83%E7%A0%94%E7%BA%AA%E8%A6%81/88844545452542",
            "download_count": 20,
        }
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-080000.json",
            {"kind": "mp3", "files": [original]},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-120000.json",
            {"kind": "mp3", "files": [duplicate]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )

        self.assertEqual(result["summary"]["unique_downloads"], 1)
        self.assertEqual(result["files"][0]["latest_download_count"], 20)

    def test_recent_history_treats_same_recent_filename_as_downloaded_even_if_time_differs(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        original = self._history_entry(4, count=11)
        duplicate = {
            **original,
            "published_at": "2026-08-18T04:00:00+08:00",
            "download_count": 22,
        }
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-080000.json",
            {"kind": "mp3", "files": [original]},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260818-080000.json",
            {"kind": "mp3", "files": [duplicate]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-18T09:00:00+08:00"),
        )

        self.assertEqual(result["summary"]["unique_downloads"], 1)
        self.assertEqual(result["summary"]["duplicates_collapsed"], 1)
        self.assertEqual(result["files"][0]["latest_download_count"], 22)

    def test_recent_history_normalizes_chrome_duplicate_suffix_and_unicode_filename(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        original = {
            **self._history_entry(3, count=11),
            "filename": "从算力租赁到Token工厂260817.mp3",
        }
        duplicate = {
            **original,
            "filename": "从算力租赁到ＴＯＫＥＮ工厂260817 (1).MP3",
            "download_count": 21,
        }
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-080000.json",
            {"kind": "mp3", "files": [original]},
        )
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-120000.json",
            {"kind": "mp3", "files": [duplicate]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )

        self.assertEqual(result["summary"]["unique_downloads"], 1)
        self.assertEqual(
            result["files"][0]["normalized_filename"],
            "从算力租赁到token工厂260817.mp3",
        )
        self.assertTrue(result["files"][0]["dedupe_key"].startswith("metadata:"))

    def test_recent_download_history_does_not_create_download_from_observation_only(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        observation = self._history_entry(9, count=22)
        self._write_history_file(
            downloads,
            "sidebar-submit-mp3-20260817-120000.json",
            {"kind": "mp3", "files": [], "history_observations": [observation]},
        )

        result = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["summary"]["records_seen"], 1)
        self.assertEqual(result["summary"]["unique_downloads"], 0)
        self.assertEqual(result["files"], [])

    def test_inline_manifest_persists_empty_run_atomically_and_deterministically(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = {
            "run_id": "sidebar-mp3-20260817T120000+0800",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "scan_summary": {"scanned_topics": 40, "new_downloads": 0},
            "files": [],
        }

        canonical, payload_hash = service.canonical_manifest_payload(manifest)
        first_path = service.persist_inline_manifest(canonical, payload_hash)
        second_path = service.persist_inline_manifest(canonical, payload_hash)

        self.assertEqual(first_path, second_path)
        self.assertTrue(first_path.is_file())
        self.assertEqual(json.loads(first_path.read_text(encoding="utf-8")), canonical)
        self.assertIn(
            "sidebar-submit-mp3-20260817-sidebar-mp3-20260817T120000_0800",
            first_path.name,
        )
        self.assertIn(payload_hash[:12], first_path.name)
        self.assertEqual(list(first_path.parent.glob("*.tmp")), [])
        history = service.recent_download_history(
            "mp3",
            days=1,
            now=datetime.fromisoformat("2026-08-17T13:00:00+08:00"),
        )
        self.assertEqual(history["summary"]["source_files"], 1)

    def test_inline_manifest_enforces_scope_limit_and_strict_thresholds(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)

        def manifest(kind, count, *, tab_id=None, files_count=1):
            config = {
                "mp3": ("88844545452542", "调研纪要", "mp3", "audio/mpeg"),
                "pdf": ("51184248544214", "外资研报", "pdf", "pdf"),
            }[kind]
            return {
                "run_id": f"sidebar-{kind}-20260817T120000+0800",
                "kind": kind,
                "group_id": "28888112822211",
                "tab_id": tab_id or config[0],
                "tab_label": config[1],
                "files": [
                    {
                        "filename": f"文件-{index}.{config[2]}",
                        "media_type": config[3],
                        "download_count": count,
                        "success": True,
                    }
                    for index in range(files_count)
                ],
            }

        for payload in (
            manifest("mp3", 10),
            manifest("pdf", 30),
            manifest("mp3", 11, tab_id="51184248544214"),
            manifest("pdf", 31, files_count=41),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(BrowserImportValidationError):
                    service.canonical_manifest_payload(payload)

        self.assertEqual(
            service.canonical_manifest_payload(manifest("mp3", 11))[0]["kind"], "mp3"
        )
        self.assertEqual(
            service.canonical_manifest_payload(manifest("pdf", 31))[0]["kind"], "pdf"
        )

    def test_inline_manifest_validates_history_observations(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        base = {
            "run_id": "sidebar-mp3-20260817T120000+0800",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "files": [],
        }

        invalid_observations = (
            "not-a-list",
            [{}],
            [{"filename": "错误类型.pdf", "download_count": 31}],
            [{"filename": "历史.mp3", "download_count": True}],
            [{"filename": f"历史-{index}.mp3"} for index in range(501)],
        )
        for observations in invalid_observations:
            with self.subTest(observations_type=type(observations).__name__):
                with self.assertRaises(BrowserImportValidationError):
                    service.canonical_manifest_payload(
                        {**base, "history_observations": observations}
                    )

        canonical, _digest = service.canonical_manifest_payload(
            {
                **base,
                "history_observations": [
                    {
                        "filename": "历史.mp3",
                        "download_count": 25,
                        "last_seen_at": "2026-08-17T12:00:00+08:00",
                        "success": True,
                    }
                ],
            }
        )
        self.assertEqual(canonical["history_observations"][0]["download_count"], 25)

    def test_inline_manifest_normalizes_inconsistent_candidate_failure_audit_counts(self):
        tempdir, _downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        base = {
            "run_id": "sidebar-mp3-20260819T120000+0800",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "files": [],
            "candidate_failures": [
                {
                    "expected_filename": "候选-a.mp3",
                    "failure_stage": "detail_file_mismatch_after_2s",
                },
                {
                    "expected_filename": "候选-b.mp3",
                    "failure_stage": "detail_file_mismatch_after_2s",
                },
                {
                    "expected_filename": "候选-c.mp3",
                    "failure_stage": "detail_preview_close_failed",
                },
            ],
        }

        invalid_summaries = (
            {
                "candidate_failure_count": 1,
                "detail_file_mismatch_after_2s": 2,
                "detail_preview_close_failed": 0,
            },
            {
                "candidate_failure_count": 3,
                "detail_file_mismatch_after_2s": 0,
                "detail_preview_close_failed": 1,
            },
        )
        for scan_summary in invalid_summaries:
            with self.subTest(scan_summary=scan_summary):
                canonical, _digest = service.canonical_manifest_payload(
                    {**base, "scan_summary": scan_summary}
                )
                self.assertEqual(
                    canonical["scan_summary"]["candidate_failure_count"],
                    3,
                )
                self.assertEqual(
                    canonical["scan_summary"]["detail_file_mismatch_after_2s"],
                    2,
                )
                self.assertEqual(
                    canonical["scan_summary"]["detail_preview_close_failed"],
                    1,
                )
                self.assertIn(
                    "candidate_failure_count_audit_normalized",
                    canonical["scan_summary"],
                )

        canonical, _digest = service.canonical_manifest_payload(
            {
                **base,
                "scan_summary": {
                    "candidate_failure_count": 3,
                    "detail_file_mismatch_after_2s": 2,
                    "detail_preview_close_failed": 1,
                },
            }
        )
        self.assertEqual(canonical["scan_summary"]["candidate_failure_count"], 3)

    def test_inline_manifest_normalizes_terminal_candidate_classification(self):
        tempdir, _downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)

        cases = (
            {
                "kind": "mp3",
                "tab_id": "88844545452542",
                "tab_label": "调研纪要",
                "within": 5,
                "low_field": "skipped_download_count_not_over_10",
                "low": 1,
                "history": 1,
                "abort_latch": True,
                "expected": {
                    "candidate_terminal_failure_count": 1,
                    "candidates_not_processed_due_success_limit": 0,
                    "candidates_not_processed_due_global_abort": 2,
                    "candidates_not_processed_due_execution_deadline": 0,
                    "candidate_terminal_classification_total": 5,
                },
            },
            {
                "kind": "pdf",
                "tab_id": "51184248544214",
                "tab_label": "外资研报",
                "within": 4,
                "low_field": "skipped_download_count_not_over_30",
                "low": 1,
                "history": 0,
                "abort_latch": False,
                "expected": {
                    "candidate_terminal_failure_count": 1,
                    "candidates_not_processed_due_success_limit": 0,
                    "candidates_not_processed_due_global_abort": 0,
                    "candidates_not_processed_due_execution_deadline": 2,
                    "candidate_terminal_classification_total": 4,
                },
            },
        )
        for index, case in enumerate(cases):
            with self.subTest(kind=case["kind"]):
                canonical, _digest = service.canonical_manifest_payload(
                    {
                        "run_id": f"sidebar-{case['kind']}-20260820T12000{index}+0800",
                        "kind": case["kind"],
                        "group_id": "28888112822211",
                        "tab_id": case["tab_id"],
                        "tab_label": case["tab_label"],
                        "files": [],
                        "candidate_failures": [
                            {
                                "expected_filename": f"候选-{index}",
                                "failure_stage": "detail_preview_close_failed",
                            }
                        ],
                        "scan_summary": {
                            "within_36h": case["within"],
                            "new_download_success": 0,
                            "historical_already_downloaded": case["history"],
                            case["low_field"]: case["low"],
                            "abort_latch": case["abort_latch"],
                        },
                    }
                )
                for field, expected in case["expected"].items():
                    self.assertEqual(canonical["scan_summary"][field], expected)
                self.assertIn(
                    "candidate_terminal_classification_audit_normalized",
                    canonical["scan_summary"],
                )

    def test_inline_manifest_rejects_terminal_candidate_overclassification(self):
        tempdir, _downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        with self.assertRaises(BrowserImportValidationError):
            service.canonical_manifest_payload(
                {
                    "run_id": "sidebar-mp3-20260820T120010+0800",
                    "kind": "mp3",
                    "group_id": "28888112822211",
                    "tab_id": "88844545452542",
                    "tab_label": "调研纪要",
                    "files": [],
                    "candidate_failures": [],
                    "scan_summary": {
                        "within_36h": 2,
                        "new_download_success": 0,
                        "historical_already_downloaded": 1,
                        "skipped_download_count_not_over_10": 2,
                    },
                }
            )

    def test_inline_manifest_rejects_same_run_id_with_changed_payload(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = {
            "run_id": "sidebar-pdf-20260817T120000+0800",
            "kind": "pdf",
            "group_id": "28888112822211",
            "tab_id": "51184248544214",
            "tab_label": "外资研报",
            "files": [],
        }
        canonical, payload_hash = service.canonical_manifest_payload(manifest)
        service.persist_inline_manifest(canonical, payload_hash)
        changed = {**manifest, "scan_summary": {"scanned_topics": 1}}
        changed_canonical, changed_hash = service.canonical_manifest_payload(changed)

        with self.assertRaises(BrowserImportValidationError):
            service.persist_inline_manifest(changed_canonical, changed_hash)

    def test_preview_accepts_array_manifest_and_maps_pdf_tab(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        file_path = downloads / "研报-示例 (1).pdf"
        file_path.write_bytes(b"pdf-content")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-pdf-20260815.json",
            [
                {
                    "filename": file_path.name,
                    "absolute_path": str(file_path),
                    "media_type": "pdf",
                    "download_count": 31,
                    "published_at": "2026-08-14",
                    "source_url": "https://wx.zsxq.com/group/28888112822211",
                    "downloaded_at": "2026-08-15T09:00:00+08:00",
                    "success": True,
                }
            ],
        )

        result = service.preview_manifest(manifest)

        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["valid"], 1)
        item = result["files"][0]
        self.assertEqual(item["tab_label"], "外资研报")
        self.assertEqual(item["tab_id"], "51184248544214")
        self.assertEqual(item["title"], "研报-示例.pdf")
        self.assertTrue(item["topic_id"].startswith("browser-topic-"))
        self.assertTrue(item["file_id"].startswith("browser-file-"))
        self.assertEqual(item["download_count"], 31)
        self.assertEqual(result["warnings"], [])

    def test_preview_accepts_object_manifest_and_maps_mp3_tab(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        file_path = downloads / "调研纪要.mp3"
        file_path.write_bytes(b"audio-content")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-mp3-20260815.json",
            {
                "run_id": "20260815T090000+0800-mp3",
                "files": [
                    {
                        "filename": file_path.name,
                        "absolute_path": str(file_path),
                        "media_type": "audio/mpeg",
                        "download_count": 11,
                        "published_at": "2026-08-14T10:00:00+08:00",
                        "source_url": "https://wx.zsxq.com/tags/调研纪要/88844545452542",
                        "downloaded_at": "2026-08-15T09:00:00+08:00",
                        "success": True,
                    }
                ],
            },
        )

        result = service.preview_manifest(manifest)

        self.assertEqual(result["batch_key"], "20260815T090000+0800-mp3")
        self.assertEqual(result["files"][0]["tab_label"], "调研纪要")
        self.assertEqual(result["files"][0]["tab_id"], "88844545452542")
        self.assertEqual(result["files"][0]["file_ext"], "mp3")

    def test_preview_accepts_empty_run_manifest_with_explicit_tab_metadata(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = self._write_manifest(
            downloads,
            "sidebar-submit-mp3-20260815-120000.json",
            {
                "run_id": "20260815T120000+0800-mp3",
                "kind": "mp3",
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
                "tab_label": "调研纪要",
                "scan_summary": {"scanned_topics": 40, "new_downloads": 0},
                "files": [],
            },
        )

        result = service.preview_manifest(manifest)

        self.assertEqual(result["run_id"], "20260815T120000+0800-mp3")
        self.assertEqual(result["kind"], "mp3")
        self.assertEqual(result["tab_id"], "88844545452542")
        self.assertEqual(result["files"], [])
        self.assertEqual(result["scan_summary"]["new_downloads"], 0)

    def test_preview_warns_for_unknown_download_count_without_treating_it_as_zero(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        file_path = downloads / "未知次数.pdf"
        file_path.write_bytes(b"pdf-content")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-pdf-20260815.json",
            [
                {
                    "filename": file_path.name,
                    "absolute_path": str(file_path),
                    "media_type": "pdf",
                    "download_count": None,
                    "published_at": "2026-08-14",
                    "source_url": "https://wx.zsxq.com/group/28888112822211",
                    "downloaded_at": "2026-08-15T09:00:00+08:00",
                    "success": True,
                }
            ],
        )

        result = service.preview_manifest(manifest)

        self.assertIsNone(result["files"][0]["download_count"])
        self.assertEqual(result["summary"]["unknown_download_count"], 1)
        self.assertEqual(result["summary"]["valid"], 1)
        self.assertTrue(
            any(item["code"] == "download_count_unknown" for item in result["warnings"])
        )

    def test_preview_rejects_file_outside_downloads_root(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        outside = Path(tempdir.name) / "outside.pdf"
        outside.write_bytes(b"not-in-downloads")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-pdf-20260815.json",
            [
                {
                    "filename": outside.name,
                    "absolute_path": str(outside),
                    "media_type": "pdf",
                    "download_count": 30,
                    "success": True,
                }
            ],
        )

        with self.assertRaises(BrowserImportValidationError):
            service.preview_manifest(manifest)


class ZsxqBrowserImportPersistenceTest(unittest.TestCase):
    def setUp(self):
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

    def _service_and_downloads(self):
        tempdir = TemporaryDirectory()
        downloads = Path(tempdir.name) / "Downloads"
        downloads.mkdir()
        return tempdir, downloads, BrowserSidebarImportService(downloads_root=downloads)

    def _write_manifest(self, downloads, name, payload):
        path = downloads / "inbox" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _empty_inline_manifest(kind="mp3", *, run_id=None, scanned_topics=40):
        configs = {
            "mp3": ("88844545452542", "调研纪要"),
            "pdf": ("51184248544214", "外资研报"),
        }
        tab_id, tab_label = configs[kind]
        return {
            "run_id": run_id or f"sidebar-{kind}-20260817T120000+0800",
            "kind": kind,
            "group_id": "28888112822211",
            "tab_id": tab_id,
            "tab_label": tab_label,
            "scan_summary": {"scanned_topics": scanned_topics, "new_downloads": 0},
            "files": [],
        }

    @staticmethod
    def _mp3_inline_manifest(run_id, files, *, new_download_success=None):
        scan_summary = {"scanned_topics": 1}
        if new_download_success is not None:
            scan_summary["new_download_success"] = new_download_success
        return {
            "run_id": run_id,
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "scan_summary": scan_summary,
            "files": files,
        }

    @staticmethod
    def _download_entry(path, *, filename=None, content=None):
        if content is not None:
            path.write_bytes(content)
        return {
            "filename": filename or path.name,
            "absolute_path": str(path),
            "media_type": "mp3",
            "download_count": 11,
            "published_at": "2026-08-17T13:39:00+08:00",
            "success": True,
        }

    def test_idempotent_submission_reuses_receipt_manifest_and_task(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = self._empty_inline_manifest()
        key = f"zsxq-sidebar:mp3:{manifest['run_id']}"

        first = service.prepare_inline_submission(manifest, key)
        replay_before_queue = service.prepare_inline_submission(manifest, key)
        service.mark_submission_queued(first["submission_id"])
        replay_after_queue = service.prepare_inline_submission(manifest, key)

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 1)
        self.assertFalse(first["replayed"])
        self.assertTrue(first["should_dispatch"])
        self.assertTrue(replay_before_queue["replayed"])
        self.assertTrue(replay_before_queue["should_dispatch"])
        self.assertTrue(replay_after_queue["replayed"])
        self.assertFalse(replay_after_queue["should_dispatch"])
        self.assertEqual(first["submission_id"], replay_after_queue["submission_id"])
        self.assertEqual(first["manifest_path"], replay_after_queue["manifest_path"])
        self.assertEqual(first["task_id"], replay_after_queue["task_id"])
        self.assertTrue(Path(first["manifest_path"]).is_file())

    def test_recent_history_ignores_deterministic_manifest_without_receipt(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        downloaded = downloads / "未被后端接收.mp3"
        downloaded.write_bytes(b"orphan-content")
        manifest = {
            "run_id": "sidebar-mp3-20260817T140000+0800-orphan",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "files": [
                {
                    "filename": downloaded.name,
                    "absolute_path": str(downloaded),
                    "media_type": "mp3",
                    "download_count": 11,
                    "published_at": "2026-08-17T13:39:00+08:00",
                    "success": True,
                }
            ],
        }
        canonical, payload_hash = service.canonical_manifest_payload(manifest)
        manifest_path = service.persist_inline_manifest(canonical, payload_hash)
        self.assertRegex(manifest_path.name, r"-[0-9a-f]{12}\.json$")
        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)

        history = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T15:00:00+08:00"),
            require_submission_receipt=True,
        )

        self.assertTrue(history["complete"])
        self.assertEqual(history["files"], [])
        self.assertEqual(history["summary"]["source_files"], 1)
        self.assertEqual(history["summary"]["trusted_source_files"], 0)
        self.assertEqual(history["summary"]["ignored_untrusted_manifests"], 1)

    def test_recent_history_includes_deterministic_manifest_with_receipt(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        downloaded = downloads / "已被后端接收.mp3"
        downloaded.write_bytes(b"accepted-content")
        manifest = {
            "run_id": "sidebar-mp3-20260817T150000+0800-accepted",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "files": [
                {
                    "filename": downloaded.name,
                    "absolute_path": str(downloaded),
                    "media_type": "mp3",
                    "download_count": 11,
                    "published_at": "2026-08-17T14:39:00+08:00",
                    "success": True,
                }
            ],
        }
        key = f"zsxq-sidebar:mp3:{manifest['run_id']}"
        prepared = service.prepare_inline_submission(manifest, key)
        self.assertEqual(ZsxqBrowserSubmission.query.count(), 1)

        history = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-17T16:00:00+08:00"),
            require_submission_receipt=True,
        )

        self.assertTrue(history["complete"])
        self.assertEqual(history["summary"]["trusted_source_files"], 1)
        self.assertEqual(history["summary"]["ignored_untrusted_manifests"], 0)
        self.assertEqual(history["summary"]["unique_downloads"], 1)
        self.assertEqual(history["files"][0]["filename"], downloaded.name)
        self.assertEqual(history["files"][0]["dedupe_key"].split(":", 1)[0], "metadata")
        self.assertEqual(
            prepared["manifest_path"], ZsxqBrowserSubmission.query.one().manifest_path
        )

    def test_recent_history_includes_all_unified_submission_upstreams(self):
        """History is the shared pipeline ledger, not sidebar manifests alone."""
        tempdir, _downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)

        tasks = [
            PublicationTask(params={}, feature="content_audio_clean")
            for _ in range(5)
        ]
        db.session.add_all(tasks)
        db.session.flush()
        db.session.add_all(
            [
                ContentPipelineAsset(
                    media_type="audio",
                    content_sha256="a" * 64,
                    canonical_task_id=tasks[0].id,
                    source_path="/submissions/scheduled.mp3",
                    filename="计划任务.mp3",
                    source_mode="scheduled_api",
                    status="cleaned",
                    created_at=datetime(2026, 8, 25, 9, 0, 0),
                ),
                ContentPipelineAsset(
                    media_type="audio",
                    content_sha256="b" * 64,
                    canonical_task_id=tasks[1].id,
                    source_path="/submissions/sidebar.mp3",
                    filename="侧边栏.mp3",
                    source_mode="browser_sidebar",
                    status="cleaned",
                    created_at=datetime(2026, 8, 24, 9, 0, 0),
                ),
                ContentPipelineAsset(
                    media_type="audio",
                    content_sha256="c" * 64,
                    canonical_task_id=tasks[2].id,
                    source_path="/submissions/manual.mp3",
                    filename="手动投递.mp3",
                    source_mode="manual_download",
                    status="cleaned",
                    created_at=datetime(2026, 8, 23, 9, 0, 0),
                ),
                ContentPipelineAsset(
                    media_type="audio",
                    content_sha256="d" * 64,
                    canonical_task_id=tasks[3].id,
                    source_path="/submissions/too-old.mp3",
                    filename="窗口外.mp3",
                    source_mode="manual_download",
                    status="cleaned",
                    created_at=datetime(2026, 8, 22, 23, 59, 59),
                ),
                ContentPipelineAsset(
                    media_type="pdf",
                    content_sha256="e" * 64,
                    canonical_task_id=tasks[4].id,
                    source_path="/submissions/pdf.pdf",
                    filename="不属于音频.pdf",
                    source_mode="manual_download",
                    status="cleaned",
                    created_at=datetime(2026, 8, 25, 9, 0, 0),
                ),
            ]
        )
        db.session.commit()

        history = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-25T20:00:00+08:00"),
            require_submission_receipt=True,
        )

        self.assertTrue(history["complete"])
        self.assertEqual(
            {item["filename"] for item in history["files"]},
            {"计划任务.mp3", "侧边栏.mp3", "手动投递.mp3"},
        )
        self.assertEqual(history["summary"]["unified_submissions"], 3)
        self.assertEqual(history["summary"]["records_seen"], 3)
        self.assertEqual(history["summary"]["unique_downloads"], 3)

    def test_recent_mp3_history_reports_latest_video_workflow_submission(self):
        tempdir, _downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)

        clean_tasks = [
            PublicationTask(params={}, feature="content_audio_clean")
            for _ in range(2)
        ]
        db.session.add_all(clean_tasks)
        db.session.flush()
        submitted_asset = ContentPipelineAsset(
            id="11111111-1111-4111-8111-111111111111",
            media_type="audio",
            content_sha256="a" * 64,
            canonical_task_id=clean_tasks[0].id,
            source_path="/submissions/submitted.mp3",
            filename="已投递.mp3",
            source_mode="browser_sidebar",
            status="cleaned",
            created_at=datetime(2026, 8, 25, 9, 0, 0),
        )
        untouched_asset = ContentPipelineAsset(
            id="22222222-2222-4222-8222-222222222222",
            media_type="audio",
            content_sha256="b" * 64,
            canonical_task_id=clean_tasks[1].id,
            source_path="/submissions/untouched.mp3",
            filename="未投递.mp3",
            source_mode="browser_sidebar",
            status="cleaned",
            created_at=datetime(2026, 8, 25, 10, 0, 0),
        )
        db.session.add_all([submitted_asset, untouched_asset])
        db.session.add_all(
            [
                PublicationTask(
                    id="33333333-3333-4333-8333-333333333333",
                    params={
                        "feature": "mp3_video_workflow",
                        "source_feature": "content_pipeline_asset",
                        "source_task_id": submitted_asset.id,
                    },
                    feature="mp3_video_workflow",
                    source_task_id=submitted_asset.id,
                    status="failed",
                    current_step="visuals_failed",
                    created_at=datetime(2026, 8, 25, 11, 0, 0),
                ),
                PublicationTask(
                    id="44444444-4444-4444-8444-444444444444",
                    params={
                        "feature": "mp3_video_workflow",
                        "source_feature": "content_pipeline_asset",
                        "source_task_id": submitted_asset.id,
                    },
                    feature="mp3_video_workflow",
                    source_task_id=submitted_asset.id,
                    status="processing",
                    current_step="landscape_video_waiting",
                    created_at=datetime(2026, 8, 25, 12, 0, 0),
                ),
            ]
        )
        db.session.commit()

        history = service.recent_download_history(
            "mp3",
            days=3,
            now=datetime.fromisoformat("2026-08-25T20:00:00+08:00"),
            require_submission_receipt=True,
        )

        files = {item["filename"]: item for item in history["files"]}
        self.assertEqual(
            files["已投递.mp3"]["mp3_video_workflow"],
            {
                "submitted": True,
                "job_id": "44444444-4444-4444-8444-444444444444",
                "status": "processing",
                "step": "landscape_video_waiting",
                "submitted_at": "2026-08-25T12:00:00",
                "completed": False,
            },
        )
        self.assertEqual(
            files["未投递.mp3"]["mp3_video_workflow"],
            {
                "submitted": False,
                "job_id": None,
                "status": None,
                "step": None,
                "submitted_at": None,
                "completed": False,
            },
        )

    def test_invalid_inline_manifest_does_not_reserve_run_id_or_persist_manifest(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        run_id = "sidebar-pdf-20260818T003014Z-path-retry"
        key = f"zsxq-sidebar:pdf:{run_id}"
        invalid = {
            "run_id": run_id,
            "kind": "pdf",
            "group_id": "28888112822211",
            "tab_id": "51184248544214",
            "tab_label": "外资研报",
            "files": [
                {
                    "filename": "研报.pdf",
                    "media_type": "pdf",
                    "download_count": 31,
                    "success": True,
                }
            ],
        }

        with self.assertRaises(BrowserImportValidationError):
            service.prepare_inline_submission(invalid, key)

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)
        self.assertEqual(list(service.inbox_root.glob("sidebar-submit-pdf-*.json")), [])

        downloaded = downloads / "研报.pdf"
        downloaded.write_bytes(b"pdf-content")
        corrected = json.loads(json.dumps(invalid))
        corrected["files"][0]["absolute_path"] = str(downloaded)

        prepared = service.prepare_inline_submission(corrected, key)

        self.assertFalse(prepared["replayed"])
        self.assertEqual(ZsxqBrowserSubmission.query.count(), 1)
        self.assertTrue(Path(prepared["manifest_path"]).is_file())

    def test_inline_submission_rejects_scan_success_count_mismatch_before_receipt(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        downloaded = downloads / "计数不一致.mp3"
        files = [self._download_entry(downloaded, content=b"one-file")]
        run_id = "sidebar-mp3-20260818T100000+0800-count-mismatch"
        manifest = self._mp3_inline_manifest(run_id, files, new_download_success=2)

        with self.assertRaises(BrowserImportValidationError):
            service.prepare_inline_submission(manifest, f"zsxq-sidebar:mp3:{run_id}")

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)
        self.assertEqual(list(service.inbox_root.glob("*.json")), [])

    def test_inline_submission_rejects_duplicate_normalized_basenames_before_receipt(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        base = downloads / "同轮重复.mp3"
        chrome_copy = downloads / "同轮重复 (1).mp3"
        files = [
            self._download_entry(base, content=b"first"),
            self._download_entry(chrome_copy, content=b"second"),
        ]
        run_id = "sidebar-mp3-20260818T100100+0800-name-duplicate"
        manifest = self._mp3_inline_manifest(run_id, files, new_download_success=2)

        with self.assertRaises(BrowserImportValidationError):
            service.prepare_inline_submission(manifest, f"zsxq-sidebar:mp3:{run_id}")

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)
        self.assertEqual(list(service.inbox_root.glob("*.json")), [])

    def test_inline_submission_rejects_duplicate_file_content_before_receipt(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        first = downloads / "内容甲.mp3"
        second = downloads / "内容乙.mp3"
        files = [
            self._download_entry(first, content=b"same-content"),
            self._download_entry(second, content=b"same-content"),
        ]
        run_id = "sidebar-mp3-20260818T100200+0800-content-duplicate"
        manifest = self._mp3_inline_manifest(run_id, files, new_download_success=2)

        with self.assertRaises(BrowserImportValidationError):
            service.prepare_inline_submission(manifest, f"zsxq-sidebar:mp3:{run_id}")

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)
        self.assertEqual(list(service.inbox_root.glob("*.json")), [])

    def test_inline_submission_rejects_declared_filename_path_mismatch_before_receipt(
        self,
    ):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        actual = downloads / "实际文件.mp3"
        files = [
            self._download_entry(
                actual,
                filename="声称是另一个文件.mp3",
                content=b"actual-content",
            )
        ]
        run_id = "sidebar-mp3-20260818T100300+0800-path-mismatch"
        manifest = self._mp3_inline_manifest(run_id, files, new_download_success=1)

        with self.assertRaises(BrowserImportValidationError):
            service.prepare_inline_submission(manifest, f"zsxq-sidebar:mp3:{run_id}")

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 0)
        self.assertEqual(list(service.inbox_root.glob("*.json")), [])

    def test_inline_submission_reuses_preview_when_file_changes_after_validation(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        run_id = "sidebar-pdf-20260818T090000+0800-single-preview"
        key = f"zsxq-sidebar:pdf:{run_id}"
        downloaded = downloads / "研报.pdf"
        downloaded.write_bytes(b"pdf-content")
        manifest = {
            "run_id": run_id,
            "kind": "pdf",
            "group_id": "28888112822211",
            "tab_id": "51184248544214",
            "tab_label": "外资研报",
            "files": [
                {
                    "filename": downloaded.name,
                    "absolute_path": str(downloaded),
                    "media_type": "pdf",
                    "download_count": 31,
                    "success": True,
                }
            ],
        }
        original_preview_manifest = service.preview_manifest
        preview_calls = []

        def preview_once(path):
            result = original_preview_manifest(path)
            preview_calls.append(str(path))
            if len(preview_calls) == 1:
                downloaded.unlink()
            return result

        service.preview_manifest = preview_once

        prepared = service.prepare_inline_submission(manifest, key)

        self.assertFalse(prepared["replayed"])
        self.assertEqual(len(preview_calls), 1)
        self.assertEqual(prepared["preview"]["summary"]["files"], 1)
        self.assertEqual(ZsxqBrowserSubmission.query.count(), 1)
        self.assertTrue(Path(prepared["manifest_path"]).is_file())

    def test_idempotent_submission_rejects_same_key_with_changed_payload(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = self._empty_inline_manifest()
        key = f"zsxq-sidebar:mp3:{manifest['run_id']}"
        service.prepare_inline_submission(manifest, key)
        changed = self._empty_inline_manifest(scanned_topics=39)

        with self.assertRaises(IdempotencyConflictError):
            service.prepare_inline_submission(changed, key)

        self.assertEqual(ZsxqBrowserSubmission.query.count(), 1)

    def test_idempotent_submission_rejects_changed_workflow_options(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest = self._empty_inline_manifest(
            run_id="sidebar-mp3-20260817T130000+0800-options"
        )
        key = f"zsxq-sidebar:mp3:{manifest['run_id']}"
        service.prepare_inline_submission(
            manifest,
            key,
            dispatch_clean=True,
            auto_publish={"enabled": True},
        )

        with self.assertRaises(IdempotencyConflictError):
            service.prepare_inline_submission(
                manifest,
                key,
                dispatch_clean=False,
                auto_publish={"enabled": False},
            )

    def _service_and_manifest(self, *, filename="研报.pdf", content=b"pdf-content"):
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        downloads = Path(tempdir.name) / "Downloads"
        data_root = Path(tempdir.name) / "project-data"
        downloads.mkdir()
        file_path = downloads / filename
        file_path.write_bytes(content)
        manifest_path = downloads / "inbox" / "sidebar-result-pdf-20260815.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "filename": filename,
                        "absolute_path": str(file_path),
                        "media_type": "pdf",
                        "download_count": 30,
                        "published_at": "2026-08-14",
                        "source_url": "https://wx.zsxq.com/group/28888112822211",
                        "downloaded_at": "2026-08-15T09:00:00+08:00",
                        "success": True,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return (
            BrowserSidebarImportService(
                downloads_root=downloads,
                data_root=data_root,
            ),
            manifest_path,
        )

    def test_preview_does_not_write_database_and_import_creates_canonical_rows(self):
        service, manifest_path = self._service_and_manifest()

        preview = service.preview_manifests([manifest_path])

        self.assertEqual(preview["summary"]["files"], 1)
        self.assertEqual(ZsxqSyncRun.query.count(), 0)
        self.assertEqual(ZsxqTopic.query.count(), 0)
        self.assertEqual(ZsxqAttachment.query.count(), 0)

        result = service.import_manifests([manifest_path])

        self.assertEqual(result["summary"]["created_attachments"], 1)
        self.assertEqual(result["summary"]["created_topics"], 1)
        self.assertEqual(ZsxqSyncRun.query.count(), 1)
        self.assertEqual(ZsxqTopic.query.count(), 1)
        self.assertEqual(ZsxqAttachment.query.count(), 1)
        self.assertEqual(ZsxqSyncRunDownload.query.count(), 1)
        run = ZsxqSyncRun.query.one()
        topic = ZsxqTopic.query.one()
        attachment = ZsxqAttachment.query.one()
        ledger = ZsxqSyncRunDownload.query.one()
        self.assertEqual(run.mode, "browser_sidebar")
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.topics_fetched, 1)
        self.assertEqual(run.attachments_seen, 1)
        self.assertEqual(run.attachments_downloaded, 1)
        self.assertEqual(run.attachments_skipped, 0)
        self.assertEqual(topic.tab_label, "外资研报")
        self.assertEqual(attachment.download_status, "downloaded")
        self.assertEqual(attachment.local_path, ledger.local_path)
        self.assertEqual(attachment.raw_payload["source"], "chrome_sidebar")
        self.assertTrue(attachment.raw_payload["sha256"])
        self.assertEqual(datetime.fromisoformat("2026-08-15T09:00:00+08:00").year, 2026)

    def test_import_empty_manifest_creates_completed_zero_file_sync_run(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        manifest_path = self._write_manifest(
            downloads,
            "sidebar-submit-mp3-20260815-120000.json",
            {
                "run_id": "20260815T120000+0800-mp3",
                "kind": "mp3",
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
                "tab_label": "调研纪要",
                "scan_summary": {"scanned_topics": 40, "new_downloads": 0},
                "files": [],
            },
        )

        result = service.import_manifests([manifest_path], dispatch_clean=True)

        self.assertEqual(result["summary"]["manifests"], 1)
        self.assertEqual(result["summary"]["created_topics"], 0)
        self.assertEqual(result["summary"]["created_attachments"], 0)
        self.assertEqual(ZsxqSyncRun.query.count(), 1)
        self.assertEqual(ZsxqTopic.query.count(), 0)
        self.assertEqual(ZsxqAttachment.query.count(), 0)
        run = ZsxqSyncRun.query.one()
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.tab_id, "88844545452542")
        self.assertEqual(run.topics_fetched, 0)
        self.assertEqual(run.attachments_downloaded, 0)
        self.assertTrue(run.result["browser_import"]["no_new_files"])
        self.assertEqual(result["manifests"][0]["kind"], "mp3")

    def test_import_moves_file_to_project_data_and_records_stored_path(self):
        service, manifest_path = self._service_and_manifest(filename="归档研报.pdf")
        source = Path(manifest_path).parent.parent / "归档研报.pdf"

        service.import_manifests([manifest_path])

        attachment = ZsxqAttachment.query.one()
        target = Path(attachment.local_path)
        self.assertFalse(source.exists())
        self.assertTrue(target.exists())
        self.assertIn(
            "project-data/28888112822211/外资研报-51184248544214/files", str(target)
        )

        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_payload[0]["stored_path"], str(target))

        repeated = service.import_manifests([manifest_path])
        self.assertEqual(repeated["summary"]["reused_attachments"], 1)
        self.assertTrue(target.exists())

    def test_repeated_import_reuses_attachment_and_topic_but_records_new_run_ledger(
        self,
    ):
        service, manifest_path = self._service_and_manifest()

        first = service.import_manifests([manifest_path])
        second = service.import_manifests([manifest_path])

        self.assertEqual(first["summary"]["created_attachments"], 1)
        self.assertEqual(second["summary"]["created_attachments"], 0)
        self.assertEqual(second["summary"]["reused_attachments"], 1)
        self.assertEqual(ZsxqTopic.query.count(), 1)
        self.assertEqual(ZsxqAttachment.query.count(), 1)
        self.assertEqual(ZsxqSyncRun.query.count(), 2)
        self.assertEqual(ZsxqSyncRunDownload.query.count(), 2)

    def test_same_hash_with_different_names_creates_one_attachment(self):
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        downloads = Path(tempdir.name) / "Downloads"
        downloads.mkdir()
        first = downloads / "研报.pdf"
        second = downloads / "研报 (1).pdf"
        first.write_bytes(b"same-content")
        second.write_bytes(b"same-content")
        manifest_path = downloads / "inbox" / "sidebar-result-pdf-20260815.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "filename": first.name,
                        "absolute_path": str(first),
                        "media_type": "pdf",
                        "download_count": 30,
                        "success": True,
                    },
                    {
                        "filename": second.name,
                        "absolute_path": str(second),
                        "media_type": "pdf",
                        "download_count": 30,
                        "success": True,
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = BrowserSidebarImportService(
            downloads_root=downloads,
            data_root=Path(tempdir.name) / "project-data",
        ).import_manifests([manifest_path])

        self.assertEqual(result["summary"]["created_attachments"], 1)
        self.assertEqual(result["summary"]["reused_attachments"], 1)
        self.assertEqual(ZsxqAttachment.query.count(), 1)

    def test_preview_marks_same_content_with_different_chrome_names_as_duplicate(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        first = downloads / "同一研报.pdf"
        second = downloads / "同一研报 (1).pdf"
        first.write_bytes(b"same-content")
        second.write_bytes(b"same-content")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-pdf-20260815.json",
            [
                {
                    "filename": first.name,
                    "absolute_path": str(first),
                    "media_type": "pdf",
                    "download_count": 30,
                    "success": True,
                },
                {
                    "filename": second.name,
                    "absolute_path": str(second),
                    "media_type": "pdf",
                    "download_count": 30,
                    "success": True,
                },
            ],
        )

        result = service.preview_manifest(manifest)

        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["duplicate_content"], 1)
        self.assertEqual(
            result["files"][1]["duplicate_of"], result["files"][0]["file_id"]
        )

    def test_preview_rejects_extension_media_type_mismatch(self):
        tempdir, downloads, service = self._service_and_downloads()
        self.addCleanup(tempdir.cleanup)
        file_path = downloads / "audio.mp3"
        file_path.write_bytes(b"audio")
        manifest = self._write_manifest(
            downloads,
            "sidebar-result-pdf-20260815.json",
            [
                {
                    "filename": file_path.name,
                    "absolute_path": str(file_path),
                    "media_type": "pdf",
                    "download_count": 30,
                    "success": True,
                }
            ],
        )

        with self.assertRaises(BrowserImportValidationError):
            service.preview_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
