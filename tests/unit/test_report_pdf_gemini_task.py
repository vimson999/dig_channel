import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.gemini_browser import GeminiBrowserOptions, GeminiBrowserWorker
from tasks.gemini_browser import _apply_total_timeout_budget, _response_contract_callbacks
from tests.unit.test_report_audio_gem_v2 import PDF_FILENAME, valid_payload


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


def _worker_without_browser():
    worker = object.__new__(GeminiBrowserWorker)
    worker.options = GeminiBrowserOptions()
    worker._current_job_id = "contract-test"
    return worker


def _initial_turn(text="initial-invalid"):
    return [{
        "index": 1,
        "kind": "initial",
        "prompt": "minimal-trigger",
        "response_text": text,
        "page_url": "https://gemini.test/gem",
        "completed_at": "2026-08-12T00:00:00Z",
    }]


def test_role_stop_control_is_seen_as_active_gemini_generation():
    """Gemini may render its stop control as a role button, not a button tag."""
    worker = _worker_without_browser()

    class RoleStopControlPage:
        def evaluate(self, _script, selectors):
            return "[role='button'][aria-label*='Stop']" in selectors

    assert worker._is_generating(RoleStopControlPage()) is True


def test_wait_for_response_keeps_waiting_through_a_bounded_active_generation_grace(monkeypatch):
    """A visible stop control at the response deadline means Gemini is still working."""
    worker = _worker_without_browser()
    worker.options = GeminiBrowserOptions(
        response_timeout_seconds=2,
        response_stable_seconds=0,
        total_timeout_seconds=30,
        min_response_chars=1,
    )
    monkeypatch.setenv("GEMINI_BROWSER_ACTIVE_GENERATION_GRACE_SECONDS", "10")

    class Clock:
        value = 0.0

        def monotonic(self):
            current = self.value
            self.value += 0.5
            return current

    clock = Clock()

    def response_after_initial_deadline(*_args):
        return "完成" if clock.value >= 3.0 else ""

    with patch("services.gemini_browser.time.monotonic", side_effect=clock.monotonic), \
        patch("services.gemini_browser.time.sleep"), \
        patch.object(worker, "_guard_total_timeout"), \
        patch.object(worker, "_raise_if_login_or_captcha"), \
        patch.object(worker, "_latest_response_text", side_effect=response_after_initial_deadline), \
        patch.object(worker, "_composer_text", return_value=""), \
        patch.object(worker, "_composer_submit_state", return_value={"enabledSend": False, "busy": False}), \
        patch.object(worker, "_is_generating", side_effect=[False, True, True, True, False]):
        result = worker._wait_for_response(
            page=SimpleNamespace(),
            prompt="请处理 PDF",
            baseline_text="",
            started_at=0.0,
        )

    assert result == {"text": "完成"}


def test_valid_contract_response_does_not_submit_a_repair_turn():
    worker = _worker_without_browser()
    validator = Mock(return_value={"valid": True, "error_codes": []})
    prompt_builder = Mock(return_value="unused")

    with patch.object(worker, "_submit_prompt") as submit:
        final_text, diagnostics = worker._repair_response_contract(
            page=SimpleNamespace(url="https://gemini.test/gem"),
            started=0.0,
            response_text="valid-json",
            turns=_initial_turn("valid-json"),
            validator=validator,
            repair_prompt_builder=prompt_builder,
            max_repairs=1,
            progress_callback=None,
        )

    assert final_text == "valid-json"
    assert diagnostics["valid"] is True
    assert diagnostics["repair_attempt_count"] == 0
    submit.assert_not_called()
    prompt_builder.assert_not_called()


def test_invalid_contract_response_gets_one_repair_and_replacement_wins():
    worker = _worker_without_browser()
    turns = _initial_turn()
    validator = Mock(side_effect=[
        {"valid": False, "error_codes": ["schema_version"], "errors": [{"code": "schema_version"}]},
        {"valid": True, "error_codes": []},
    ])
    prompt_builder = Mock(return_value="return-complete-replacement-json")

    with patch.object(worker, "_body_text", return_value="baseline"), \
        patch.object(worker, "_submit_prompt") as submit, \
        patch.object(worker, "_guard_total_timeout"), \
        patch.object(worker, "_wait_for_response", return_value={"text": "repaired-valid-json"}), \
        patch.object(worker, "_emit"):
        final_text, diagnostics = worker._repair_response_contract(
            page=SimpleNamespace(url="https://gemini.test/gem"),
            started=0.0,
            response_text="initial-invalid",
            turns=turns,
            validator=validator,
            repair_prompt_builder=prompt_builder,
            max_repairs=1,
            progress_callback=None,
        )

    assert final_text == "repaired-valid-json"
    assert diagnostics["valid"] is True
    assert diagnostics["repair_attempt_count"] == 1
    assert turns[-1]["kind"] == "response_contract_repair"
    assert turns[-1]["response_text"] == "repaired-valid-json"
    submit.assert_called_once_with(SimpleNamespace(url="https://gemini.test/gem"), "return-complete-replacement-json")


def test_second_invalid_contract_response_falls_back_without_a_second_repair():
    worker = _worker_without_browser()
    validator = Mock(side_effect=[
        {"valid": False, "error_codes": ["json_parse"], "errors": [{"code": "json_parse"}]},
        {"valid": False, "error_codes": ["script_too_short"], "errors": [{"code": "script_too_short"}]},
    ])

    with patch.object(worker, "_body_text", return_value="baseline"), \
        patch.object(worker, "_submit_prompt") as submit, \
        patch.object(worker, "_guard_total_timeout"), \
        patch.object(worker, "_wait_for_response", return_value={"text": "still-invalid"}), \
        patch.object(worker, "_emit"):
        final_text, diagnostics = worker._repair_response_contract(
            page=SimpleNamespace(url="https://gemini.test/gem"),
            started=0.0,
            response_text="initial-invalid",
            turns=_initial_turn(),
            validator=validator,
            repair_prompt_builder=Mock(return_value="repair-once"),
            max_repairs=1,
            progress_callback=None,
        )

    assert final_text == "still-invalid"
    assert diagnostics["valid"] is False
    assert diagnostics["error_codes"] == ["script_too_short"]
    assert diagnostics["repair_attempt_count"] == 1
    submit.assert_called_once()
    assert validator.call_count == 2


def test_report_audio_v2_contract_callbacks_validate_and_build_targeted_repair():
    validator, repair_builder, max_repairs = _response_contract_callbacks({
        "response_contract": "report_audio_gem_v2",
        "contract_expected_pdf_filename": PDF_FILENAME,
        "contract_minimum_script_chars": 20,
        "contract_max_repairs": 1,
    })

    valid = validator(json.dumps(valid_payload(), ensure_ascii=False))
    assert valid["valid"] is True
    assert valid["error_codes"] == []

    invalid = validator('{"schema_version":"wrong"}')
    assert invalid["valid"] is False
    repair_prompt = repair_builder(invalid)
    assert "完整替换" in repair_prompt
    assert "schema_version" in repair_prompt
    assert max_repairs == 1


def test_absent_response_contract_keeps_generic_gemini_behavior():
    validator, repair_builder, max_repairs = _response_contract_callbacks({})

    assert validator is None
    assert repair_builder is None
    assert max_repairs == 0
