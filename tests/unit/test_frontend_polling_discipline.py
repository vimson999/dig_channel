import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FrontendPollingDisciplineTest(unittest.TestCase):
    def test_linked_content_uses_a_dedicated_detail_page_and_resumable_backfill(self):
        lab_source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        detail_source = (PROJECT_ROOT / "src" / "frontend" / "js" / "linked-content-detail.js").read_text(encoding="utf-8")
        detail_html = (PROJECT_ROOT / "src" / "frontend" / "linked-content-detail.html").read_text(encoding="utf-8")

        self.assertIn('linked-content-detail.html?id=', lab_source)
        self.assertIn('>查看更多</span>', lab_source)
        self.assertIn('/external-documents/backfill/tasks', detail_source)
        self.assertIn('/external-documents/backfill/tasks/${encodeURIComponent(task.id)}', detail_source)
        self.assertIn('id="detail-view"', detail_html)
        self.assertIn('id="list-view"', detail_html)

    def test_zsxq_manual_download_controls_are_guarded_by_group(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        update_controls = source.split("function updateBatchDownloadButton()", 1)[1].split(
            "\nfunction readManualPlanLimit()", 1
        )[0]
        create_workflow = source.split("async function createManualDownloadWorkflow()", 1)[1].split(
            "\nfunction buildManualDownloadPayload", 1
        )[0]

        self.assertIn("const SYSTEM_SYNC_GROUP_ID = '28888112822211'", source)
        self.assertIn("if (!isSelectedSystemSyncGroup())", update_controls)
        self.assertIn("hideManualDownloadControls()", update_controls)
        self.assertIn("if (!isSelectedSystemSyncGroup())", create_workflow)
        self.assertIn("manualDownloadEnabled ?", source)

    def test_zsxq_publish_panel_is_guarded_by_junjun_podcast_group(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        load_topics = source.split("async function loadTopics(group, options = {})", 1)[1].split(
            "\nasync function loadOfficialMenus", 1
        )[0]
        publish_topic = source.split("async function publishTopic()", 1)[1].split(
            "\nfunction setPublishStatus", 1
        )[0]

        self.assertIn("const PUBLISH_GROUP_ID = '15554855514112'", source)
        self.assertIn("renderPublishPanelVisibility()", load_topics)
        self.assertNotIn("publishPanel.classList.remove('hidden')", load_topics)
        self.assertIn("if (!isSelectedPublishGroup())", publish_topic)

    def test_zsxq_hot_rank_is_guarded_by_system_sync_group(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        load_topics = source.split("async function loadTopics(group, options = {})", 1)[1].split(
            "\nasync function loadOfficialMenus", 1
        )[0]
        render_rank = source.split("function renderHotRankList()", 1)[1].split(
            "\nfunction buildHotRankEntries", 1
        )[0]

        self.assertIn("renderHotRankPanelVisibility()", load_topics)
        self.assertIn("renderHotRankPanelVisibility()", render_rank)
        self.assertIn("if (!isSelectedSystemSyncGroup()) return", render_rank)

    def test_zsxq_sync_polling_is_single_flight(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        polling = source.split("function startSyncPolling()", 1)[1].split("\nfunction stopSyncPolling()", 1)[0]

        self.assertIn("syncPollInFlight", source)
        self.assertIn("if (state.syncPollInFlight) return", polling)
        self.assertIn("state.syncPollInFlight = true", polling)
        self.assertIn("finally", polling)
        self.assertIn("state.syncPollInFlight = false", polling)

    def test_favorite_filter_commits_only_after_the_replacement_list_loads(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "src" / "frontend" / "zsxq-lab.html").read_text(encoding="utf-8")
        handler = source.split("async function requestFavoriteFilterChange()", 1)[1].split(
            "\nfunction topicKey(topic)", 1
        )[0]

        self.assertIn("favoriteFilterPending", source)
        self.assertIn("favoriteOnlyOverride: nextFavoriteOnly", handler)
        self.assertIn("if (!succeeded || !stillViewingSameTab)", handler)
        self.assertIn("收藏筛选未生效", handler)
        self.assertIn("state.favoriteOnly = nextFavoriteOnly", handler)
        self.assertIn("state.tabStates.set(tabStateKey(tab.id), candidateTabState)", handler)
        self.assertIn('data-favorite-filter-label', html)

    def test_zsxq_sync_queue_explains_waiting_and_uses_activity_time(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")

        self.assertIn("function syncRunActivityTime(run)", source)
        self.assertIn("function renderSyncQueueMessage(run)", source)
        self.assertIn("等待同步 worker 领取", source)
        self.assertIn("run.blocking_run?.group_name", source)
        self.assertIn("最近活动：${formatZsxqTime(syncRunActivityTime(latest))}", source)
        self.assertIn("function renderSyncRetryMessage(run)", source)
        self.assertIn("function renderSyncTimingMessage(run)", source)
        self.assertIn("retrying: '重试等待'", source)


if __name__ == "__main__":
    unittest.main()
