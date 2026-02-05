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

__all__ = [
    'db',
    'User',
    'Channel',
    'ChannelData',
    'Video',
    'VideoData',
    'FavoriteVideo',
    'OpportunitySignal',
    'UserOpportunityInteraction',
    'CollectionKeyword',
    'CollectionJob',
    'PublicationTask'
]
