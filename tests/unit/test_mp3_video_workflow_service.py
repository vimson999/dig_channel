import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from services.gemini_browser import (
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    _clear_stale_singleton_files,
    _looks_like_page_shell_text,
    _looks_like_incomplete_response,
    _looks_like_singleton_launch_error,
    _promptless_submit_confirmed,
    _submit_state_summary,
    _upload_is_ready,
)
from services.mp3_video_workflow import (
    DEFAULT_GEMINI_INITIAL_PROMPT,
    DEFAULT_SPEED,
    PLANNER_VERSION,
    Mp3VideoWorkflowError,
    VISUAL_ARTIFACT_EXPECTED_SIZES,
    VISUAL_ARTIFACT_KEYS,
    VISUAL_ARTIFACT_VERSION,
    _child_id_from_result,
    _is_retryable_gemini_error,
    _visual_artifacts_ready,
    _write_data_url,
    audio_segment_from_plan,
    draw_audio_background,
    draw_closed_door_cover,
    draw_finance_cover,
    extract_audio_range,
    extract_title_candidates,
    fallback_plan,
    gemini_options_from_env,
    initial_gemini_prompt,
    validate_plan,
)


class Mp3VideoWorkflowServiceTest(unittest.TestCase):
    def test_fallback_plan_extracts_clean_title_and_audio_speed(self):
        plan = fallback_plan(
            "标题方案：算力调整假摔还是上车。核心观点：CPO 时代预期差。",
            audio={"filename": "demo.mp3", "duration_seconds": 120},
        )

        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)
        self.assertIn("算力调整", plan["title"])
        self.assertNotIn("标题方案", plan["title"])
        self.assertEqual(plan["cover_main_title"], "内资闭门会议")
        self.assertEqual(plan["xiaojunjun_title"], "内资闭门会议")
        self.assertEqual(plan["planner_version"], PLANNER_VERSION)

    def test_fallback_plan_extracts_gemini_investor_titles_and_clip_range(self):
        response = """
        1. 投资者视角长标题（3个版本）
        这三个标题兼顾了高信息密度与吸引力。

        内资闭门会 | 航空基本面拐点确立现价进入买入窗口 | 估值极寒跌至历史1%分位 | 油价大跌释放超额红利 | 260622
        内资闭门会 | 揭秘航空股左侧布局时机 | 隐形供给瓶颈引爆戴维斯双击 | 票价背后的真实需求反转 | 260622
        内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622

        4. 音频精华片段提取
        推荐截取时间戳：01:10 - 09:45 （预估时长约 8分35秒）
        """

        titles = extract_title_candidates(response)
        audio_range = extract_audio_range(response)
        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 800})

        self.assertEqual(
            titles[0],
            "航空基本面拐点确立现价进入买入窗口 | 估值极寒跌至历史1%分位 | 油价大跌释放超额红利",
        )
        self.assertIn(
            "揭秘航空股左侧布局时机 | 隐形供给瓶颈引爆戴维斯双击 | 票价背后的真实需求反转",
            titles,
        )
        self.assertEqual(audio_range, (70.0, 585.0))
        self.assertEqual(plan["audio"]["start_seconds"], 70)
        self.assertEqual(plan["audio"]["end_seconds"], 585)

    def test_fallback_plan_extracts_version_titles_and_separate_timestamps(self):
        response = """
        1. 长标题（3个版本）
        版本一： 内资闭门会 | AI风口引爆有色板块 | 铜铝镍锂估值修复 | 下半场Alpha个股机会 | 260621
        版本二： 内资闭门会 | 顺周期迎AI催化 | 黄金与海运双重红利 | 寻找性价比空间 | 260621
        版本三： 内资闭门会 | 供给硬约束+需求刚性 | 有色高景气度标的 | 资金回流防御首选 | 260621

        4. 精华片段提取
        开始时间戳：00:36
        结束时间戳：02:41
        """

        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 229})

        self.assertEqual(
            plan["title"],
            "AI风口引爆有色板块 | 铜铝镍锂估值修复 | 下半场Alpha个股机会",
        )
        self.assertEqual(plan["audio"]["start_seconds"], 36)
        self.assertEqual(plan["audio"]["end_seconds"], 161)

    def test_fallback_plan_extracts_flattened_gemini_version_titles(self):
        response = (
            "1. 长标题（投资者视角，高信息密度） "
            "版本一： 内资闭门会 | AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha | 260621 "
            "版本二： 内资闭门会 | 顺周期高景气度+AI催化 | 有色板块兼具红利与进攻属性 | 海运放开预期下的估值弹性 | 260621 "
            "版本三： 内资闭门会 | AI二三线标的全面补涨 | 资源加工材料估值洼地怎么选 | 黄金、铜、稀土的赔率与策略 | 260621 "
            "4. 精华片段提取 开始时间戳： 01:00 结束时间戳： 02:40"
        )

        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 229})

        self.assertEqual(
            plan["title"],
            "AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha",
        )
        self.assertEqual(plan["title_options"][:3], [
            "AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha",
            "顺周期高景气度+AI催化 | 有色板块兼具红利与进攻属性 | 海运放开预期下的估值弹性",
            "AI二三线标的全面补涨 | 资源加工材料估值洼地怎么选 | 黄金、铜、稀土的赔率与策略",
        ])
        self.assertEqual(plan["audio"]["start_seconds"], 60)
        self.assertEqual(plan["audio"]["end_seconds"], 160)

    def test_validate_plan_preserves_local_clip_range_when_planner_omits_it(self):
        fallback = fallback_plan(
            "内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622\n"
            "推荐截取时间戳：01:10 - 09:45",
            audio={"duration_seconds": 800},
        )
        plan = validate_plan(
            {
                "title": "内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622",
                "audio": {},
            },
            fallback=fallback,
            audio={"duration_seconds": 800},
        )

        self.assertEqual(
            plan["title"],
            "油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间",
        )
        self.assertEqual(plan["audio"]["start_seconds"], 70)
        self.assertEqual(plan["audio"]["end_seconds"], 585)

    def test_validate_plan_clamps_audio_range(self):
        fallback = fallback_plan("标题方案：CPO 时代预期差。", audio={"duration_seconds": 60})
        plan = validate_plan(
            {
                "title": "CPO 时代预期差",
                "audio": {"start_seconds": "00:10", "end_seconds": "99:00", "speed": 1.0},
            },
            fallback=fallback,
            audio={"duration_seconds": 60},
        )

        self.assertEqual(plan["audio"]["start_seconds"], 10)
        self.assertEqual(plan["audio"]["end_seconds"], 60)
        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)

    def test_audio_segment_uses_end_offset_for_publisher(self):
        segment = audio_segment_from_plan(
            {"path": "/tmp/demo.mp3", "duration_seconds": 100},
            {"audio": {"start_seconds": 12, "end_seconds": 72}},
        )

        self.assertEqual(segment["start"], 12)
        self.assertEqual(segment["end_offset"], 28)
        self.assertEqual(segment["speed"], DEFAULT_SPEED)

    def test_draw_visual_assets_create_files(self):
        plan = fallback_plan("标题方案：算力调整假摔还是上车。", audio={"duration_seconds": 12})
        with tempfile.TemporaryDirectory() as tmpdir:
            infographic = os.path.join(tmpdir, "infographic.png")
            img = Image.new("RGB", (1200, 700), "white")
            draw = ImageDraw.Draw(img)
            draw.rectangle([40, 40, 1160, 660], outline="black", width=4)
            draw.text((100, 100), "dummy", fill="black")
            img.save(infographic)

            outputs = [
                os.path.join(tmpdir, "finance.jpg"),
                os.path.join(tmpdir, "audio_bg.jpg"),
                os.path.join(tmpdir, "closed_cover.jpg"),
                os.path.join(tmpdir, "closed_bg.jpg"),
            ]
            draw_finance_cover(outputs[0], plan)
            draw_audio_background(outputs[1], plan, infographic)
            draw_closed_door_cover(outputs[2], plan, include_date=True)
            draw_closed_door_cover(outputs[3], plan, include_date=False)

            for path in outputs:
                self.assertTrue(os.path.exists(path), path)
                self.assertGreater(os.path.getsize(path), 10_000)

    def test_visual_artifacts_ready_requires_all_non_empty_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                Image.new("RGB", VISUAL_ARTIFACT_EXPECTED_SIZES[key], "white").save(path)
                artifacts[key] = {"path": path, "template_version": VISUAL_ARTIFACT_VERSION}

            self.assertTrue(_visual_artifacts_ready(artifacts))

            os.remove(artifacts["audio_background"]["path"])
            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_visual_artifacts_ready_rejects_old_template_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                Image.new("RGB", VISUAL_ARTIFACT_EXPECTED_SIZES[key], "white").save(path)
                artifacts[key] = {"path": path, "template_version": "old"}

            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_write_data_url_rejects_wrong_image_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.png"
            output = Path(tmpdir) / "output.png"
            Image.new("RGB", (64, 64), "white").save(source)
            data_url = "data:image/png;base64," + base64.b64encode(source.read_bytes()).decode("ascii")

            with self.assertRaises(Mp3VideoWorkflowError):
                _write_data_url(data_url, str(output), expected_size=(1280, 720), label="test")

    def test_gemini_response_detection_rejects_page_shell(self):
        shell_text = (
            "跳至主要内容 Gemini 发起新对话 搜索对话内容 库 Gem 笔记本 "
            "最近 与 Gemini 对话 MP3 demo.mp3 Pro Gemini 是一款 AI 工具"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = GeminiBrowserWorker(GeminiBrowserOptions(user_data_dir=tmpdir))
            valid_response = (
                "这份音频的核心结论是 AI 算力资产被系统性低估。报告先解释需求端的训练和推理增长，"
                "再讨论供给端的数据中心、电力、网络和芯片约束，最后给出市场可能重新定价的路径，"
                "整体内容具有明确观点、事实链条和可用于视频策划的结构化信息。并且包含足够长的正文，"
                "用于区分真实回答和页面外壳。"
            )

            self.assertTrue(_looks_like_page_shell_text(shell_text))
            self.assertFalse(worker._looks_like_response(shell_text, ""))
            self.assertTrue(worker._looks_like_response(valid_response, ""))

    def test_mp3_gemini_defaults_wait_for_long_audio(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_GEMINI_RESPONSE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_IMAGE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_TOTAL_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_UPLOAD_TIMEOUT": "",
            "GEMINI_MP3_VIDEO_UPLOAD_TIMEOUT": "",
        }, clear=False):
            options = gemini_options_from_env()

        self.assertGreaterEqual(options.response_timeout_seconds, 900)
        self.assertGreaterEqual(options.image_timeout_seconds, 900)
        self.assertGreaterEqual(options.total_timeout_seconds, 2400)
        self.assertGreaterEqual(options.upload_timeout_seconds, 900)

    def test_mp3_gemini_initial_prompt_is_never_empty(self):
        self.assertEqual(initial_gemini_prompt({}), DEFAULT_GEMINI_INITIAL_PROMPT)
        self.assertEqual(initial_gemini_prompt({"gemini_prompt": "请处理这个音频"}), "请处理这个音频")

    def test_mp3_gemini_retryable_error_classification(self):
        self.assertTrue(_is_retryable_gemini_error("等待 Gemini 生成图片超时"))
        self.assertTrue(_is_retryable_gemini_error("页面自动化超时: Locator.click: Timeout 10000ms exceeded"))
        self.assertTrue(_is_retryable_gemini_error("Gemini 提示词未成功提交: 未找到 Gemini 发送按钮"))
        self.assertFalse(_is_retryable_gemini_error("音频文件不存在: demo.mp3"))

    def test_gemini_upload_ready_requires_file_name_and_enabled_submit_button(self):
        self.assertFalse(_upload_is_ready({"hasName": False, "busy": False, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": True, "busy": False, "canceled": False, "enabledSend": False}))
        self.assertTrue(_upload_is_ready({"hasName": True, "busy": False, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": True, "busy": True, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": False, "busy": False, "canceled": False, "enabledSend": False}))

    def test_gemini_response_wait_rejects_dangling_timestamp(self):
        self.assertTrue(_looks_like_incomplete_response("4. 精华片段提取 开始时间戳： 00:36"))
        self.assertFalse(_looks_like_incomplete_response("4. 精华片段提取 开始时间戳： 00:36 结束时间戳：02:41"))
        self.assertFalse(_looks_like_incomplete_response("推荐截取时间戳：01:10 - 09:45 （预估时长约 8分35秒）"))

    def test_promptless_submit_wait_does_not_treat_ready_button_as_submitted(self):
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": True}, "", generating=False))
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": False}, "", generating=False))
        self.assertTrue(_promptless_submit_confirmed({"enabledSend": False}, "", generating=False, clicked_send=True))
        self.assertTrue(_promptless_submit_confirmed({"enabledSend": True}, "", generating=True))
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": False}, "还在输入框里", generating=False, clicked_send=True))

    def test_submit_state_summary_keeps_compact_diagnostics(self):
        summary = _submit_state_summary(
            {"hasName": True, "busy": False, "canceled": False, "enabledSend": False, "sendButtonCount": 0},
            "还在输入框里",
        )

        self.assertIn("enabledSend=False", summary)
        self.assertIn("sendButtonCount=0", summary)
        self.assertIn("composer_chars=6", summary)

    def test_stale_gemini_singleton_files_are_cleared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                (profile_dir / name).write_text("stale", encoding="utf-8")

            cleared = _clear_stale_singleton_files(profile_dir, "host-99999999（pid 99999999 未运行/疑似残留）")

            self.assertTrue(cleared)
            self.assertFalse((profile_dir / "SingletonLock").exists())
            self.assertFalse((profile_dir / "SingletonCookie").exists())
            self.assertFalse((profile_dir / "SingletonSocket").exists())

    def test_running_gemini_singleton_files_are_not_cleared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)
            lock_path = profile_dir / "SingletonLock"
            lock_path.write_text("active", encoding="utf-8")

            cleared = _clear_stale_singleton_files(profile_dir, f"host-{os.getpid()}（pid {os.getpid()} 运行中）")

            self.assertFalse(cleared)
            self.assertTrue(lock_path.exists())

    def test_singleton_launch_error_detection_is_specific(self):
        self.assertTrue(_looks_like_singleton_launch_error("Failed to create /tmp/profile/SingletonLock: File exists"))
        self.assertTrue(_looks_like_singleton_launch_error("Failed to create a ProcessSingleton for your profile directory. Aborting now"))
        self.assertFalse(_looks_like_singleton_launch_error("Target page, context or browser has been closed"))

    def test_video_child_id_can_be_recovered_for_deferred_resume(self):
        result = {
            "children": {
                "vertical_video": {
                    "job_id": "child-vertical",
                    "status": "processing",
                }
            }
        }

        self.assertEqual(_child_id_from_result(result, "vertical_video"), "child-vertical")


if __name__ == "__main__":
    unittest.main()
