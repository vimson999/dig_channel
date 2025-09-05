#!/usr/bin/env python3
"""
TTS测试服务器
独立运行TTS API进行测试
"""

import sys
import os
sys.path.insert(0, 'src/backend')

from flask import Flask
from flask_cors import CORS

# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'test-secret-key'

# 启用CORS
CORS(app, origins="*", allow_headers=["Content-Type", "Authorization"], 
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], supports_credentials=True)

# 注册TTS蓝图
try:
    from api.tts import tts_bp
    app.register_blueprint(tts_bp)
    print("✅ TTS蓝图注册成功")
except Exception as e:
    print(f"❌ TTS蓝图注册失败: {e}")
    sys.exit(1)

# 基础路由
@app.route('/')
def index():
    return {'message': 'TTS测试服务器运行中', 'status': 'running'}

@app.route('/health')
def health():
    return {'status': 'healthy', 'service': 'tts-test'}

if __name__ == '__main__':
    print("🎤 启动TTS测试服务器...")
    print("📝 访问 http://localhost:5002/api/v1/tts/models 查看可用模型")
    print("📝 访问 http://localhost:8888/tts.html 使用TTS界面")
    app.run(host='0.0.0.0', port=5002, debug=True)