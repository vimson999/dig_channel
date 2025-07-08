# 数据模型包初始化文件
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .channel import Channel
from .channel_data import ChannelData
from .video import Video, VideoData

__all__ = ['db', 'Channel', 'ChannelData', 'Video', 'VideoData']