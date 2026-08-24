import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.bilibili.uploader import (  # noqa: E402
    BilibiliChunkRetryError,
    BilibiliUploadTimeoutError,
    BoundedVideoUploader,
    BiliUploader,
    DEFAULT_CHARGING_PREVIEW_END,
    DEFAULT_NEUTRAL_MARK,
)


class BiliUploaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_sdk_uploader_stops_infinite_chunk_retries(self):
        uploader = object.__new__(BoundedVideoUploader)
        uploader._max_chunk_attempts = 2
        uploader._chunk_attempts = {}
        page = Mock(path="/tmp/video.mp4")
        failed = {
            "ok": False,
            "chunk_number": 0,
            "offset": 0,
            "page": page,
        }

        with patch(
            "services.bilibili.uploader.VideoUploader._upload_chunk",
            new=AsyncMock(return_value=failed),
        ):
            first = await uploader._upload_chunk(page, 0, 0, 3, {})
            self.assertFalse(first["ok"])
            with self.assertRaises(BilibiliChunkRetryError):
                await uploader._upload_chunk(page, 0, 0, 3, {})

    async def test_upload_single_stops_when_overall_timeout_expires(self):
        async def expire(awaitable, *, timeout):
            awaitable.close()
            self.assertEqual(timeout, 7)
            raise TimeoutError

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
                sdk_uploader = sdk_uploader_cls.return_value
                sdk_uploader.start = Mock(return_value=AsyncMock()())
                sdk_uploader.on.return_value = lambda handler: handler
                with patch(
                    "services.bilibili.uploader.asyncio.wait_for",
                    side_effect=expire,
                ), patch.dict(
                    "os.environ",
                    {"BILIBILI_UPLOAD_TIMEOUT_SECONDS": "7"},
                ):
                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    with self.assertRaises(BilibiliUploadTimeoutError):
                        await uploader.upload_single(
                            video_path=video.name,
                            title="超时测试",
                            cover_path="/tmp/cover.jpg",
                        )

    def test_creator_seasons_reads_empty_creator_collection_and_paginates(self):
        first_response = Mock()
        first_response.raise_for_status.return_value = None
        first_response.json.return_value = {
            "code": 0,
            "data": {
                "seasons": [{"season": {"id": 8644096, "title": "test", "ep_num": 0}}],
                "total": 2,
            },
        }
        second_response = Mock()
        second_response.raise_for_status.return_value = None
        second_response.json.return_value = {
            "code": 0,
            "data": {
                "seasons": [{"season": {"id": 8644097, "title": "第二个", "ep_num": 3}}],
                "total": 2,
            },
        }

        uploader = BiliUploader(
            sessdata="sess",
            bili_jct="csrf",
            buvid3="device",
            dedeuserid="123",
        )
        with patch(
            "services.bilibili.uploader.requests.get",
            side_effect=[first_response, second_response],
        ) as request_get:
            items = uploader.creator_seasons()

        self.assertEqual([item["season"]["title"] for item in items], ["test", "第二个"])
        self.assertEqual(
            [item.kwargs["params"]["pn"] for item in request_get.call_args_list],
            [1, 2],
        )
        self.assertEqual(request_get.call_args_list[0].kwargs["params"]["filter"], 1)

    async def test_upload_single_maps_neutral_mark_to_creation_statement(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
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
        self.assertFalse(meta_cls.called)
        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["creation_statement"], {"id": 4})
        self.assertNotIn("neutral_mark", meta)
        self.assertEqual(meta["no_reprint"], 1)

    async def test_upload_single_passes_delay_time_to_meta(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
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

        self.assertFalse(meta_cls.called)
        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["dtime"], 1782000000)
        self.assertEqual(meta["creation_statement"], {"id": 4})

    async def test_upload_single_builds_season_meta_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_resolve_season_section_id", return_value=67890):
                with patch.object(
                    BiliUploader,
                    "_add_uploaded_video_to_season",
                    return_value={"season_id": 12345, "section_id": 67890, "cid": 99},
                ) as add_to_season:
                    with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                        with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
                            sdk_uploader = sdk_uploader_cls.return_value
                            sdk_uploader.start = AsyncMock(return_value={"aid": 5, "bvid": "BVseason"})
                            sdk_uploader.on.return_value = lambda handler: handler

                            uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                            result = await uploader.upload_single(
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
        self.assertEqual(meta["creation_statement"], {"id": 4})
        self.assertEqual(result["season_id"], 12345)
        self.assertEqual(result["section_id"], 67890)
        add_to_season.assert_called_once_with(
            season_id=12345,
            section_id=67890,
            title="合集测试",
            aid=5,
            bvid="BVseason",
            charging_pay=False,
        )

    def test_resolve_season_section_id_uses_last_creator_section(self):
        uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
        with patch.object(
            uploader,
            "creator_seasons",
            return_value=[{
                "season": {"id": 8644096, "title": "test"},
                "sections": {"sections": [{"id": 9630301}, {"id": 9630302}]},
            }],
        ):
            section_id = uploader._resolve_season_section_id(8644096)

        self.assertEqual(section_id, 9630302)

    async def test_season_association_failure_does_not_repeat_media_upload(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_resolve_season_section_id", return_value=67890):
                with patch.object(
                    BiliUploader,
                    "_add_uploaded_video_to_season",
                    side_effect=RuntimeError("加入合集失败"),
                ):
                    with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
                        sdk_uploader = sdk_uploader_cls.return_value
                        sdk_uploader.start = AsyncMock(return_value={"aid": 5, "bvid": "BVseason"})
                        sdk_uploader.on.return_value = lambda handler: handler
                        uploader = BiliUploader(sessdata="sess", bili_jct="csrf")

                        with self.assertRaisesRegex(RuntimeError, "加入合集失败"):
                            await uploader.upload_single(
                                video_path=video.name,
                                title="合集测试",
                                cover_path="/tmp/cover.jpg",
                                season_id=12345,
                            )

        sdk_uploader.start.assert_awaited_once()

    def test_add_uploaded_video_to_season_matches_creator_request_contract(self):
        archive_response = Mock()
        archive_response.raise_for_status.return_value = None
        archive_response.json.return_value = {
            "code": 0,
            "data": {"videos": [{"cid": 778899}]},
        }
        season_response = Mock()
        season_response.raise_for_status.return_value = None
        season_response.json.return_value = {"code": 0, "data": {"ok": 1}}
        uploader = BiliUploader(
            sessdata="sess",
            bili_jct="csrf",
            buvid3="device",
            dedeuserid="123",
        )

        with patch("services.bilibili.uploader.requests.get", return_value=archive_response):
            with patch("services.bilibili.uploader.requests.post", return_value=season_response) as post:
                result = uploader._add_uploaded_video_to_season(
                    season_id=8644096,
                    section_id=9630302,
                    title="合集测试",
                    aid=116966891193752,
                    bvid="BV1hzgm67EWV",
                )

        self.assertEqual(result["cid"], 778899)
        self.assertEqual(
            post.call_args.args[0],
            "https://member.bilibili.com/x2/creative/web/season/section/episodes/add",
        )
        self.assertEqual(post.call_args.kwargs["params"], {"csrf": "csrf"})
        self.assertEqual(post.call_args.kwargs["json"], {
            "sectionId": 9630302,
            "episodes": [{
                "title": "合集测试",
                "cid": 778899,
                "aid": 116966891193752,
                "charging_pay": 0,
            }],
        })

    async def test_upload_single_builds_only_self_meta_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
                    sdk_uploader = sdk_uploader_cls.return_value
                    sdk_uploader.start = AsyncMock(return_value={"aid": 6, "bvid": "BVprivate"})
                    sdk_uploader.on.return_value = lambda handler: handler

                    uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
                    await uploader.upload_single(
                        video_path=video.name,
                        title="仅自己可见测试",
                        desc="测试简介",
                        tid=36,
                        tags=["测试"],
                        cover_path="/tmp/cover.jpg",
                        is_only_self=1,
                        space_hidden=2,
                    )

        self.assertFalse(meta_cls.called)
        meta = sdk_uploader_cls.call_args.kwargs["meta"]
        self.assertEqual(meta["is_only_self"], 1)
        self.assertEqual(meta["space_hidden"], 2)
        self.assertEqual(meta["creation_statement"], {"id": 4})
        self.assertNotIn("charging_pay", meta)

    async def test_upload_single_builds_charging_pay_meta_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_fetch_default_upower_level_id", return_value="level-1"):
                with patch("services.bilibili.uploader.VideoMeta") as meta_cls:
                    with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
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
        self.assertEqual(meta["creation_statement"], {"id": 4})
        self.assertNotIn("neutral_mark", meta)
        self.assertEqual(meta["tid"], 36)

    async def test_upload_single_charging_pay_defaults_preview_end(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video:
            with patch.object(BiliUploader, "_fetch_default_upower_level_id", return_value="level-1"):
                with patch("services.bilibili.uploader.BoundedVideoUploader") as sdk_uploader_cls:
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
