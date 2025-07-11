#!/usr/bin/env python3
"""
简化的测试服务器，仅用于测试视频同步功能
"""

from flask import Flask
from flask_cors import CORS
import sys
import os

# 添加项目路径到sys.path
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

# 配置数据库
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
db_path = os.path.join(project_root, 'instance', 'youtube_tracker.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev-secret-key'

# 初始化数据库
from models import db
db.init_app(app)

# 注册蓝图
from api.videos import videos_bp
from apis.v1.auth import bp as auth_bp
from apis.v1.channels import bp as channels_bp
from apis.v1.rankings import bp as rankings_bp
from apis.v1.data import bp as data_bp

app.register_blueprint(videos_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(channels_bp)
app.register_blueprint(rankings_bp)
app.register_blueprint(data_bp)

@app.route('/')
def index():
    return {'message': 'YouTube Analytics API 测试服务器', 'status': 'running'}

@app.route('/health')
def health():
    return {'status': 'healthy', 'database': 'connected'}

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    print("🚀 启动测试服务器...")
    print("📡 访问地址: http://localhost:5001")
    print("🔍 测试视频同步: POST http://localhost:5001/api/v1/videos/sync")
    
    app.run(host='0.0.0.0', port=5001, debug=True)