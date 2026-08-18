import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    "mp3": ROOT / "plugins/zsxq-sidebar-sync/skills/zsxq-mp3-sidebar-download/SKILL.md",
    "pdf": ROOT / "plugins/zsxq-sidebar-sync/skills/zsxq-pdf-sidebar-download/SKILL.md",
}


class ZsxqSidebarSkillContractTest(unittest.TestCase):
    def _skill_texts(self):
        for kind, path in SKILLS.items():
            with self.subTest(kind=kind):
                self.assertTrue(path.is_file())
            yield kind, path.read_text(encoding="utf-8")

    def test_production_scan_limits_remain_48_hours_40_topics_40_downloads(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("回溯最多 40 组主题", text)
                self.assertIn("向前 48 小时以内", text)
                self.assertRegex(
                    text, rf"最多成功下载 40 个新的 {'MP3' if kind == 'mp3' else 'PDF'}"
                )

    def test_history_identity_uses_canonical_fields_not_source_url(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`normalized_filename`", text)
                self.assertIn("`published_at_canonical`", text)
                self.assertIn("`dedupe_key`", text)
                self.assertIn("不得把 `source_url` 加入去重键", text)
                self.assertNotIn("filename + published_at + source_url", text)
                self.assertIn("同一规范化文件名", text)
                self.assertNotIn("规范化文件名和发布时间同时匹配", text)
                self.assertIn("ignored_untrusted_manifests", text)

    def test_stale_modal_must_be_rechecked_before_any_download_click(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("expected_candidate_filename", text)
                self.assertIn("detail_file_mismatch_after_2s", text)
                self.assertIn("保持同一弹层不动", text)
                self.assertIn("绝对不得点击此时的下载按钮", text)

    def test_actual_file_mismatch_or_same_run_duplicate_stops_further_downloads(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("downloaded_actual_base_names", text)
                self.assertIn("downloaded_file_mismatch", text)
                self.assertIn("duplicate_actual_file_in_same_run", text)
                self.assertGreaterEqual(text.count("立即停止本轮后续"), 2)

    def test_only_one_download_may_be_in_flight_and_must_finish_before_next_candidate(
        self,
    ):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("同时只能有 1 个下载任务在途", text)
                self.assertIn("不得打开或处理下一个候选", text)
                self.assertIn("当前候选对应的下载完成事件", text)

    def test_frozen_manifest_drives_submission_and_final_report(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("new_download_success` 必须严格等于 `files` 数量", text)
                self.assertIn("视为冻结对象", text)
                self.assertIn("最终汇报中的下载数量、文件清单和跳过计数", text)


if __name__ == "__main__":
    unittest.main()
