import os
import json
import base64
import requests
import uuid
import logging
from datetime import datetime

logger = logging.getLogger("VOLC_TTS")

class VolcTTS:
    """火山引擎 TTS 工具类"""
    
    def __init__(self, appid=None, access_token=None, cluster=None):
        self.appid = appid or os.getenv('VOLC_APPID')
        self.access_token = access_token or os.getenv('VOLC_ACCESS_TOKEN')
        self.cluster = cluster or os.getenv('VOLC_CLUSTER', 'volcano_tts')
        self.host = "openspeech.bytedance.com"
        self.api_url = f"https://{self.host}/api/v1/tts"

    def synthesize(self, text, voice_type="bv001_streaming", speed_ratio=1.0, pitch_ratio=1.0):
        """
        同步合成语音 (火山非流式接口逻辑封装)
        返回: (success, result_data_or_error_msg)
        """
        if not self.appid or not self.access_token:
            return False, "火山配置(APPID/ACCESS_TOKEN)缺失"

        header = {
            "Authorization": f"Bearer;{self.access_token}"
        }

        request_json = {
            "app": {
                "appid": self.appid,
                "token": "access_token", # 火山固定占位符，实际 token 在 header
                "cluster": self.cluster
            },
            "user": {
                "uid": "system_user"
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": "mp3",
                "speed_ratio": speed_ratio,
                "pitch_ratio": pitch_ratio,
                "volume_ratio": 1.0
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query"
            }
        }

        try:
            resp = requests.post(self.api_url, json=request_json, headers=header, timeout=30)
            if resp.status_code != 200:
                return False, f"API请求失败: HTTP {resp.status_code}, {resp.text}"
            
            resp_data = resp.json()
            if resp_data.get("code") != 3000: # 3000 是火山成功的 code
                return False, f"TTS失败: {resp_data.get('message')} (Code: {resp_data.get('code')})"
            
            # 获取音频数据 (base64)
            audio_data_base64 = resp_data.get("data")
            if not audio_data_base64:
                return False, "API未返回音频数据"
            
            return True, base64.b64decode(audio_data_base64)

        except Exception as e:
            logger.error(f"火山TTS调用异常: {str(e)}")
            return False, f"调用异常: {str(e)}"

    def save_to_file(self, audio_content, save_path):
        """保存音频内容到文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'wb') as f:
                with open(save_path, 'wb') as f:
                    f.write(audio_content)
            return True
        except Exception as e:
            logger.error(f"保存音频文件失败: {str(e)}")
            return False
