import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ScriptVideoWorkflowFrontendContractTest(unittest.TestCase):
    def test_workbench_has_all_five_stages_and_shared_navigation(self):
        html = (PROJECT_ROOT / "src" / "frontend" / "script-video-workflow.html").read_text(encoding="utf-8")

        for stage in ("文案", "语音", "分镜", "图片", "对齐与成片"):
            self.assertIn(stage, html)
        self.assertIn("js/shared-navigation.js", html)
        self.assertIn("js/script-video-workflow.js", html)

    def test_frontend_uses_stage_endpoints_and_pauses_polling_when_hidden(self):
        javascript = (PROJECT_ROOT / "src" / "frontend" / "js" / "script-video-workflow.js").read_text(encoding="utf-8")

        self.assertIn("/api/script-video-workflows/jobs", javascript)
        self.assertIn("/storyboard", javascript)
        self.assertIn("/audio", javascript)
        self.assertIn("/images", javascript)
        self.assertIn("/render", javascript)
        self.assertIn("document.hidden", javascript)
        self.assertIn("visibilitychange", javascript)
        for method in (
            "submitDraft()",
            "startStoryboard()",
            "saveAudioChunks()",
            "startAudio()",
            "saveScenes()",
            "startImages()",
            "retryImage(sequence)",
            "startRender()",
            "loadWorkflow()",
        ):
            self.assertIn(method, javascript)

    def test_frontend_has_an_explicit_gemini_storyboard_planning_action(self):
        html = (PROJECT_ROOT / "src" / "frontend" / "script-video-workflow.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "src" / "frontend" / "js" / "script-video-workflow.js").read_text(encoding="utf-8")

        self.assertIn('id="start-storyboard-btn"', html)
        self.assertIn("投递 Gemini 规划分镜", html)
        self.assertIn("startStoryboard()", javascript)
        self.assertIn("storyboard_queued", javascript)

    def test_frontend_polls_only_active_stages_at_a_bounded_interval(self):
        javascript = (PROJECT_ROOT / "src" / "frontend" / "js" / "script-video-workflow.js").read_text(encoding="utf-8")
        polling_needed = javascript.split("isPollingNeeded()", 1)[1].split(
            "\n    startPolling()", 1
        )[0]

        self.assertIn("const SCRIPT_VIDEO_ACTIVE_POLL_MS = 10_000", javascript)
        self.assertIn("this.isBusy('audio')", polling_needed)
        self.assertIn("this.isBusy('images')", polling_needed)
        self.assertIn("this.isBusy('render')", polling_needed)
        self.assertIn("}, SCRIPT_VIDEO_ACTIVE_POLL_MS);", javascript)
        self.assertIn("/api/script-video-workflows/jobs?per_page=12&summary=1", javascript)
        self.assertIn("loadWorkflow({poll = false} = {})", javascript)
        self.assertIn("poll ? '?poll=1' : ''", javascript)
        self.assertIn("this.mergeWorkflowPoll", javascript)
        self.assertIn("this.loadWorkflow({poll: true})", javascript)
        self.assertIn("`${Number(scene.sequence)}:${scene.image_scene_hash || ''}`", javascript)

    def test_shared_navigation_has_a_generic_menu_entry_without_rebranding_existing_schemes(self):
        navigation = (PROJECT_ROOT / "src" / "frontend" / "js" / "shared-navigation.js").read_text(encoding="utf-8")

        self.assertIn("script-video-workflow.html", navigation)
        self.assertIn("script-video-workflow", navigation)
        self.assertIn("文案到成片", navigation)

    def test_workbench_uses_single_step_navigation_and_jianying_style_audio_controls(self):
        html = (PROJECT_ROOT / "src" / "frontend" / "script-video-workflow.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "src" / "frontend" / "js" / "script-video-workflow.js").read_text(encoding="utf-8")

        for step in range(1, 6):
            self.assertIn(f'data-workflow-step="{step}"', html)
        self.assertIn('id="workflow-prev-btn"', html)
        self.assertIn('id="workflow-next-btn"', html)
        self.assertIn('id="audio-waveform-canvas"', html)
        self.assertIn('id="audio-selection-overlay"', html)
        self.assertIn('data-audio-speed="1.25"', html)
        self.assertIn("renderStepVisibility()", javascript)
        self.assertIn("goToStep(step)", javascript)
        self.assertIn("drawAudioWaveform", javascript)
        self.assertIn("storyboard_failed", javascript)


if __name__ == "__main__":
    unittest.main()
