import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.bilibili.uploader import (  # noqa: E402
    BiliUploader,
    DEFAULT_CHARGING_PREVIEW_END,
    DEFAULT_NEUTRAL_MARK,
)


class BiliUploaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_single_passes_neutral_mark_to_sdk_meta(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.VideoUploader") as sdk_uploader_cls:
                    sdk_uploader = sdk_uploader_cls.return_value
                    sdk_uploader.start = AsyncMock(return_value={"aid": 1, "bvid": "BVtest"})
                    sdk_uploader.on.return_value = lambda handler: handler

                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    result = await uploader.upload_single(
                        video_path=video.name,
                        title="测试标题",
                        desc="测试简介",
                        tid=21,
                        tags=["测试"],
                        cover_path="/tmp/cover.jpg",
                        no_reprint=1,
                    )

        self.assertEqual(result["aid"], 1)
        meta_kwargs = meta_cls.call_args.kwargs
        self.assertEqual(meta_kwargs["neutral_mark"], DEFAULT_NEUTRAL_MARK)
        self.assertTrue(meta_kwargs["no_reprint"])

    async def test_upload_single_passes_delay_time_to_sdk_meta(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.VideoUploader") as sdk_uploader_cls:
                    sdk_uploader = sdk_uploader_cls.return_value
                    sdk_uploader.start = AsyncMock(return_value={"aid": 4, "bvid": "BVdelay"})
                    sdk_uploader.on.return_value = lambda handler: handler

                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    await uploader.upload_single(
                        video_path=video.name,
                        title="定时测试",
                        desc="测试简介",
                        tid=36,
                        tags=["测试"],
                        cover_path="/tmp/cover.jpg",
                        delay_time=1782000000,
                    )

        self.assertEqual(meta_cls.call_args.kwargs["delay_time"], 1782000000)

    async def test_upload_single_builds_season_meta_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.VideoUploader") as sdk_uploader_cls:
                    sdk_uploader = sdk_uploader_cls.return_value
                    sdk_uploader.start = AsyncMock(return_value={"aid": 5, "bvid": "BVseason"})
                    sdk_uploader.on.return_value = lambda handler: handler

                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    await uploader.upload_single(
                        video_path=video.name,
                        title="合集测试",
                        desc="测试简介",
                        tid=36,
                        tags=["测试"],
                        cover_path="/tmp/cover.jpg",
                        season_id=12345,
                        delay_time=1782000000,
                    )

        self.assertFalse(meta_cls.called)
        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["season_id"], 12345)
        self.assertEqual(meta["dtime"], 1782000000)
        self.assertEqual(meta["no_disturbance"], 0)

    async def test_upload_single_builds_charging_pay_meta_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_fetch_default_upower_level_id", return_value="level-1"):
                with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                    with patch("services.bilibili.uploader.VideoUploader") as sdk_uploader_cls:
                        sdk_uploader = sdk_uploader_cls.return_value
                        sdk_uploader.start = AsyncMock(return_value={"aid": 2, "bvid": "BVcharge"})
                        sdk_uploader.on.return_value = lambda handler: handler

                        uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                        result = await uploader.upload_single(
                            video_path=video.name,
                            title="充电测试",
                            desc="测试简介",
                            tid=36,
                            tags=["测试"],
                            cover_path="/tmp/cover.jpg",
                            no_reprint=1,
                            charging_pay={
                                "enabled": True,
                                "upower_mode": 0,
                                "preview": {"need_preview": 1, "start_time": 0, "end_time": 5},
                                "qa": {"qa_enabled": 0, "qa_id": None},
                                "member_first": {"enabled": 0, "exp_time": 0},
                            },
                        )

        self.assertEqual(result["aid"], 2)
        self.assertFalse(meta_cls.called)
        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["charging_pay"], 1)
        self.assertEqual(meta["upower_level_id"], "level-1")
        self.assertEqual(meta["upower_mode"], 0)
        self.assertEqual(meta["upower_unit_price"], 0)
        self.assertEqual(meta["preview"], {"need_preview": 1, "start_time": 0, "end_time": 5})
        self.assertEqual(meta["qa"], {"qa_enabled": 0, "qa_id": None})
        self.assertEqual(meta["member_first"], {"enabled": 0, "exp_time": 0})
        self.assertEqual(meta["neutral_mark"], DEFAULT_NEUTRAL_MARK)
        self.assertEqual(meta["tid"], 36)

    async def test_upload_single_charging_pay_defaults_preview_end(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_fetch_default_upower_level_id", return_value="level-1"):
                with patch("services.bilibili.uploader.VideoUploader") as sdk_uploader_cls:
                    sdk_uploader = sdk_uploader_cls.return_value
                    sdk_uploader.start = AsyncMock(return_value={"aid": 3, "bvid": "BVdefault"})
                    sdk_uploader.on.return_value = lambda handler: handler

                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    await uploader.upload_single(
                        video_path=video.name,
                        title="充电测试",
                        desc="测试简介",
                        tid=36,
                        tags=["测试"],
                        cover_path="/tmp/cover.jpg",
                        charging_pay={"enabled": True},
                    )

        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["preview"]["end_time"], DEFAULT_CHARGING_PREVIEW_END)


if __name__ == "__main__":
    unittest.main()
