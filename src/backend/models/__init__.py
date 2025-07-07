# 数据模型包初始化文件
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .channel import Channel
from .channel_data import ChannelData

__all__ = ['db', 'Channel', 'ChannelData']