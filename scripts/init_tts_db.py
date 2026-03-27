import sys
import os

# 将 src/backend 添加到路径
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
sys.path.insert(0, backend_dir)

from app import create_app
from models import db
from models.tts import TTSArticle, TTSSegment

app = create_app()
with app.app_context():
    db.create_all()
    print("✅ TTS 相关数据库表已成功创建或已存在")
