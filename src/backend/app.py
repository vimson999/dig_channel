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
import hmac
import time
from dotenv import load_dotenv

# Ensure the backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from flask import Flask, abort, current_app, jsonify, make_response, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from extensions import limiter
from middleware.error_handler import error_handler
from middleware.request_logger import RequestLoggerMiddleware
from middleware.database_logger import DatabaseLoggerMiddleware
from middleware.performance_monitor import PerformanceMonitor
from utils.colored_logger import log_startup_banner, setup_colored_logger
from utils.log_manager import setup_application_logging, schedule_log_maintenance
from utils.security_config import validate_security_config
from utils.security_logging import log_security_event

# 初始化扩展
cors = CORS()
request_logger = RequestLoggerMiddleware()
database_logger = DatabaseLoggerMiddleware()
performance_monitor = PerformanceMonitor()


def _start_web_background_threads(app):
    if app.config.get("TESTING"):
        return False
    role = str(os.environ.get("DB_PROCESS_ROLE") or os.environ.get("APP_PROCESS_ROLE") or "").strip().lower()
    if role in {"worker", "workers", "celery", "scheduler", "schedulers", "watchdog"}:
        return False
    command = " ".join(str(item) for item in sys.argv).lower()
    return not any(marker in command for marker in ("celery", "scheduler", "watchdog", "pytest", "unittest"))

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
    validate_security_config(app.config)

    if str(app.config.get('AUTH_MODE') or '').lower() == 'production':
        proxy_hops = int(app.config.get('TRUST_PROXY_HOPS', 1))
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
        )
    
    # 初始化数据库
    from models import db
    db.init_app(app)

    command = f" {' '.join(sys.argv).lower()} "
    if (
        str(app.config.get('AUTH_MODE') or '').lower() == 'production'
        and not app.config.get('TESTING')
        and ' db ' not in command
    ):
        from sqlalchemy import inspect
        from utils.security_config import SecurityConfigurationError
        with app.app_context():
            columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
        if 'auth_version' not in columns:
            raise SecurityConfigurationError(
                'Production database is missing users.auth_version; apply migrations as the table owner'
            )
    
    # 初始化迁移工具
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
    
    # 初始化CORS
    cors.init_app(app,
                 origins=app.config.get('CORS_ORIGINS', []),
                 allow_headers=["Content-Type", "Authorization"], 
                 methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                 supports_credentials=True)

    limiter.init_app(app)
    
    # 初始化日志中间件
    request_logger.init_app(app)
    database_logger.init_app(app)
    start_background_threads = _start_web_background_threads(app)
    performance_monitor.init_app(app, start_system_monitor=start_background_threads)
    
    # 注册API蓝图
    from apis import register_all_apis
    register_all_apis(app)
    
    # 初始化错误处理器
    error_handler.init_app(app)

    @app.errorhandler(429)
    def handle_rate_limit(error):
        log_security_event('request.rate_limited', 'denied', reason='rate_limit_exceeded')
        response = getattr(error, 'get_response', lambda: None)()
        retry_after = response.headers.get('Retry-After') if response else None
        result = jsonify({'success': False, 'error': {'message': '请求过于频繁，请稍后重试'}})
        if retry_after:
            result.headers['Retry-After'] = retry_after
        return result, 429
    
    # 设置应用程序日志
    setup_application_logging(app.config)
    if start_background_threads:
        schedule_log_maintenance()

    # 本地后端启动时可自动恢复 Celery workers 和计划任务。
    from services.backend_process_bootstrap import start_backend_process_bootstrap
    start_backend_process_bootstrap(app)
    
    # 基础路由
    @app.route('/')
    def index():
        return {'message': 'YouTube频道数据追踪工具 API v1.0', 'status': 'running'}
    
    @app.route('/health')
    def health():
        return {'status': 'healthy', 'version': '1.0.0'}

    @app.route('/access/<portal_token>')
    def login_portal(portal_token):
        """Private, unindexed entry point for the production login page."""
        production = str(current_app.config.get('AUTH_MODE') or '').lower() == 'production'
        expected = str(current_app.config.get('LOGIN_PORTAL_TOKEN') or '')
        if production and not hmac.compare_digest(str(portal_token), expected):
            abort(404)

        session['login_portal_verified_at'] = time.time()
        response = make_response(send_from_directory(app.static_folder, 'login.html'))
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response
    
    # 静态页面路由
    @app.route('/tasks.html')
    def tasks_page():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, 'tasks.html')
    
    @app.route('/tasks')
    def tasks_redirect():
        from flask import redirect
        return redirect('/tasks.html')
    
    # 默认拒绝：所有业务页面和 API 都必须是已登录管理员。
    @app.before_request
    def enforce_project_authentication():
        if request.method == 'OPTIONS':
            return None

        path = request.path
        production = str(current_app.config.get('AUTH_MODE') or '').lower() == 'production'
        public_paths = {
            '/health',
            '/api/v1/auth/login',
            '/api/v1/auth/register',
            '/api/v1/auth/session-check',
            '/js/config.js',
            '/js/api.js',
            '/js/utils.js',
            '/js/env.js',
            '/assets/images/favicon.ico',
        }
        if path in public_paths or path.startswith('/access/'):
            return None

        if not production and path in {'/login.html', '/login_enhanced.html'}:
            return None

        if production and path in {'/login.html', '/login_enhanced.html', '/register.html'}:
            abort(404)

        from flask import g
        from utils.auth_utils import get_current_user
        current_user = get_current_user()
        if not current_user:
            log_security_event('request.authorization', 'denied', reason='authentication_required')
            if production:
                abort(404)
            if path.startswith('/api/'):
                return jsonify({'success': False, 'error': {'message': '请先登录'}}), 401
            abort(401)

        if current_app.config.get('AUTH_REQUIRE_ADMIN', True) and not current_user.is_admin:
            log_security_event('request.authorization', 'denied', user=current_user, reason='admin_required')
            if production:
                abort(404)
            if path.startswith('/api/'):
                return jsonify({'success': False, 'error': {'message': '访问被拒绝'}}), 403
            abort(403)

        g.current_user = current_user
    
    return app

if __name__ == '__main__':
    # 显示启动横幅
    log_startup_banner()
    
    # 设置应用日志
    logger = setup_colored_logger("FLASK_APP")
    logger.info("创建Flask应用...")

    os.environ.setdefault("DB_PROCESS_ROLE", "web")
    os.environ.setdefault("WEB_DB_POOL_MODE", "queue")
    os.environ.setdefault("WEB_DB_POOL_SIZE", "4")
    os.environ.setdefault("WEB_DB_MAX_OVERFLOW", "0")
    os.environ.setdefault("WORKER_DB_POOL_MODE", "null")
    os.environ.setdefault("WORKER_DB_POOL_SIZE", "1")
    os.environ.setdefault("WORKER_DB_MAX_OVERFLOW", "0")
    os.environ.setdefault("SCHEDULER_DB_POOL_MODE", "null")
    os.environ.setdefault("SCHEDULER_DB_POOL_SIZE", "1")
    os.environ.setdefault("SCHEDULER_DB_MAX_OVERFLOW", "0")
    os.environ.setdefault("DB_POOL_MODE", "queue")
    os.environ.setdefault("DB_POOL_SIZE", "1")
    os.environ.setdefault("DB_MAX_OVERFLOW", "0")
    os.environ.setdefault("DB_CONNECT_TIMEOUT", "5")
    os.environ.setdefault("TASK_ADMISSION_GUARD_ENABLED", "1")
    os.environ.setdefault("TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS", "10")
    os.environ.setdefault("TASK_ADMISSION_DB_MAX_USED_RATIO", "0.85")
    os.environ.setdefault("TASK_ADMISSION_DB_MAX_IDLE_IN_TX", "5")
    os.environ.setdefault("TASK_ADMISSION_RETRY_DELAY_SECONDS", "180")
    os.environ.setdefault(
        "TASK_ADMISSION_FEATURE_BACKLOG_LIMITS",
        "publisher=12,report_audio_workflow=10,report_pdf_audio_workflow=10,report_top10_workflow=5,mp3_video_workflow=5,"
        "content_pdf_clean=10,content_audio_clean=5,zsxq_clean_output_publish=5",
    )
    os.environ.setdefault("ZSXQ_SYNC_DB_RETRIES", "5")
    os.environ.setdefault("ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS", "60")
    os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_ENABLED", "1")
    os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_MODE", "ensure")
    os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES", "all")
    os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS", "zsxq_auto")
    os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_WATCHDOG", "1")
    os.environ.setdefault(
        "CELERY_AUTO_HEAL_SERVICES",
        os.environ.get("BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES", "all"),
    )
    os.environ.setdefault("CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS", "1")
    
    app = create_app()
    
    logger.info("启动Flask开发服务器...")
    logger.info("访问地址: http://localhost:5001")
    logger.info("按 Ctrl+C 停止服务器")
    
    debug_enabled = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    use_reloader = os.environ.get("FLASK_USE_RELOADER", "0").lower() in {"1", "true", "yes", "on"}
    bind_host = os.environ.get('FLASK_BIND_HOST', '127.0.0.1')
    app.run(debug=debug_enabled, host=bind_host, port=5001, use_reloader=use_reloader, reloader_type='stat')
