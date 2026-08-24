import re
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

    @staticmethod
    def _level_three_section(text, heading):
        start = text.index(f"### {heading}")
        next_start = text.find("\n### ", start + 4)
        return text[start:] if next_start == -1 else text[start:next_start]

    def test_formal_scan_limits_are_36_hours_30_topics_40_downloads(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("回溯最多 30 组主题", text)
                self.assertIn("向前 36 小时以内", text)
                self.assertRegex(
                    text, rf"最多成功下载 40 个新的 {'MP3' if kind == 'mp3' else 'PDF'}"
                )
                self.assertIn("当前正式运行参数为", text)
                self.assertNotIn("回溯最多 40 组主题", text)
                self.assertNotIn("向前 48 小时以内", text)

    def test_topic_candidate_scan_uses_scoped_locators_not_page_evaluate(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("topic_group_locator", text)
                self.assertIn(
                    'topic_group_locator = current_page.playwright.locator("app-topic")',
                    text,
                )
                self.assertIn("topic_group_locator.count()", text)
                self.assertIn("topic_group_locator.nth(topic_index)", text)
                self.assertIn('topic_group.locator(".file-name")', text)
                self.assertIn("只从当前单个主题卡片作用域", text)
                self.assertIn("不得调用 `current_page.playwright.evaluate`", text)
                self.assertIn("不得使用 `document.querySelectorAll`", text)
                self.assertIn("不得读取 `outerHTML`", text)
                self.assertIn("不得读取整个页面或主题列表的宽泛 `innerText`", text)

    def test_download_button_click_requires_the_complete_final_gate(self):
        thresholds = {"mp3": 10, "pdf": 30}
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                gate = self._level_three_section(text, "下载按钮点击最终门禁")
                required = (
                    "`within_36h == true`",
                    "`extension_matches_kind == true`",
                    "`history_hit == false`",
                    "`duplicate_candidate_in_same_run == false`",
                    "`detail_filename_bound == true`",
                    f"`download_count > {thresholds[kind]}`",
                    "`download_button_present == true`",
                    "`download_metadata_capability_ready == true`",
                    "`download_state == IDLE`",
                    "`abort_latch == false`",
                    "`no_inflight_download == true`",
                )
                for condition in required:
                    with self.subTest(kind=kind, condition=condition):
                        self.assertIn(condition, gate)
                self.assertIn("任一条件为假都不得点击下载按钮", gate)
                self.assertIn("最终核对后才创建本候选账本并把监听器置为 active", gate)
                self.assertIn("监听器 active 后只允许点击一次", gate)
                self.assertIn(
                    "发出触发命令前再次确认 `abort_latch == false`、`download_state == ARMED`、当前监听代次 active 且没有其他在途下载",
                    text,
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

    def test_definitive_actual_file_mismatch_isolated_to_candidate_and_scan_continues(
        self,
    ):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("downloaded_actual_base_names", text)
                self.assertIn("downloaded_file_mismatch", text)
                self.assertIn("duplicate_actual_file_in_same_run", text)
                self.assertIn(
                    "实际文件名不匹配或同轮实际文件重复属于已取得唯一终态的候选级失败",
                    text,
                )
                self.assertIn("从 `files` 排除当前候选后继续处理下一个候选", text)
                self.assertIn("不得把单个候选异常扩大为整批失败", text)

    def test_only_one_download_may_be_in_flight_and_must_finish_before_next_candidate(
        self,
    ):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("同时只能有 1 个下载任务在途", text)
                self.assertIn(
                    "当前候选事务仍为 `ARMED` 或 `WAITING_FOR_COMPLETION` 时",
                    text,
                )
                self.assertIn("不得打开或处理下一个候选", text)

    def test_each_candidate_download_uses_a_separate_browser_call_checkpoint(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("一次浏览器工具调用最多只处理 1 个候选的下载事务", text)
                self.assertIn(
                    "禁止在同一次 `js` 或其他浏览器工具调用中使用循环、批处理或递归连续处理多个候选",
                    text,
                )
                self.assertIn("候选间副作用屏障", text)
                self.assertIn(
                    "本次浏览器工具调用已经返回到当前对话，并完成账本终态核对",
                    text,
                )
                self.assertIn(
                    "才允许发起处理下一个候选的新浏览器工具调用",
                    text,
                )

    def test_frozen_manifest_drives_submission_and_final_report(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("new_download_success` 必须严格等于 `files` 数量", text)
                self.assertIn("视为冻结对象", text)
                self.assertIn("最终汇报中的下载数量、文件清单和跳过计数", text)

    def test_audit_counters_are_derived_from_authoritative_sources(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    "`candidate_failure_count == candidate_failures.length`",
                    text,
                )
                self.assertIn(
                    "每个失败阶段计数字段必须严格等于 `candidate_failures` 中相同 `failure_stage` 的条目数",
                    text,
                )
                self.assertIn(
                    "逐字段原样复制历史页 `data.summary.records_seen`、`data.summary.unique_downloads`、`data.summary.duplicates_collapsed` 和 `data.summary.ignored_untrusted_manifests`",
                    text,
                )
                self.assertIn(
                    "不得用 `data.files.length` 重算 `history_records_seen` 或 `history_duplicates_collapsed`",
                    text,
                )

    def test_history_observations_are_one_to_one_with_current_run_history_hits(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    "`history_observations.length == historical_already_downloaded`",
                    text,
                )
                self.assertIn("历史命中为 0 时必须是空数组 `[]`", text)
                self.assertIn(
                    "不得把历史汇总对象、占位对象、完整 `data.files` 或未命中记录放入 `history_observations`",
                    text,
                )
                self.assertIn(
                    "每项都必须含有非空 `filename`、正确的媒体扩展名",
                    text,
                )

    def test_history_observations_include_backend_compatibility_download_count(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    "`download_count` 必须作为兼容字段显式写入",
                    text,
                )
                self.assertIn(
                    "`download_count == latest_download_count`",
                    text,
                )
                self.assertRegex(
                    text,
                    r'"download_count": 41|"download_count": 23',
                )

    def test_first_submit_attempt_irreversibly_locks_the_frozen_manifest(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`submission_attempted = true`", text)
                self.assertIn("`frozen_manifest_sha256`", text)
                self.assertIn(
                    "任何明确 HTTP 返回（包括 400、409 和字段校验错误）都必须立即结束提交阶段",
                    text,
                )
                self.assertIn(
                    "不得修字段、不得重新序列化变体、不得用相同或新的 `run_id` 再提",
                    text,
                )
                self.assertIn(
                    "只有无法取得任何 HTTP/结果页证据的结果未知状态",
                    text,
                )
                self.assertIn(
                    "重试前必须重新计算并确认哈希仍等于 `frozen_manifest_sha256`",
                    text,
                )

    def test_final_report_copies_full_history_names_and_submit_receipt_fields(self):
        for kind, text in self._skill_texts():
            extension = ".mp3" if kind == "mp3" else ".pdf"
            with self.subTest(kind=kind):
                self.assertIn(
                    "逐项原样复制 `history_observations[].filename` 的完整文件名",
                    text,
                )
                self.assertIn(
                    "不得缩写、概括或省略扩展名",
                    text,
                )
                self.assertIn(extension, text)
                self.assertIn(
                    "逐字段原样复制结果页的 `task_id`、`manifest_path` 和 `replayed`",
                    text,
                )
                self.assertIn(
                    "`manifest_path` 必须输出结果页返回的完整绝对路径",
                    text,
                )
                self.assertIn(
                    "只显示文件名或可点击标签不能代替这个字段值",
                    text,
                )
                self.assertIn(
                    "`replayed` 必须明确输出为 `true` 或 `false`",
                    text,
                )

    def test_history_lookup_is_materialized_and_candidate_terminal_states_do_not_overlap(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`history_by_normalized_filename`", text)
                self.assertIn("不得依赖对话记忆、截断输出或人工回想", text)
                self.assertIn("打开详情弹层之前", text)
                self.assertIn("最终点击门禁之前", text)
                self.assertIn("`candidate_terminal_states`", text)
                self.assertIn("`pre_download` 不是允许的 `failure_stage`", text)
                self.assertIn(
                    "不得把尚未启动下载事务的候选塞入 `candidate_failures`",
                    text,
                )
                self.assertIn(
                    "必须直接进入 `candidates_not_processed_due_execution_deadline`",
                    text,
                )
                self.assertIn(
                    "终态分类各分项之和必须严格等于兼容字段 `within_48h`",
                    text,
                )

    def test_history_filename_normalizer_is_fixed_and_matches_backend_contract(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    "固定复制下面这一个规范化函数，不得临时重写或使用第二套实现",
                    text,
                )
                self.assertIn("normalizeFilenameForHistory", text)
                self.assertIn(
                    '.replace(/[\\u200B-\\u200D\\u2060\\uFEFF]/g, "")',
                    text,
                )
                self.assertIn(
                    '.replace(/\\s+\\(\\d+\\)(?=\\.[^.]+$)/, "")',
                    text,
                )
                self.assertIn('.replace(/\\s+/gu, " ")', text)
                self.assertIn('.toLocaleLowerCase("en-US")', text)

    def test_history_dedupe_uses_dual_indexes_and_a_whole_window_preflight(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                for marker in (
                    "`history_by_raw_filename`",
                    "`frozen_history_normalized_keys`",
                    "`frozen_history_raw_keys`",
                    "`history_candidate_intersection_keys`",
                    "在第一次下载点击前对完整窗口候选执行一次整批历史交叉审计",
                    "直接遍历 `data.files`",
                    "任一索引或直接查找命中都必须判为历史命中",
                    "三种查找的命中布尔值不一致",
                    "0 次下载点击处以 `history_incomplete` 停止",
                ):
                    with self.subTest(kind=kind, marker=marker):
                        self.assertIn(marker, text)

    def test_frozen_manifest_files_cannot_overlap_the_frozen_history_snapshot(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`files_history_overlap_keys`", text)
                self.assertIn("`files_history_overlap_keys.size == 0`", text)
                self.assertIn(
                    "逐条同时对照 `frozen_history_normalized_keys` 和 `frozen_history_raw_keys`",
                    text,
                )
                self.assertIn(
                    "交集非空时不得提交这些重复文件",
                    text,
                )

    def test_terminal_counts_are_derived_from_disjoint_candidate_key_sets(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`low_threshold_keys`", text)
                self.assertIn("`failure_keys`", text)
                self.assertIn("两个集合必须互斥", text)
                self.assertIn(
                    "候选一旦写入失败终态，就必须先从 `low_threshold_keys` 删除",
                    text,
                )
                self.assertIn(
                    "禁止在循环中执行低阈值或失败汇总计数器的 `++`/累加",
                    text,
                )
                self.assertIn(
                    "53 个唯一候选、4 个历史命中、3 个候选失败时，低阈值最多只能是 46",
                    text,
                )

    def test_valid_page_cannot_report_completion_before_submission_terminal(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`run_stage`", text)
                self.assertIn("`PROCESSING -> MANIFEST_FROZEN -> SUBMISSION_TERMINAL -> REPORTING`", text)
                self.assertIn(
                    "禁止从 `PROCESSING` 直接进入 `REPORTING`",
                    text,
                )
                self.assertIn(
                    "不能以“已检查前 N 个候选”作为本轮完成条件",
                    text,
                )
                self.assertIn(
                    "把尚未开始事务的全部剩余候选一次性写入 `candidates_not_processed_due_execution_deadline`",
                    text,
                )
                self.assertIn(
                    "有效页面必须取得一次提交结果页，或取得明确的提交/完整性阻断证据",
                    text,
                )

    def test_candidate_scoped_definitive_failures_may_continue_scan(self):
        for kind, text in self._skill_texts():
            required_contract = (
                "可继续的候选级失败",
                "单候选下载事务使用受控完成期限",
                "`candidate_failures`",
                "重置监听代次",
                "继续处理下一个候选",
                "浏览器明确返回 `download_failed` 且确认没有文件落盘",
                "已经获得唯一下载事件和真实路径",
                "`downloaded_file_mismatch` 或 `duplicate_actual_file_in_same_run`",
                "`download_clicks_issued == download_events_confirmed + unresolved_download_count`",
            )
            for marker in required_contract:
                with self.subTest(kind=kind, marker=marker):
                    self.assertIn(marker, text)

            ledger_before_listener = text.index("先写入 `download_click_ledger`")
            listener_before_click = text.index("先把下载完成监听器置为 active")
            click_once = text.index(
                "监听器 active 之后才允许通过同一 `download_button_locator` 触发一次下载"
            )
            self.assertLess(ledger_before_listener, listener_before_click)
            self.assertLess(listener_before_click, click_once)

    def test_download_completion_deadline_precedes_outer_browser_tool_timeout(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                transaction_match = re.search(
                    r"`download_transaction_deadline_ms = (\d+)`", text
                )
                outer_match = re.search(
                    r"`browser_tool_timeout_ms >= (\d+)`", text
                )
                self.assertIsNotNone(transaction_match)
                self.assertIsNotNone(outer_match)
                transaction_ms = int(transaction_match.group(1))
                outer_ms = int(outer_match.group(1))
                self.assertGreaterEqual(transaction_ms, 180_000)
                self.assertGreaterEqual(outer_ms - transaction_ms, 30_000)
                self.assertIn("`Promise.race`", text)
                self.assertIn("`download.path()` 的等待也必须计入这个期限", text)
                self.assertIn("不得让外层工具超时先触发 `kernel reset`", text)

    def test_download_metadata_capability_is_proved_before_first_click(
        self,
    ):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("第一次下载点击前的能力预检", text)
                self.assertIn("非空 `absolute_path` 和 `filename`", text)
                self.assertIn("不得用一次真实下载来试探能力", text)
                self.assertIn("能力无法证明时必须在 0 次点击处停止下载", text)
                self.assertIn("以 `files: []` 提交本轮审计", text)

    def test_download_metadata_uses_download_path_and_path_basename(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn('waitForEvent("download")', text)
                self.assertIn("`download.path()`", text)
                self.assertIn(
                    "`absolute_path = await download.path({timeoutMs: remaining_timeout_ms})`",
                    text,
                )
                self.assertIn("`filename = basename(absolute_path)`", text)
                self.assertIn("路径最后一段是真实落盘文件名", text)
                self.assertIn("不得扫描 Downloads 目录猜测路径或文件名", text)

    def test_download_uses_playwright_listener_and_angular_button_click(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    'download_promise = current_page.playwright.waitForEvent("download", {timeoutMs: 60000})',
                    text,
                )
                self.assertIn(
                    'await download_button_locator.click({timeoutMs: 60000})',
                    text,
                )
                self.assertIn(
                    'absolute_path = await download.path({timeoutMs: remaining_timeout_ms})',
                    text,
                )
                self.assertIn(
                    "`<div class=\"btn download\">`",
                    text,
                )
                self.assertIn(
                    "没有 `href` 或 `download` 属性",
                    text,
                )
                self.assertIn("不得对它调用 `downloadMedia()`", text)
                self.assertNotIn(
                    'current_page.waitForEvent("download"',
                    text,
                )
                self.assertNotIn("download_button_locator.downloadMedia", text)

    def test_attachment_open_and_download_trigger_are_distinct_actions(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    "附件列表中的 `.file-name` 只用于打开详情弹层",
                    text,
                )
                self.assertIn(
                    "对精确文件名定位器执行一次普通 `click()`",
                    text,
                )
                self.assertIn(
                    "附件列表项不得调用 `downloadMedia()`",
                    text,
                )
                self.assertIn(
                    "打开弹层后目标 URL 必须保持不变",
                    text,
                )

    def test_detail_preview_reads_are_scoped_to_the_unique_preview(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn(
                    'detail_preview_locator = current_page.playwright.locator("app-file-preview")',
                    text,
                )
                self.assertIn(
                    'detail_filename_locator = detail_preview_locator.locator(".file-name")',
                    text,
                )
                self.assertIn(
                    'download_count_locator = detail_preview_locator.locator(".download-record")',
                    text,
                )
                self.assertIn(
                    'download_button_locator = detail_preview_locator.locator(".btn.download")',
                    text,
                )
                self.assertIn("`app-file-preview` 必须恰好只有 1 个可见实例", text)
                self.assertIn("不得从主题卡片、页面全局文本或其他容器读取下载次数", text)

    def test_detail_preview_has_a_safe_semantic_dismiss_contract(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("详情预览没有关闭按钮", text)
                self.assertIn(
                    'input[placeholder="可搜索当前星球的文件、主题"], input[placeholder="搜索星球、文件、主题"]',
                    text,
                )
                self.assertIn("搜索框中心必须位于 `.content` 矩形之外", text)
                self.assertIn("只点击一次这个语义定位器来让遮罩层收起", text)
                self.assertIn("`app-file-preview` 数量变为 0", text)
                self.assertIn("`detail_preview_close_failed`", text)
                self.assertIn("不得点击 `.file-preview-container`、`app-file-preview` 或 `.content` 来收尾", text)

    def test_every_window_candidate_has_exactly_one_terminal_classification(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("每个窗口内唯一候选必须且只能进入一个终态分类", text)
                self.assertIn("`candidates_not_processed_due_success_limit`", text)
                self.assertIn("`candidates_not_processed_due_global_abort`", text)
                self.assertIn("`candidates_not_processed_due_execution_deadline`", text)
                self.assertIn("`candidate_terminal_classification_total == within_36h`", text)
                self.assertIn("终态分类不平衡时不得冻结 manifest", text)

    def test_postclick_candidate_failures_continue_and_global_contamination_stops(
        self,
    ):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                safe_failure_section = self._level_three_section(
                    text, "可继续的候选级失败"
                )
                global_section = self._level_three_section(
                    text, "跨候选或全局污染（停止后续点击）"
                )
                for reason in (
                    "`download_terminal_timeout_after_retry`",
                    "`download_result_incomplete_after_retry`",
                    "`download_event_conflict`",
                    "`downloaded_file_mismatch`",
                    "`duplicate_actual_file_in_same_run`",
                ):
                    with self.subTest(kind=kind, reason=reason):
                        self.assertIn(reason, safe_failure_section)
                self.assertIn(
                    "即使点击后状态不明，也先隔离当前候选并继续处理下一个候选",
                    safe_failure_section,
                )
                self.assertIn(
                    "当前不确定候选不得加入 `files`",
                    safe_failure_section,
                )
                for reason in (
                    "`unexpected_download_without_armed_candidate`",
                    "`late_download_event_for_closed_candidate`",
                    "浏览器连接在有在途事务时重置",
                ):
                    with self.subTest(kind=kind, reason=reason):
                        self.assertIn(reason, global_section)
                self.assertIn("`abort_latch = true`", global_section)
                self.assertIn("不得点击下一个候选", global_section)
                self.assertIn(
                    "已经确认且通过自检的 `files` 仍须提交",
                    global_section,
                )
                self.assertIn(
                    "`manifest_blocked_by_download_integrity = true` 仅用于已确认 `files` 本身无法通过完整性自检",
                    text,
                )
                self.assertNotIn(
                    "硬中止后冻结当前 `run_id`、账本、已经确认的 `files`、失败候选和计数，只做只读汇报。不得提交普通 manifest",
                    text,
                )
                self.assertNotIn(
                    "终态超时、缺少路径或文件名、事件冲突表示当前候选状态不明，本轮不再继续点击",
                    text,
                )

                for unsafe_legacy_rule in (
                    "任何候选级失败都不得置 `abort_latch = true`",
                    "候选级失败不得阻止 manifest 提交",
                    "即使所有候选都失败，也提交 `files: []`",
                ):
                    with self.subTest(kind=kind, legacy=unsafe_legacy_rule):
                        self.assertNotIn(unsafe_legacy_rule, text)

    def test_postclick_candidate_failure_can_be_retried_next_round(self):
        for kind, text in self._skill_texts():
            with self.subTest(kind=kind):
                self.assertIn("`candidate_failures` 不写入最近 3 天成功历史", text)
                self.assertIn("下一轮可以重试该候选", text)
                self.assertIn("但这不影响本轮处理其他候选", text)


if __name__ == "__main__":
    unittest.main()
