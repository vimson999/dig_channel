import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / ".codex-skill-staging" / "doubao-dialogue-tts"
SCRIPT = SKILL_ROOT / "scripts" / "dialogue_tts.py"
PROJECT_CONFIG = PROJECT_ROOT / ".doubao-dialogue-tts.json"
FIXED_OPENING = "哈喽兄弟们好，欢迎回到我的频道，我是你们的老朋友小君君。"


def manifest_with(*, opening: str = FIXED_OPENING) -> dict:
    return {
        "schema_version": "doubao-dialogue-v1",
        "title": "频道 Profile 回归测试",
        "model": "seed-tts-2.0-standard",
        "voices": {
            "host": {
                "label": "清澈",
                "voice_id": "zh_female_qingchezizi_uranus_bigtts",
            },
            "source": {
                "label": "刘飞",
                "voice_id": "zh_male_liufei_uranus_bigtts",
            },
        },
        "segments": [
            {
                "id": "001",
                "slug": "host-opening",
                "role": "host",
                "kind": "opening",
                "text": f"{opening} 今天一起听懂洪总的判断。",
                "pause_after_ms": 650,
            },
            {
                "id": "002",
                "slug": "source-01",
                "role": "source",
                "kind": "source",
                "text": "洪灏在原文中写道：市场会重新评估风险溢价。",
                "pause_after_ms": 650,
            },
        ],
    }


def run_dry(tmp_path: Path, manifest: dict, *, project_default: bool = True):
    project = tmp_path / "channel-project"
    artifact = project / "artifacts" / "episode"
    artifact.mkdir(parents=True)
    if project_default:
        (project / ".doubao-dialogue-tts.json").write_text(
            json.dumps(
                {
                    "schema_version": "doubao-dialogue-project-v1",
                    "default_profile": "xiaojunjun-honghao",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    manifest_path = artifact / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            str(manifest_path),
            "--output-dir",
            str(artifact / "output"),
            "--dry-run",
        ],
        cwd=project,
        capture_output=True,
        text=True,
    )


def test_current_project_selects_xiaojunjun_profile():
    config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))

    assert config == {
        "schema_version": "doubao-dialogue-project-v1",
        "default_profile": "xiaojunjun-honghao",
    }


def test_project_default_profile_applies_voice_defaults_and_is_visible(tmp_path):
    result = run_dry(tmp_path, manifest_with())

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["profile"] == {
        "id": "xiaojunjun-honghao",
        "source": "project_default",
    }
    assert summary["voice_settings"]["host"]["speed"] == 1.08
    assert summary["voice_settings"]["host"]["speed_source"] == "profile_default"
    assert summary["voice_settings"]["source"]["speed"] == 1.0
    assert summary["api_billing_boundary"] == "first_external_tts_request"
    assert summary["external_data_transfer"] == "not_started_dry_run"


def test_profile_rejects_opening_without_fixed_xiaojunjun_sentence(tmp_path):
    result = run_dry(tmp_path, manifest_with(opening="大家好，欢迎来到宏观研报精读。"))

    assert result.returncode != 0
    assert "fixed opening" in result.stderr


def test_profile_rejects_author_full_name_in_host_speech(tmp_path):
    manifest = manifest_with()
    manifest["segments"].append(
        {
            "id": "003",
            "slug": "host-commentary",
            "role": "host",
            "kind": "commentary",
            "text": "洪灏这段真正想表达的是政策可信度。",
        }
    )

    result = run_dry(tmp_path, manifest)

    assert result.returncode != 0
    assert "洪灏" in result.stderr
    assert "洪总" in result.stderr


def test_profile_allows_author_full_name_inside_source_segment(tmp_path):
    result = run_dry(tmp_path, manifest_with())

    assert result.returncode == 0, result.stderr


def test_profile_rejects_backstage_process_language_in_host_speech(tmp_path):
    manifest = manifest_with()
    manifest["segments"].append(
        {
            "id": "003",
            "slug": "host-commentary",
            "role": "host",
            "kind": "commentary",
            "text": "原文没动，但我们先做一处事实核对。",
        }
    )

    result = run_dry(tmp_path, manifest)

    assert result.returncode != 0
    assert "backstage process language" in result.stderr


def test_explicit_generic_mode_does_not_inherit_project_profile(tmp_path):
    manifest = manifest_with(opening="普通节目的开场。")
    manifest["editorial"] = {"profile_id": "generic"}
    manifest["segments"][0]["text"] = "普通开场，也可以直接说洪灏。"

    result = run_dry(tmp_path, manifest)

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["profile"] == {"id": "generic", "source": "manifest_override"}
    assert summary["voice_settings"]["host"]["speed"] == 1.0
    assert summary["voice_settings"]["host"]["speed_source"] == "generic_default"


def test_explicit_episode_speed_override_wins_over_profile_default(tmp_path):
    manifest = deepcopy(manifest_with())
    manifest["voices"]["host"]["speed"] = 1.2

    result = run_dry(tmp_path, manifest)

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["voice_settings"]["host"]["speed"] == 1.2
    assert summary["voice_settings"]["host"]["speed_source"] == "manifest_override"
