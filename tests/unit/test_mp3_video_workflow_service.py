import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from sqlalchemy.exc import OperationalError

from api.mp3_video_workflow import (
    _charging_pay_from_upload_payload as _api_charging_pay_from_upload_payload,
    _clean_bilibili_upload_title as _api_clean_bilibili_upload_title,
    _clean_ds_title as _api_clean_ds_title,
    _default_bilibili_title as _api_default_bilibili_title,
    _ds_extract_payload as _api_ds_extract_payload,
)
from models.task import PublicationTask
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
from services.bilibili.uploader import BiliUploader
from services.mp3_video_workflow import (
    DEFAULT_GEMINI_INITIAL_PROMPT,
    DEFAULT_SPEED,
    DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT,
    PLANNER_VERSION,
    Mp3VideoWorkflowError,
    VISUAL_ARTIFACT_EXPECTED_SIZES,
    VISUAL_ARTIFACT_KEYS,
    VISUAL_ARTIFACT_VERSION,
    _build_transcript_user_prompt,
    _child_inline_wait_seconds,
    _child_id_from_result,
    _db_retry,
    _finance_cover_highlight_text,
    _generate_from_transcript,
    _infographic_bullets,
    _is_retryable_gemini_error,
    _keyframe_artifacts_ready,
    _parent_max_defers,
    _parent_retry_delay_seconds,
    _publisher_child_generate_subtitles,
    _publisher_result_summary,
    _publisher_video_title,
    _reusable_video_summary,
    _video_summary_ready,
    _visual_artifacts_ready,
    _without_waiting_state,
    _write_data_url,
    _workflow_generate_subtitles,
    _workflow_render_mode,
    audio_segment_from_plan,
    draw_audio_background,
    draw_closed_door_cover,
    draw_finance_cover,
    draw_transcript_infographic,
    deepseek_transcript_response,
    extract_closed_door_video_titles,
    extract_audio_range,
    extract_title_candidates,
    fallback_plan,
    generate_video_keyframes,
    gemini_options_from_env,
    initial_gemini_prompt,
    initial_transcript_prompt,
    plan_from_direct_ds_response,
    transcript_model,
    validate_plan,
    _validate_strict_ds_plan,
)


class Mp3VideoWorkflowServiceTest(unittest.TestCase):
    def test_api_closed_door_title_ignores_prompt_placeholder(self):
        actual = "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07"
        dirty = (
            "尝试优化下标题，使用 内资闭门会 / xxxx / xxxx / xxxx / 当日日期) 这样的格式来写 "
            f"{actual}"
        )

        self.assertEqual(_api_clean_ds_title(dirty, prefer_closed_door=True), actual)
        self.assertEqual(_api_clean_bilibili_upload_title(dirty), actual)

    def test_api_default_bilibili_title_cleans_plan_fallback(self):
        actual = "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07"
        result = {
            "plan": {
                "xiaojunjun_video_title": (
                    "内资闭门会 / xxxx / xxxx / xxxx / 当日日期) "
                    f"{actual}"
                )
            }
        }

        self.assertEqual(_api_default_bilibili_title(result, {}, "landscape"), actual)

    def test_api_ds_extract_handles_bracketed_clip_and_quoted_title_words(self):
        response = (
            "1. 标题版本一（信息密度高、点击率强）\n"
            "东北电子内部闭门会深度解读：华为“涛”=13年前的新能源车\n\n"
            "2. 标题版本二（另一种视角）\n"
            "信息差：华为“涛”不是技术概念，而是中国半导体产业的“宁德时代时刻”！未来10年结构性投资机会\n\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n"
        )

        payload = _api_ds_extract_payload({
            "source": {"response_text": response},
        })

        self.assertIn("东北电子内部闭门会深度解读", payload["title_1"])
        self.assertIn("华为", payload["title_2"])
        self.assertIn("未来10年结构性投资机会", payload["title_2"])
        self.assertNotEqual(payload["title_2"], "宁德时代时刻")
        self.assertEqual(payload["clip"]["start_text"], "02:36")
        self.assertEqual(payload["clip"]["end_text"], "36:41")

    def test_api_bilibili_charging_payload_uses_preview_and_level(self):
        payload = _api_charging_pay_from_upload_payload({
            "charging_pay": {
                "enabled": True,
                "preview_end_time": "00:03:00",
                "upower_level_id": "level-1",
                "upower_unit_price": "68",
            }
        })

        self.assertEqual(payload["preview"]["start_time"], 0)
        self.assertEqual(payload["preview"]["end_time"], 180)
        self.assertEqual(payload["preview_end_time"], 180)
        self.assertEqual(payload["upower_level_id"], "level-1")
        self.assertEqual(payload["upower_unit_price"], 68)

    def test_bilibili_charging_options_parse_upower_levels(self):
        uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
        info = {
            "upower_auth": 1,
            "upower_level_list": [
                {
                    "level_id": "30",
                    "publish_info": {
                        "level_title_str": "30元档 金牌富豪",
                        "upower_unit_price": 30,
                    },
                },
                {
                    "upower_level_id": "68",
                    "level_title_str": "68元档 顶流富豪",
                    "upower_unit_price": "68",
                },
            ],
        }

        with patch.object(BiliUploader, "_fetch_charging_pay_info", return_value=info):
            result = uploader.charging_pay_options()

        self.assertEqual(result["default_level_id"], "30")
        self.assertEqual(result["levels"][0]["label"], "30元档 金牌富豪")
        self.assertEqual(result["levels"][0]["upower_unit_price"], 30)
        self.assertEqual(result["levels"][1]["id"], "68")

    def test_api_bilibili_charging_payload_rejects_invalid_time_range(self):
        with self.assertRaisesRegex(ValueError, "充电试看结束时间"):
            _api_charging_pay_from_upload_payload({
                "charging_pay": {
                    "enabled": True,
                    "preview": {"start_time": 30, "end_time": 20},
                }
            })

    def test_direct_ds_response_without_timestamp_fails_before_visuals(self):
        response = (
            "1. 投资视角标题\n"
            "Micro-TEC核心器件国产替代进入关键阶段\n"
            "2. 投资视角标题版本二\n"
            "国产突破内挤压工艺，出口管制误解消除\n"
            "4. 最精华片段\n"
            "未识别到时间戳\n"
            "6. 优化标题\n"
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07\n"
            "7. 优化标题\n"
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            with self.assertRaisesRegex(Mp3VideoWorkflowError, "时间戳"):
                plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 3600})

    def test_direct_ds_response_with_required_sections_builds_plan(self):
        response = (
            "1. 投资视角标题（版本一）\n"
            "专家密讲：Micro-TEC光模块核心器件国产替代关键时刻，复兴科技订单超预期\n"
            "2. 投资视角标题（版本二）\n"
            "独家：光模块升级带动Micro-TEC需求井喷，真正稀缺的是内挤压工艺\n"
            "3. 精简短标题\n"
            "Micro-TEC国产替代加速，复兴科技订单超预期\n"
            "4. 最精华片段\n"
            "开始时间戳：00:14:27.900\n"
            "结束时间戳：00:39:27.900\n"
            "6. 优化标题\n"
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07\n"
            "7. 优化标题\n"
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 4260})
        _validate_strict_ds_plan(plan)

        self.assertEqual(plan["audio"]["start_seconds"], 867.9)
        self.assertEqual(plan["audio"]["end_seconds"], 2367.9)
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07",
        )

    def test_direct_ds_response_skips_standalone_section_headings(self):
        response = (
            "1. 标题版本一（信息密度高、点击率强）\n"
            "东北电子内部闭门会深度解读：华为涛等于13年前的新能源车\n\n"
            "2. 标题版本二（另一种视角）\n"
            "信息差：华为涛不是技术概念，而是中国半导体产业的宁德时代时刻\n\n"
            "3. 短标题版本\n"
            "华为涛完整版：20分钟搞懂中国半导体投资图谱\n\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n\n"
            "6. 优化标题格式 (版本A)\n"
            "内资闭门会 / 华为涛改写半导体规则 / 3片7nm堆叠打1片3nm / 宁德时代时刻 / 2026-07-07\n\n"
            "7. 优化标题格式 (版本B)\n"
            "内资闭门会 | 华为涛哲学 | 产业链话语权之战 | 超级通胀赛道 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 2588})
        _validate_strict_ds_plan(plan)

        self.assertIn("华为涛不是技术概念", plan["title"])
        self.assertNotIn("标题版本", plan["title"])
        self.assertEqual(plan["audio"]["start_seconds"], 156.52)
        self.assertEqual(plan["audio"]["end_seconds"], 2201.04)
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / 华为涛改写半导体规则 / 3片7nm堆叠打1片3nm / 宁德时代时刻 / 2026-07-07",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | 华为涛哲学 | 产业链话语权之战 | 超级通胀赛道 | 2026-07-07",
        )

    def test_direct_ds_response_prefers_deepseek_structured_plan(self):
        response = (
            "1. 标题版本一\n"
            "本地标题不应优先\n"
            "2. 标题版本二\n"
            "本地二号标题\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n"
            "6. 优化标题格式 (版本A)\n"
            "内资闭门会 / 本地A / 本地B / 本地C / 2026-07-07\n"
            "7. 优化标题格式 (版本B)\n"
            "内资闭门会 | 本地A | 本地B | 本地C | 2026-07-07"
        )
        planner_payload = {
            "title": "DS结构化标题",
            "title_options": ["DS结构化标题"],
            "subtitle": "DS副标题",
            "cover_main_title": "内资闭门会议",
            "cover_title": "DS主标题",
            "cover_subtitle": "DS副标题",
            "finance_cover_subtitle": "DS横屏封皮标题",
            "xiaojunjun_title": "内资闭门会议",
            "xiaojunjun_subtitle": "DS小君君标题",
            "xiaojunjun_video_title": "内资闭门会 / DS一 / DS二 / DS三 / 2026-07-07",
            "ciketc_video_title": "内资闭门会 | DS一 | DS二 | DS三 | 2026-07-07",
            "date_text": "2026.07.07",
            "emotion": "pro",
            "audio": {"start_seconds": 156.52, "end_seconds": 2201.04, "speed": 1.2},
            "provider": "deepseek",
        }

        with patch("services.mp3_video_workflow.deepseek_plan", return_value=planner_payload):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 2588})

        self.assertEqual(plan["title"], "DS结构化标题")
        self.assertEqual(plan["xiaojunjun_video_title"], "内资闭门会 / DS一 / DS二 / DS三 / 2026-07-07")
        self.assertEqual(plan["ciketc_video_title"], "内资闭门会 | DS一 | DS二 | DS三 | 2026-07-07")
        self.assertEqual(plan["planner_input_source"], "direct_ds_response")

    def test_strict_ds_plan_rejects_placeholder_visual_copy(self):
        plan = {
            "title": "标题版本一（信息密度高，点击率高）",
            "cover_title": "格式(版本A)",
            "cover_subtitle": "未识别到时间戳",
            "xiaojunjun_video_title": "内资闭门会 / xxxx / xxxx / xxxx / 当日日期",
            "ciketc_video_title": "内资闭门会 | xxxx | xxxx | xxxx | 当日日期",
            "audio": {"start_seconds": 0, "end_seconds": None},
        }

        with self.assertRaisesRegex(Mp3VideoWorkflowError, "规划不合格"):
            _validate_strict_ds_plan(plan)

    def test_db_retry_recovers_after_operational_error(self):
        attempts = {"count": 0}

        def operation():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
            return "ok"

        with patch("services.mp3_video_workflow.db.session.rollback") as rollback, \
                patch("services.mp3_video_workflow.db.session.remove") as remove, \
                patch("services.mp3_video_workflow.time.sleep") as sleep:
            self.assertEqual(_db_retry(operation, "test"), "ok")

        self.assertEqual(attempts["count"], 2)
        rollback.assert_called_once()
        remove.assert_called_once()
        sleep.assert_called_once()

    def test_generate_from_transcript_reuses_same_input_cache(self):
        transcript = "这里是一段足够长的 IMA 复制全文，讨论黄金、美元和美债的交易机会。" * 3
        workflow = {
            "transcript_text": transcript,
            "transcript_prompt": "请总结这段音频\n\n{{transcript}}",
        }
        audio = {
            "attachment_id": "asset-1",
            "filename": "demo.mp3",
            "path": "/tmp/demo.mp3",
            "duration_seconds": 90,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {
                "MP3_VIDEO_TRANSCRIPT_CACHE_DIR": tmpdir,
                "MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-test",
                "MP3_VIDEO_TRANSCRIPT_CACHE_DISABLED": "0",
            }, clear=False), \
                    patch("services.mp3_video_workflow.deepseek_transcript_response", return_value="DS 返回结果") as deepseek, \
                    patch("services.mp3_video_workflow._find_recent_transcript_deepseek_result", return_value=None), \
                    patch("services.mp3_video_workflow._emit"):
                first = _generate_from_transcript(
                    task_id="job-1",
                    workflow=workflow,
                    audio=audio,
                    output_path=os.path.join(tmpdir, "job-1.json"),
                )
                second = _generate_from_transcript(
                    task_id="job-2",
                    workflow=workflow,
                    audio=audio,
                    output_path=os.path.join(tmpdir, "job-2.json"),
                )
                self.assertTrue(os.path.exists(first["cache"]["path"]))

        deepseek.assert_called_once()
        self.assertEqual(first["cache"]["status"], "miss")
        self.assertEqual(second["cache"]["status"], "hit")
        self.assertEqual(second["response_text"], "DS 返回结果")

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

    def test_audio_range_extracts_bracketed_separate_timestamps(self):
        response = """
        4. 最精华音频片段
        开始时间戳： [00:02:36.520]
        结束时间戳： [00:36:41.040]
        该片段信息密度最高。
        """

        self.assertEqual(extract_audio_range(response), (156.52, 2201.04))

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

    def test_title_extraction_ignores_prompt_placeholders(self):
        response = (
            "好的，收到您的指令。以下是基于您提供的 IMA 转录全文和处理要求，输出结果如下：\n"
            "7：尝试优化下标题，并给我另外一个版本，使用 内资闭门会 | xxxx | xxxx | xxxx | 当日日期 ，这样的格式来写\n"
            "**7. 标题优化** 内资闭门会 | “小微” 如何复制小程序奇迹 | 解码To C Agent | 2026-07-06\n"
            "中金闭门会揭示最大信息差：市场低估C端Agent独占价值，高估B端商业化速度。"
        )

        candidates = extract_title_candidates(response)
        joined = "\n".join(candidates)

        self.assertIn("小微", candidates[0])
        self.assertIn("解码To C Agent", candidates[0])
        self.assertNotIn("xxxx", joined)
        self.assertNotIn("当日日期", joined)
        self.assertNotIn("收到您的指令", joined)

    def test_fallback_plan_maps_ds_six_and_seven_to_video_titles(self):
        slash_title = "内资闭门会 / 化工供给侧东升西落 / 油价70美元补库倒计时 / 蛋氨酸-PVA-制冷剂三大主线 / 2026-07-06"
        pipe_title = "内资闭门会 | 全球化工产能重塑 | 补库提前量价齐升 | 锁定MDI/化肥/氟化工机遇 | 2026-07-06"
        response = (
            "6. **闭门会格式标题（斜杠版）** "
            f"{slash_title} "
            "7. **闭门会格式标题（竖线版）** "
            f"{pipe_title}"
        )

        extracted = extract_closed_door_video_titles(response)
        plan = validate_plan({}, fallback=fallback_plan(response, audio={"duration_seconds": 1000}), audio={"duration_seconds": 1000})

        self.assertEqual(extracted["xiaojunjun_video_title"], slash_title)
        self.assertEqual(extracted["ciketc_video_title"], pipe_title)
        self.assertEqual(plan["xiaojunjun_video_title"], slash_title)
        self.assertEqual(plan["ciketc_video_title"], pipe_title)
        self.assertEqual(_publisher_video_title(plan, role="xiaojunjun_landscape"), slash_title)
        self.assertEqual(_publisher_video_title(plan, role="ciketc_vertical"), pipe_title)

    def test_fallback_plan_uses_high_density_titles_for_ciketc_covers(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“多数人还在用GDP和信用周期分析美元美债时，我们已全部抛掉——科技泡沫会崩吗？"
            "美债利率回不去了？美元指数破不了90？黄金今年不行但等这个信号就是暴利机会” "
            "**2. 高信息密度投资人标题（版本二）** "
            "“算力投资可能已经过剩？一个群体性误判的三条脆弱线索，以及美元、美债、黄金如何全部回归"
            "‘国家信用’这把最原始的秤——2026下半年最大的信息差与赚钱窗口” "
            "**3. 精简短标题** “科技不崩，美元不跌；美债回不去了；黄金等最后一跌” "
            "**4. 最精华音频片段（约25分钟）** - **开始时间戳**：00:11 - **结束时间戳**：25:27"
        )

        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 2000})
        plan = validate_plan({}, fallback=fallback, audio={"filename": "demo.mp3", "duration_seconds": 2000})

        self.assertIn("科技泡沫会崩吗", plan["title_options"][0])
        self.assertIn("算力投资可能已经过剩", plan["title_options"][1])
        self.assertEqual(
            plan["finance_cover_subtitle"],
            "抛掉GDP和信用周期\n科技泡沫会崩吗，美债回不去\n美元难破90",
        )
        self.assertEqual(plan["cover_title"], "美债美元\n黄金窗口")
        self.assertEqual(plan["cover_subtitle"], "回归国家信用定价锚")
        self.assertEqual(plan["audio"]["start_seconds"], 11)
        self.assertEqual(plan["audio"]["end_seconds"], 1527)

    def test_validate_plan_preserves_deepseek_vertical_cover_decision(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“多数人还在用GDP和信用周期分析美元美债时，我们已全部抛掉——科技泡沫会崩吗？"
            "美债利率回不去了？美元指数破不了90？黄金今年不行但等这个信号就是暴利机会” "
            "**2. 高信息密度投资人标题（版本二）** "
            "“算力投资可能已经过剩？一个群体性误判的三条脆弱线索，以及美元、美债、黄金如何全部回归"
            "‘国家信用’这把最原始的秤——2026下半年最大的信息差与赚钱窗口”"
        )
        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 2000})

        plan = validate_plan(
            {
                "title": fallback["title"],
                "title_options": fallback["title_options"],
                "cover_title": "美元美债\n黄金信号",
                "cover_subtitle": "国家信用重定价",
                "finance_cover_subtitle": "抛掉GDP旧框架\n美债美元再定价\n黄金等待信号",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 2000},
        )

        self.assertEqual(plan["cover_title"], "美元美债\n黄金信号")
        self.assertEqual(plan["cover_subtitle"], "国家信用重定价")
        self.assertEqual(
            plan["finance_cover_subtitle"],
            "抛掉GDP旧框架\n美债美元再定价\n黄金等待信号",
        )

    def test_validate_plan_separates_xiaojunjun_from_ciketc_copy(self):
        response = (
            "**7. 标题优化** 内资闭门会 | “小微” 如何复制小程序奇迹 | "
            "解码 To C Agent 的抽佣逻辑 | 2026-07-06\n"
            "中金闭门会揭示最大信息差：市场低估C端Agent独占价值，高估B端商业化速度。"
        )
        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 1000})

        plan = validate_plan(
            {
                "title": fallback["title"],
                "title_options": fallback["title_options"],
                "cover_title": "微信小微\n交易入口",
                "cover_subtitle": "C端Agent重估",
                "finance_cover_subtitle": "微信小微灰测\n复制小程序奇迹\n重估抽佣逻辑",
                "xiaojunjun_subtitle": "C端Agent重估",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )

        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["cover_subtitle"])
        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["finance_cover_subtitle"])
        self.assertIn("小微", plan["xiaojunjun_subtitle"])
        self.assertIn("To C Agent", plan["xiaojunjun_subtitle"])

    def test_vertical_cover_asset_focus_is_not_hardcoded_to_us_bonds_gold(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“港股和铜油资产如何重新回归供需定价？原油窗口、铜价弹性与港股估值修复，"
            "可能是下半年最大的交易机会”"
        )

        plan = validate_plan(
            {},
            fallback=fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 800}),
            audio={"filename": "demo.mp3", "duration_seconds": 800},
        )

        self.assertNotEqual(plan["cover_title"], "美债美元\n黄金窗口")
        self.assertIn("港股", plan["cover_title"])
        self.assertTrue(any(term in plan["cover_title"] for term in ("铜", "原油", "油价")))

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

    def test_finance_cover_copy_is_short_before_generic_renderer(self):
        value = (
            "市场恐慌暴跌为何高呼抓紧布局\n"
            "全球化工供给侧东升西落逻辑已定\n"
            "短期超跌砸出黄金坑补库品类拿好"
        )

        result = _finance_cover_highlight_text({"finance_cover_subtitle": value})

        self.assertEqual(
            result,
            "市场恐慌暴跌抓紧布局\n化工供给东升西落\n超跌黄金坑补库拿好",
        )
        self.assertTrue(all(len(line) <= 13 for line in result.split("\n")))

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

    def test_workflow_render_mode_defaults_to_full_and_accepts_preview_aliases(self):
        self.assertEqual(_workflow_render_mode({}), "full_video")
        self.assertEqual(_workflow_render_mode({"render_mode": "keyframes"}), "keyframes")
        self.assertEqual(_workflow_render_mode({"output_mode": "preview"}), "keyframes")
        self.assertEqual(_workflow_render_mode({"preview_only": True}), "keyframes")

    def test_workflow_generate_subtitles_defaults_to_true_and_accepts_false(self):
        self.assertTrue(_workflow_generate_subtitles({}))
        self.assertTrue(_workflow_generate_subtitles({"generate_subtitles": True}))
        self.assertFalse(_workflow_generate_subtitles({"generate_subtitles": False}))
        self.assertFalse(_workflow_generate_subtitles({"generate_subtitles": "off"}))
        self.assertFalse(_workflow_generate_subtitles({"metadata": {"enable_subtitles": "0"}}))

    def test_generate_video_keyframes_creates_reusable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            landscape = os.path.join(tmpdir, "landscape.jpg")
            vertical = os.path.join(tmpdir, "vertical.jpg")
            Image.new("RGB", (1280, 720), "#112233").save(landscape)
            Image.new("RGB", (1080, 1920), "#223344").save(vertical)
            artifacts = {
                "xiaojunjun_background": {"path": landscape},
                "audio_background": {"path": vertical},
            }

            with patch("services.mp3_video_workflow.storage_dir_for_job", return_value=Path(tmpdir)):
                keyframes = generate_video_keyframes(
                    "job-1",
                    plan=fallback_plan("标题方案：黄金调整以后怎么看。", audio={"duration_seconds": 180}),
                    artifacts=artifacts,
                    audio={"filename": "demo.mp3", "duration_seconds": 180},
                    source_text="黄金调整以后怎么看。央行购金和避险需求仍是长期主线。",
                )

            self.assertEqual(len(keyframes), 6)
            self.assertTrue(_keyframe_artifacts_ready(keyframes))
            for artifact in keyframes.values():
                self.assertTrue(os.path.exists(artifact["path"]))
                self.assertGreater(os.path.getsize(artifact["path"]), 10_000)

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

    def test_mp3_transcript_prompt_defaults_and_override(self):
        self.assertEqual(initial_transcript_prompt({}), DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT)
        self.assertEqual(initial_transcript_prompt({"transcript_prompt": "按我的模板处理"}), "按我的模板处理")

    def test_build_transcript_user_prompt_replaces_placeholder(self):
        prompt = "请处理 {audio_filename}\n\n{{transcript}}"
        rendered = _build_transcript_user_prompt(
            "这里是 IMA 复制全文，包含完整会议讨论。",
            prompt=prompt,
            audio={"filename": "demo.mp3"},
        )

        self.assertIn("demo.mp3", rendered)
        self.assertIn("完整会议讨论", rendered)
        self.assertNotIn("{{transcript}}", rendered)

    def test_deepseek_transcript_response_posts_prompt_and_transcript(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "DS 返回结果"}}]}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["json"] = json or {}
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict(os.environ, {"MP3_VIDEO_TRANSCRIPT_API_KEY": "test-key", "MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-test"}, clear=False):
            with patch("services.mp3_video_workflow.requests.post", side_effect=fake_post):
                result = deepseek_transcript_response("转录全文内容足够长", prompt="请总结", audio={"filename": "demo.mp3"})

        self.assertEqual(result, "DS 返回结果")
        self.assertTrue(captured["url"].endswith("/v1/chat/completions"))
        self.assertEqual(captured["json"]["model"], "deepseek-test")
        user_message = captured["json"]["messages"][1]["content"]
        self.assertIn("请总结", user_message)
        self.assertIn("转录全文内容足够长", user_message)

    def test_draw_transcript_infographic_creates_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "transcript.png")
            draw_transcript_infographic(
                output,
                "投资者视角长标题：内资闭门会 | 黄金短期调整但长期逻辑仍在 | 260705\n推荐截取时间戳：00:10 - 01:20\n核心观点：央行购金和避险需求仍是主线。",
                audio={"filename": "demo.mp3"},
            )

            self.assertTrue(os.path.exists(output))
            self.assertGreater(os.path.getsize(output), 10_000)

    def test_transcript_infographic_filters_process_markers(self):
        bullets = _infographic_bullets(
            "好的，收到您的指令。以下是基于您提供的 IMA 转录全文和处理要求，输出结果如下：\n"
            "使用 内资闭门会 | xxxx | xxxx | xxxx | 当日日期 ，这样的格式来写\n"
            "1. 核心观点：科技不崩之前，美元指数一直偏强。\n"
            "4 最精华音频片段（约25分钟）\n"
            "推荐截取时间戳：00:11 - 25:27\n"
            "Generated from transcript, no Gemini upload\n"
            "黄金的基本面就是央行，决定了黄金跌不深。"
        )

        joined = "\n".join(bullets)
        self.assertIn("科技不崩", joined)
        self.assertIn("黄金的基本面", joined)
        self.assertNotIn("最精华音频片段", joined)
        self.assertNotIn("推荐截取", joined)
        self.assertNotIn("xxxx", joined)
        self.assertNotIn("收到您的指令", joined)

    def test_transcript_model_uses_specific_override(self):
        with patch.dict(os.environ, {"MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-transcript", "MP3_VIDEO_PLANNER_MODEL": "deepseek-planner"}, clear=False):
            self.assertEqual(transcript_model(), "deepseek-transcript")

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

    def test_publisher_result_summary_uses_projected_video_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            task = PublicationTask(
                id="child-landscape",
                status="completed",
                current_step="done",
                progress=100,
                params={"aspect_ratio": "16:9"},
                result={},
            )
            task.task_video_path = str(video_path)

            summary = _publisher_result_summary(task)

            self.assertEqual(summary["video_path"], str(video_path))
            self.assertEqual(summary["landscape_video_path"], str(video_path))
            self.assertTrue(_video_summary_ready(summary))

    def test_publisher_result_summary_marks_subtitle_toggle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            task = PublicationTask(
                id="child-no-subtitles",
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "aspect_ratio": "9:16",
                    "generate_subtitles": False,
                    "video": {"sub_burn_enabled": False, "asr_enabled": False},
                },
                result={"video_path": str(video_path), "generate_subtitles": False},
            )

            summary = _publisher_result_summary(task)

            self.assertFalse(_publisher_child_generate_subtitles(task))
            self.assertFalse(summary["generate_subtitles"])
            self.assertEqual(summary["vertical_video_path"], str(video_path))

    def test_reusable_video_summary_falls_back_to_existing_publisher_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            child = PublicationTask(
                id="child-vertical",
                status="completed",
                current_step="done",
                progress=100,
                params={"aspect_ratio": "9:16"},
                result={"video_path": str(video_path)},
            )
            with patch(
                "services.mp3_video_workflow._find_existing_publisher_video_task",
                return_value=child,
            ) as find_existing:
                summary, task = _reusable_video_summary({}, "vertical_video", parent_id="parent-1")

            self.assertIs(task, child)
            self.assertEqual(summary["vertical_video_path"], str(video_path))
            find_existing.assert_called_once_with(
                "parent-1",
                role="ciketc_vertical",
                aspect_ratio="9:16",
            )

    def test_completed_result_drops_waiting_state(self):
        result = {
            "plan": {"title": "demo"},
            "waiting_child": {"label": "vertical_video", "child_id": "child-1"},
            "deferred_waits": {"vertical_video": {"defer_count": 2}},
        }

        cleaned = _without_waiting_state(result)

        self.assertEqual(cleaned, {"plan": {"title": "demo"}})
        self.assertIn("waiting_child", result)

    def test_publisher_video_children_use_short_inline_wait_and_deferred_resume(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS": "",
            "MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS": "",
            "MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS": "",
            "MP3_VIDEO_CHILD_TIMEOUT_SECONDS": "",
            "MP3_VIDEO_PARENT_RETRY_DELAY_SECONDS": "",
            "MP3_VIDEO_PARENT_MAX_DEFERS": "",
        }, clear=False):
            self.assertEqual(_child_inline_wait_seconds("landscape_video"), 5)
            self.assertEqual(_child_inline_wait_seconds("vertical_video"), 5)
            self.assertEqual(_parent_retry_delay_seconds("landscape_video"), 60)
            self.assertEqual(_parent_max_defers("vertical_video"), 180)
            self.assertEqual(_child_inline_wait_seconds("gemini"), 2400)
            self.assertEqual(_parent_retry_delay_seconds("gemini"), 300)
            self.assertEqual(_parent_max_defers("gemini"), 144)

    def test_publisher_video_wait_settings_can_be_overridden(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS": "12",
            "MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS": "45",
            "MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS": "9",
        }, clear=False):
            self.assertEqual(_child_inline_wait_seconds("landscape_video"), 12)
            self.assertEqual(_parent_retry_delay_seconds("vertical_video"), 45)
            self.assertEqual(_parent_max_defers("landscape_video"), 9)


if __name__ == "__main__":
    unittest.main()
