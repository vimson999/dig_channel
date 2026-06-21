import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402
from models.zsxq_sync import ZsxqAttachment  # noqa: E402


class ZsxqSyncPdfCleanHandoffTest(unittest.TestCase):
    def test_effective_topic_max_pages_expands_to_cover_target_count(self):
        self.assertEqual(zsxq_sync_service._effective_topic_max_pages(12, 1), 1)
        self.assertEqual(zsxq_sync_service._effective_topic_max_pages(40, 1), 2)
        self.assertEqual(zsxq_sync_service._effective_topic_max_pages(50, 1), 3)
        self.assertEqual(zsxq_sync_service._effective_topic_max_pages(40, 4), 4)

    def test_attachment_download_policy_blocks_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            policy = zsxq_sync_service._attachment_download_policy(
                requested=True,
                explicit_allow=True,
                max_download_files=9,
            )

        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["reason"], "env_disabled")
        self.assertEqual(policy["max_download_files"], 0)

    def test_attachment_download_policy_requires_explicit_allow_and_caps_limit(self):
        with patch.dict("os.environ", {
            "ZSXQ_SYNC_ATTACHMENT_DOWNLOADS_ENABLED": "1",
            "ZSXQ_SYNC_ATTACHMENT_DOWNLOAD_LIMIT": "9",
        }, clear=True):
            missing_allow = zsxq_sync_service._attachment_download_policy(
                requested=True,
                explicit_allow=False,
                max_download_files=9,
            )
            enabled = zsxq_sync_service._attachment_download_policy(
                requested=True,
                explicit_allow=True,
                max_download_files=9,
            )

        self.assertFalse(missing_allow["enabled"])
        self.assertEqual(missing_allow["reason"], "missing_explicit_allow")
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["max_download_files"], 3)

    def test_downloaded_pdf_files_from_run_result_is_strict_and_deduped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pdf = tmp_path / "new.pdf"
            txt = tmp_path / "note.txt"
            pdf.write_bytes(b"%PDF-1.4\nnew")
            txt.write_text("not a pdf", encoding="utf-8")

            run = SimpleNamespace(result={
                "downloaded_pdf_files": [
                    {
                        "path": str(pdf),
                        "name": "new.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    },
                    {
                        "path": str(pdf),
                        "name": "duplicate.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    },
                    {"path": str(txt), "name": "note.txt"},
                    {"path": str(tmp_path / "missing.pdf"), "name": "missing.pdf"},
                ],
            })

            self.assertEqual(
                zsxq_sync_service._downloaded_pdf_files_from_run_result(run),
                [
                    {
                        "path": str(pdf.resolve()),
                        "name": "new.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    }
                ],
            )

    def test_downloaded_file_entry_requires_real_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pdf = tmp_path / "new.pdf"
            pdf.write_bytes(b"%PDF-1.4\nnew")
            record = SimpleNamespace(
                name="new.pdf",
                topic_id="topic-1",
                tab_id="tab-1",
                id="attachment-1",
            )

            self.assertEqual(
                zsxq_sync_service._downloaded_file_entry(record, str(pdf)),
                {
                    "path": str(pdf.resolve()),
                    "name": "new.pdf",
                    "topic_id": "topic-1",
                    "tab_id": "tab-1",
                    "attachment_id": "attachment-1",
                },
            )
            self.assertIsNone(
                zsxq_sync_service._downloaded_file_entry(record, str(tmp_path / "missing.pdf"))
            )

    def test_attachment_publish_status_fallback_is_disabled_by_default(self):
        payload = {"topic-1": [{"id": "attachment-1", "name": "report.pdf"}]}

        with patch.dict("os.environ", {}, clear=True):
            with patch.object(
                zsxq_sync_service,
                "_recent_publish_status_context",
                side_effect=AssertionError("slow JSON fallback should stay disabled"),
            ):
                zsxq_sync_service._add_attachment_publish_statuses(payload)

        self.assertNotIn("publish_status", payload["topic-1"][0])

    def test_attachment_publish_status_fallback_can_be_enabled(self):
        payload = {"topic-1": [{"id": "attachment-1", "name": "report.pdf"}]}
        context = {
            "status": {
                "publish_status": "submitted",
                "publish_task_id": "publish-1",
                "publish_source_task_id": "clean-1",
            },
            "outcomes": {"by_path": {}, "by_name": {}},
            "source_refs": [{"attachment_id": "attachment-1"}],
        }

        with patch.dict("os.environ", {"ZSXQ_ATTACHMENT_PUBLISH_FALLBACK_ENABLED": "1"}, clear=True):
            with patch.object(zsxq_sync_service, "_recent_publish_status_context", return_value=[context]) as recent:
                zsxq_sync_service._add_attachment_publish_statuses(payload)

        recent.assert_called_once_with()
        self.assertEqual(payload["topic-1"][0]["publish_status"], "submitted")
        self.assertEqual(payload["topic-1"][0]["publish_task_id"], "publish-1")

    def test_source_ref_with_clean_output_requires_file_level_publish_match(self):
        status = {"publish_status": "published", "publish_task_id": "publish-1"}
        source_ref = {
            "attachment_id": "attachment-1",
            "output_paths": ["/tmp/cleaned/report.pdf"],
        }

        self.assertIsNone(
            zsxq_sync_service._status_for_source_ref(
                source_ref,
                status,
                {"by_path": {}, "by_name": {}},
            )
        )

    def test_attachment_published_without_topic_is_attention_status(self):
        attachment = ZsxqAttachment(
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="file-1",
            name="report.pdf",
            download_status="downloaded",
            publish_status="published",
            publish_topic_id=None,
        )

        payload = attachment.to_dict()

        self.assertEqual(payload["publish_status"], "published_unverified")
        self.assertFalse(payload["published"])
        self.assertTrue(payload["publish_attention_required"])


if __name__ == "__main__":
    unittest.main()
