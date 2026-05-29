# 数据模型包初始化文件
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .user import User
from .channel import Channel
from .channel_data import ChannelData
from .video import Video, VideoData
from .favorite import FavoriteVideo
from .indie_navigator import OpportunitySignal, UserOpportunityInteraction, CollectionKeyword, CollectionJob
from .task import PublicationTask
from .download_task import DownloadTask
from .system_config import SystemConfig
from .tts import TTSArticle, TTSSegment
from .zsxq_sync import ZsxqSyncRun, ZsxqTopic, ZsxqAttachment, ZsxqSyncCursor

__all__ = [
    'db', 
    'User', 
    'Channel', 
    'ChannelData', 
    'Video', 
    'VideoData', 
    'FavoriteVideo', 
    'OpportunitySignal',
    'PublicationTask',
    'DownloadTask',
    'SystemConfig',
    'TTSArticle',
    'TTSSegment',
    'ZsxqSyncRun',
    'ZsxqTopic',
    'ZsxqAttachment',
    'ZsxqSyncCursor'
]
