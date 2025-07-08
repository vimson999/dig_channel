# Flask应用主文件
"""
YouTube频道数据追踪工具 - Flask应用主文件

这是Flask应用的入口点，负责：
- 创建和配置Flask应用
- 注册蓝图和中间件
- 初始化数据库和其他服务
"""

from flask import Flask
from flask_cors import CORS
from config import Config
from middleware.error_handler import error_handler
from utils.colored_logger import log_startup_banner, setup_colored_logger

# 初始化扩展
cors = CORS()

def create_app(config_class=Config):
    """应用工厂函数
    
    创建和配置Flask应用实例
    
    Args:
        config_class: 配置类，默认为Config
        
    Returns:
        Flask: 配置好的Flask应用实例
    """
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 初始化数据库
    from models import db
    db.init_app(app)
    
    # 初始化CORS
    cors.init_app(app)
    
    # 注册API蓝图
    from apis import register_all_apis
    register_all_apis(app)
    
    # 初始化错误处理器
    error_handler.init_app(app)
    
    # 基础路由
    @app.route('/')
    def index():
        return {'message': 'YouTube频道数据追踪工具 API v1.0', 'status': 'running'}
    
    @app.route('/health')
    def health():
        return {'status': 'healthy', 'version': '1.0.0'}
    
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