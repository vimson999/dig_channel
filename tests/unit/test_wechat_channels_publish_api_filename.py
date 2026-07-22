import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1.wechat_channels_publish_test import _upload_filename_metadata  # noqa: E402


class WechatChannelsPublishFilenameTest(unittest.TestCase):
    def test_cjk_filename_keeps_extension(self):
        display_name, extension = _upload_filename_metadata("#视频号测试 .mp4")
        self.assertEqual("#视频号测试 .mp4", display_name)
        self.assertEqual(".mp4", extension)

    def test_client_path_is_reduced_to_basename(self):
        display_name, extension = _upload_filename_metadata("C:\\fakepath\\视频号.MOV")
        self.assertEqual("视频号.MOV", display_name)
        self.assertEqual(".mov", extension)


if __name__ == "__main__":
    unittest.main()
