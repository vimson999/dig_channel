from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_insights_stale_refresh_behavior():
    contract = PROJECT_ROOT / "tests/frontend/zsxq-insights-refresh.test.cjs"
    completed = subprocess.run(
        ["node", str(contract)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_insights_frontend_uses_bounded_server_pages_and_stale_while_refresh_cache():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/zsxq-insights.js"
    ).read_text(encoding="utf-8")
    refresh_helper = (
        PROJECT_ROOT / "src/frontend/js/zsxq-insights-refresh.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "src/frontend/zsxq-insights.html"
    ).read_text(encoding="utf-8")

    assert "pageSize: 48" in javascript
    assert "page_size: String(state.pageSize)" in javascript
    assert "page_size: '5000'" not in javascript
    assert "RESPONSE_CACHE_TTL_MS = 60_000" in javascript
    assert "sessionStorage.getItem(SESSION_CACHE_KEY)" in javascript
    assert "refreshInsightsInBackground" in javascript
    assert "window.ZsxqInsightsRefresh.refresh" in javascript
    assert "_refresh=1" not in javascript
    assert "_refresh=1" in refresh_helper
    assert "cache: 'no-store'" in javascript
    assert "loadInsights({ append: true })" in javascript
    helper_tag = "js/zsxq-insights-refresh.js?v=20260907-server-refresh"
    page_tag = "js/zsxq-insights.js?v=20260907-server-refresh"
    assert html.index(helper_tag) < html.index(page_tag)


def test_insights_view_presets_have_persistent_distinct_meanings():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/zsxq-insights.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "src/frontend/zsxq-insights.html"
    ).read_text(encoding="utf-8")

    assert "overview: { theme: 'ai', sort: 'latest'" in javascript
    assert "trend: { theme: 'ai-content', sort: 'latest'" in javascript
    assert "opportunity: { theme: 'ai-sales', sort: 'signal'" in javascript
    assert "aria-current" in javascript
    assert "全景概览" in html
    assert "趋势追踪" in html
    assert "机会筛选" in html
    assert "主题下钻" in html


def test_topic_feed_exposes_yiren_status_and_formats_known_rich_text_tokens():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/zsxq-insights.js"
    ).read_text(encoding="utf-8")
    stylesheet = (
        PROJECT_ROOT / "src/frontend/css/zsxq-insights.css"
    ).read_text(encoding="utf-8")

    assert "renderYirenChip(topic)" in javascript
    assert "yiren_recognition" in javascript
    assert "renderTopicContent" in javascript
    assert "type === 'text_bold'" in javascript
    assert "type === 'mention'" in javascript
    assert ".yiren-recognized" in stylesheet
    assert ".rich-article" in stylesheet
