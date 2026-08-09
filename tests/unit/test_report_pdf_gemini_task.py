from services.gemini_browser import GeminiBrowserOptions
from tasks.gemini_browser import _apply_total_timeout_budget


def test_total_timeout_budget_reserves_a_full_window_for_each_followup():
    options = GeminiBrowserOptions(
        response_timeout_seconds=600,
        total_timeout_seconds=900,
        ready_timeout_seconds=90,
        upload_timeout_seconds=300,
    )

    _apply_total_timeout_budget(options, ["复核并生成最终长文"])

    assert options.total_timeout_seconds == 1710


def test_total_timeout_budget_preserves_a_larger_operator_deadline():
    options = GeminiBrowserOptions(
        response_timeout_seconds=600,
        total_timeout_seconds=3600,
        ready_timeout_seconds=90,
        upload_timeout_seconds=300,
    )

    _apply_total_timeout_budget(options, ["追问一", "追问二"])

    assert options.total_timeout_seconds == 3600
