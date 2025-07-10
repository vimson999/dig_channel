"""
数据库操作日志记录中间件
记录所有数据库操作的详细信息，包括查询、执行时间、影响行数等
"""
import time
import logging
from flask import g
from sqlalchemy import event
from sqlalchemy.engine import Engine
from ..utils.colored_logger import get_logger, log_database_operation

logger = get_logger(__name__)

class DatabaseLoggerMiddleware:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化数据库日志记录"""
        # 监听SQLAlchemy事件
        event.listen(Engine, "before_cursor_execute", self._before_cursor_execute)
        event.listen(Engine, "after_cursor_execute", self._after_cursor_execute)
        
        # 配置SQLAlchemy日志
        self._configure_sqlalchemy_logging(app)
    
    def _configure_sqlalchemy_logging(self, app):
        """配置SQLAlchemy日志"""
        # 在开发环境下启用SQL日志
        if app.config.get('FLASK_ENV') == 'development':
            logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
    
    def _before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        """SQL执行前记录"""
        conn.info.setdefault('query_start_time', []).append(time.time())
        
        # 记录查询开始
        request_id = getattr(g, 'request_id', 'unknown')
        query_type = self._get_query_type(statement)
        
        logger.debug(f"🔍 [{request_id}] SQL开始: {query_type}")
        
        # 记录完整SQL（仅在调试模式下）
        if logger.isEnabledFor(logging.DEBUG):
            safe_sql = self._sanitize_sql(statement, parameters)
            logger.debug(f"📝 [{request_id}] SQL: {safe_sql}")
    
    def _after_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        """SQL执行后记录"""
        total_time = time.time() - conn.info['query_start_time'].pop(-1)
        duration_ms = round(total_time * 1000, 2)
        
        request_id = getattr(g, 'request_id', 'unknown')
        query_type = self._get_query_type(statement)
        
        # 获取影响的行数
        rowcount = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
        
        # 根据执行时间选择日志级别
        if duration_ms > 1000:  # 超过1秒
            level = 'warning'
            icon = '🐌'
        elif duration_ms > 500:  # 超过500ms
            level = 'info'
            icon = '⏱️'
        else:
            level = 'debug'
            icon = '⚡'
        
        # 记录执行结果
        log_msg = f"{icon} [{request_id}] SQL完成: {query_type} - {duration_ms}ms, 影响行数: {rowcount}"
        getattr(logger, level)(log_msg)
        
        # 使用现有的数据库日志记录函数
        success = duration_ms < 5000  # 5秒内认为成功
        details = f"耗时: {duration_ms}ms, 影响行数: {rowcount}"
        log_database_operation(query_type, self._get_table_name(statement), success, details)
        
        # 记录慢查询
        if duration_ms > 1000:
            self._log_slow_query(statement, parameters, duration_ms, request_id)
    
    def _get_query_type(self, statement):
        """获取查询类型"""
        statement_upper = statement.strip().upper()
        if statement_upper.startswith('SELECT'):
            return 'SELECT'
        elif statement_upper.startswith('INSERT'):
            return 'INSERT'
        elif statement_upper.startswith('UPDATE'):
            return 'UPDATE'
        elif statement_upper.startswith('DELETE'):
            return 'DELETE'
        elif statement_upper.startswith('CREATE'):
            return 'CREATE'
        elif statement_upper.startswith('ALTER'):
            return 'ALTER'
        elif statement_upper.startswith('DROP'):
            return 'DROP'
        else:
            return 'OTHER'
    
    def _get_table_name(self, statement):
        """从SQL语句中提取表名"""
        try:
            statement_upper = statement.strip().upper()
            words = statement_upper.split()
            
            if words[0] == 'SELECT':
                # 查找FROM后的表名
                from_idx = words.index('FROM')
                return words[from_idx + 1] if from_idx + 1 < len(words) else 'unknown'
            elif words[0] in ['INSERT', 'UPDATE', 'DELETE']:
                # 查找INTO/FROM后的表名
                for i, word in enumerate(words):
                    if word in ['INTO', 'FROM']:
                        return words[i + 1] if i + 1 < len(words) else 'unknown'
            elif words[0] == 'CREATE':
                # 查找TABLE后的表名
                if 'TABLE' in words:
                    table_idx = words.index('TABLE')
                    return words[table_idx + 1] if table_idx + 1 < len(words) else 'unknown'
            
            return 'unknown'
        except:
            return 'unknown'
    
    def _sanitize_sql(self, statement, parameters):
        """净化SQL语句，移除敏感信息"""
        try:
            # 简单的参数替换（避免敏感信息泄露）
            if parameters:
                if isinstance(parameters, dict):
                    safe_params = {}
                    for key, value in parameters.items():
                        if any(sensitive in key.lower() for sensitive in ['password', 'token', 'secret']):
                            safe_params[key] = '*' * 8
                        else:
                            safe_params[key] = value
                    return statement % safe_params
                else:
                    # 对于tuple/list参数，简单替换
                    return statement
            return statement
        except:
            return statement
    
    def _log_slow_query(self, statement, parameters, duration_ms, request_id):
        """记录慢查询"""
        logger.warning(f"🐌 [{request_id}] 慢查询警告: {duration_ms}ms")
        
        # 记录到专门的慢查询日志
        slow_query_logger = get_logger('slow_query')
        slow_query_logger.warning(f"慢查询 [{request_id}] {duration_ms}ms: {statement}")

def log_model_operation(operation_type, model_name, record_id=None, changes=None):
    """记录模型操作"""
    request_id = getattr(g, 'request_id', 'unknown')
    
    if operation_type == 'create':
        logger.info(f"➕ [{request_id}] 创建{model_name}: ID={record_id}")
    elif operation_type == 'update':
        logger.info(f"✏️ [{request_id}] 更新{model_name}: ID={record_id}")
        if changes and logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"📝 [{request_id}] 变更字段: {changes}")
    elif operation_type == 'delete':
        logger.info(f"🗑️ [{request_id}] 删除{model_name}: ID={record_id}")
    elif operation_type == 'query':
        logger.debug(f"🔍 [{request_id}] 查询{model_name}")

def log_transaction(operation_type, success=True, error=None):
    """记录事务操作"""
    request_id = getattr(g, 'request_id', 'unknown')
    
    if operation_type == 'begin':
        logger.debug(f"🔄 [{request_id}] 开始事务")
    elif operation_type == 'commit':
        if success:
            logger.info(f"✅ [{request_id}] 事务提交成功")
        else:
            logger.error(f"❌ [{request_id}] 事务提交失败: {error}")
    elif operation_type == 'rollback':
        logger.warning(f"🔄 [{request_id}] 事务回滚: {error}")