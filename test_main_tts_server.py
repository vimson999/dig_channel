#!/usr/bin/env python3
"""
测试主TTS服务器的多人对话功能
"""

import sys
import os
sys.path.insert(0, 'src/backend')

from flask import Flask, request, jsonify
from flask_cors import CORS

# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'test-secret-key'
CORS(app, origins="*")

# 导入TTS模块并替换require_login装饰器
import sys
sys.path.append('src/backend')

# 模拟用户登录装饰器
def mock_require_login(f):
    """模拟登录装饰器用于测试"""
    def decorated_function(*args, **kwargs):
        class MockUser:
            username = "test_user"
        # 将current_user作为第一个参数传递
        return f(MockUser(), *args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# 在导入之前替换
import utils.auth_utils
utils.auth_utils.require_login = mock_require_login

# 导入TTS蓝图
from api.tts import tts_bp

# 注册蓝图
app.register_blueprint(tts_bp)

@app.route('/')
def index():
    return {
        'message': '主TTS服务器测试', 
        'status': 'running',
        'endpoints': [
            'GET /api/v1/tts/models',
            'GET /api/v1/tts/status',
            'POST /api/v1/tts/synthesize',
            'POST /api/v1/tts/dialogue',
            'GET /api/v1/tts/download/<filename>',
            'POST /api/v1/tts/cleanup'
        ]
    }

if __name__ == '__main__':
    print("🎤 启动主TTS服务器测试...")
    print("📝 访问 http://localhost:5004 查看服务状态")
    print("📝 使用 POST /api/v1/tts/dialogue 测试多人对话功能")
    app.run(host='0.0.0.0', port=5004, debug=True)