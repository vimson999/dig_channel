# Flask应用主文件
"""
YouTube频道数据追踪工具 - Flask应用主文件

这是Flask应用的入口点，负责：
- 创建和配置Flask应用
- 注册蓝图和中间件
- 初始化数据库和其他服务
"""

import os
import sys
from dotenv import load_dotenv

# Ensure the backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from flask import Flask
from flask_cors import CORS
from config import Config
from middleware.error_handler import error_handler
from middleware.request_logger import RequestLoggerMiddleware
from middleware.database_logger import DatabaseLoggerMiddleware
from middleware.performance_monitor import PerformanceMonitor
from utils.colored_logger import log_startup_banner, setup_colored_logger
from utils.log_manager import setup_application_logging, schedule_log_maintenance

# 初始化扩展
cors = CORS()
request_logger = RequestLoggerMiddleware()
database_logger = DatabaseLoggerMiddleware()
performance_monitor = PerformanceMonitor()

def create_app(config_class=Config):
    """应用工厂函数
    
    创建和配置Flask应用实例
    
    Args:
        config_class: 配置类，默认为Config
        
    Returns:
        Flask: 配置好的Flask应用实例
    """
    import os
    frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')
    root_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    instance_path = os.path.join(root_path, 'instance')
    
    app = Flask(__name__, 
                static_folder=frontend_path, 
                static_url_path='',
                instance_path=instance_path)
    app.config.from_object(config_class)
    
    # 初始化数据库
    from models import db
    db.init_app(app)
    
    # 初始化迁移工具
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
    
    # 初始化CORS
    cors.init_app(app, 
                 origins=["*"], 
                 allow_headers=["Content-Type", "Authorization"], 
                 methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                 supports_credentials=True)
    
    # 初始化日志中间件
    request_logger.init_app(app)
    database_logger.init_app(app)
    performance_monitor.init_app(app)
    
    # 注册API蓝图
    from apis import register_all_apis
    register_all_apis(app)
    
    # 初始化错误处理器
    error_handler.init_app(app)
    
    # 设置应用程序日志
    setup_application_logging(app.config)
    schedule_log_maintenance()
    
    # 基础路由
    @app.route('/')
    def index():
        return {'message': 'YouTube频道数据追踪工具 API v1.0', 'status': 'running'}
    
    @app.route('/health')
    def health():
        return {'status': 'healthy', 'version': '1.0.0'}
    
    # 静态页面路由
    @app.route('/tasks.html')
    def tasks_page():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, 'tasks.html')
    
    @app.route('/tasks')
    def tasks_redirect():
        from flask import redirect
        return redirect('/tasks.html')
    
    # 处理所有OPTIONS请求
    @app.before_request
    def handle_options():
        from flask import request
        if request.method == 'OPTIONS':
            from flask import make_response
            response = make_response()
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            response.headers['Access-Control-Max-Age'] = '86400'
            return response
    
    return app

if __name__ == '__main__':
    # 显示启动横幅
    log_startup_banner()
    
    # 设置应用日志
    logger = setup_colored_logger("FLASK_APP")
    logger.info("创建Flask应用...")
    
    app = create_app()
    
    logger.info("启动Flask开发服务器...")
    logger.info("访问地址: http://localhost:5001")
    logger.info("按 Ctrl+C 停止服务器")
    
    app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=True, reloader_type='stat')
