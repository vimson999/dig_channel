"""
数据库操作日志记录中间件
记录所有数据库操作的详细信息，包括查询、执行时间、影响行数等
"""
import hashlib
import logging
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from celery import current_task
from flask import g
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool
from utils.colored_logger import setup_colored_logger, log_database_operation

logger = setup_colored_logger(__name__)
_listener_lock = threading.Lock()
_backend_root = Path(__file__).resolve().parents[1]
_process_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_publication_task_table_pattern = re.compile(
    r'\b(?:from|update|into|delete\s+from)\s+(?:"?public"?\.)?"?publication_tasks"?\b',
    re.IGNORECASE,
)
_sql_parameter_pattern = re.compile(r"%\([^)]+\)s|:\w+|\$\d+")
_sql_literal_pattern = re.compile(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b")
_query_origin_helper_names = {
    "_load_task",
    "_load_repair_task",
    "_load_publication_task",
    "_load_task_with_retries",
}
_connection_checkout_key = "dig_ytb_checkout_monotonic"
_connection_context_key = "dig_ytb_checkout_context"
_connection_sql_count_key = "dig_ytb_checkout_sql_count"
_connection_sql_total_ms_key = "dig_ytb_checkout_sql_total_ms"
_connection_sql_max_ms_key = "dig_ytb_checkout_sql_max_ms"


def _connection_hold_warn_seconds():
    try:
        return max(
            0.1,
            float(os.environ.get("DB_CONNECTION_HOLD_WARN_SECONDS", "5")),
        )
    except (TypeError, ValueError):
        return 5.0


def _pool_checkout(dbapi_connection, connection_record, connection_proxy):
    connection_record.info[_connection_checkout_key] = time.monotonic()
    task_id, task_name = DatabaseLoggerMiddleware._celery_task_context()
    connection_record.info[_connection_context_key] = {
        "request_id": DatabaseLoggerMiddleware._request_id(),
        "task_id": task_id,
        "task_name": task_name,
        "origin": DatabaseLoggerMiddleware._query_origin(),
        "thread": threading.current_thread().name,
    }
    connection_record.info[_connection_sql_count_key] = 0
    connection_record.info[_connection_sql_total_ms_key] = 0.0
    connection_record.info[_connection_sql_max_ms_key] = 0.0


def _pool_checkin(dbapi_connection, connection_record):
    started_at = connection_record.info.pop(_connection_checkout_key, None)
    context = connection_record.info.pop(_connection_context_key, {}) or {}
    sql_count = int(connection_record.info.pop(_connection_sql_count_key, 0) or 0)
    sql_total_ms = float(
        connection_record.info.pop(_connection_sql_total_ms_key, 0.0) or 0.0
    )
    sql_max_ms = float(
        connection_record.info.pop(_connection_sql_max_ms_key, 0.0) or 0.0
    )
    if started_at is None:
        return
    held_seconds = time.monotonic() - started_at
    if held_seconds < _connection_hold_warn_seconds():
        return
    lifecycle_logger = setup_colored_logger("db_connection_lifecycle")
    held_ms = round(held_seconds * 1000, 2)
    lifecycle_logger.warning(
        "database connection held too long duration_ms=%s pid=%s role=%s "
        "process=%s process_started_at=%s request_id=%s task_id=%s task_name=%s "
        "thread=%s checkout_origin=%s sql_count=%s sql_total_ms=%s "
        "sql_max_ms=%s non_sql_hold_ms=%s",
        held_ms,
        os.getpid(),
        DatabaseLoggerMiddleware._process_role(),
        DatabaseLoggerMiddleware._process_name(),
        _process_started_at,
        context.get("request_id") or "unknown",
        context.get("task_id") or "unknown",
        context.get("task_name") or "unknown",
        context.get("thread") or "unknown",
        context.get("origin") or "unknown",
        sql_count,
        round(sql_total_ms, 2),
        round(sql_max_ms, 2),
        round(max(0.0, held_ms - sql_total_ms), 2),
    )

class DatabaseLoggerMiddleware:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化数据库日志记录"""
        with _listener_lock:
            if not event.contains(Engine, "before_cursor_execute", self._before_cursor_execute):
                event.listen(Engine, "before_cursor_execute", self._before_cursor_execute)
            if not event.contains(Engine, "after_cursor_execute", self._after_cursor_execute):
                event.listen(Engine, "after_cursor_execute", self._after_cursor_execute)
            if not event.contains(Engine, "handle_error", self._handle_error):
                event.listen(Engine, "handle_error", self._handle_error)
            if not event.contains(Pool, "checkout", _pool_checkout):
                event.listen(Pool, "checkout", _pool_checkout)
            if not event.contains(Pool, "checkin", _pool_checkin):
                event.listen(Pool, "checkin", _pool_checkin)
        
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
        request_id = self._request_id()
        query_type = self._get_query_type(statement)
        
        logger.debug(f"🔍 [{request_id}] SQL开始: {query_type}")
        
        # 记录完整SQL（仅在调试模式下）
        if logger.isEnabledFor(logging.DEBUG):
            safe_sql = self._sanitize_sql(statement, parameters)
            logger.debug(f"📝 [{request_id}] SQL: {safe_sql}")
    
    def _after_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        """SQL执行后记录"""
        query_start_times = conn.info.setdefault('query_start_time', [])
        if query_start_times:
            started_at = query_start_times.pop(-1)
        else:
            started_at = time.time()
            logger.debug("SQL query_start_time stack is empty; skipping accurate duration.")
        total_time = time.time() - started_at
        duration_ms = round(total_time * 1000, 2)
        self._record_checkout_sql_duration(conn, duration_ms)
        
        request_id = self._request_id()
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
        
        # Reaching after_cursor_execute means the database operation succeeded.
        # Duration controls the slow-query warning, never the success flag.
        success = True
        details = f"耗时: {duration_ms}ms, 影响行数: {rowcount}"
        log_database_operation(query_type, self._get_table_name(statement), success, details)
        
        # 记录慢查询
        if duration_ms > 1000:
            self._log_slow_query(
                statement,
                parameters,
                duration_ms,
                request_id,
                dialect_name=getattr(getattr(conn, "dialect", None), "name", "unknown"),
            )

        diagnostic = self._publication_task_diagnostic(statement, duration_ms=duration_ms)
        if diagnostic:
            self._log_publication_task_query(
                statement,
                duration_ms=duration_ms,
                request_id=request_id,
                diagnostic=diagnostic,
                dialect_name=getattr(getattr(conn, "dialect", None), "name", "unknown"),
            )

    def _handle_error(self, exception_context):
        """Capture failed hot-table statements; after_cursor_execute is not called on failure."""
        try:
            connection = getattr(exception_context, "connection", None)
            query_start_times = self._query_start_times_quietly(connection)
            started_at = query_start_times.pop(-1) if query_start_times else time.time()
            duration_ms = round((time.time() - started_at) * 1000, 2)
            self._record_checkout_sql_duration(connection, duration_ms)
            statement = getattr(exception_context, "statement", None) or ""
            error = getattr(exception_context, "original_exception", None)
            diagnostic = self._publication_task_diagnostic(
                statement,
                duration_ms=duration_ms,
                error=error,
            )
            if diagnostic:
                self._log_publication_task_query(
                    statement,
                    duration_ms=duration_ms,
                    request_id=self._request_id(),
                    diagnostic=diagnostic,
                    error=error,
                    dialect_name=getattr(
                        getattr(connection, "dialect", None),
                        "name",
                        "unknown",
                    ),
                )
        except Exception:
            # Diagnostics must never replace the original database exception.
            logger.exception("Failed to record publication_tasks SQL error diagnostics")

    @staticmethod
    def _query_start_times_quietly(connection):
        if connection is None:
            return []
        try:
            return connection.info.setdefault('query_start_time', [])
        except Exception:
            return []

    @staticmethod
    def _record_checkout_sql_duration(connection, duration_ms):
        if connection is None:
            return
        try:
            info = connection.info
            info[_connection_sql_count_key] = int(
                info.get(_connection_sql_count_key, 0) or 0
            ) + 1
            total_ms = float(info.get(_connection_sql_total_ms_key, 0.0) or 0.0)
            info[_connection_sql_total_ms_key] = total_ms + float(duration_ms or 0.0)
            current_max = float(info.get(_connection_sql_max_ms_key, 0.0) or 0.0)
            info[_connection_sql_max_ms_key] = max(
                current_max,
                float(duration_ms or 0.0),
            )
        except Exception:
            # Lifecycle telemetry must never affect the database operation.
            return
    
    @staticmethod
    def _get_query_type(statement):
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
            elif words[0] == 'UPDATE':
                return words[1] if len(words) > 1 else 'unknown'
            elif words[0] in ['INSERT', 'DELETE']:
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
    
    def _log_slow_query(
        self,
        statement,
        parameters,
        duration_ms,
        request_id,
        *,
        dialect_name="unknown",
    ):
        """记录慢查询"""
        logger.warning(f"🐌 [{request_id}] 慢查询警告: {duration_ms}ms")
        
        # 记录到专门的慢查询日志
        slow_query_logger = setup_colored_logger('slow_query')
        slow_query_logger.warning(
            "慢查询 [%s] %sms [pid=%s role=%s process=%s dialect=%s origin=%s]: %s",
            request_id,
            duration_ms,
            os.getpid(),
            self._process_role(),
            self._process_name(),
            dialect_name,
            self._query_origin(),
            statement,
        )

    @classmethod
    def _publication_task_diagnostic(cls, statement, *, duration_ms, error=None):
        """Classify risky or slow publication_tasks SQL without inspecting parameter values."""
        sql = str(statement or "")
        if not _publication_task_table_pattern.search(sql):
            return None

        compact = " ".join(sql.split())
        lowered = compact.lower()
        query_type = cls._get_query_type(compact)
        projection = ""
        predicate = ""
        if query_type == "SELECT" and " from " in lowered:
            from_index = lowered.index(" from ")
            projection = lowered[:from_index]
            predicate = lowered[from_index:]
        elif query_type == "UPDATE":
            where_index = lowered.find(" where ")
            projection = lowered if where_index < 0 else lowered[:where_index]
            predicate = "" if where_index < 0 else lowered[where_index:]

        if query_type == "UPDATE":
            payload_columns = [
                column
                for column in ("params", "result")
                if re.search(
                    rf'\b(?:publication_tasks[."]+)?{column}\b\s*=',
                    projection,
                )
            ]
        else:
            payload_columns = [
                column
                for column in ("params", "result")
                if re.search(
                    rf'\bpublication_tasks[."]+{column}\b'
                    r'(?=\s+(?:as\b|from\b)|\s*,|\s*$)',
                    projection,
                )
            ]
        json_predicate = bool(
            query_type == "SELECT"
            and re.search(
                r'(?:publication_tasks[."]+)?(?:params|result)\b\s*(?:->|#>)'
                r'|jsonb?_extract_path(?:_text)?\s*\([^)]*(?:params|result)',
                predicate,
            )
        )
        point_lookup_pattern = (
            r'\b(?:publication_tasks[."]+)?id\b\s*=\s*(?:%\(|:\w+|\$\d+)'
            if query_type == "UPDATE"
            else r'\bpublication_tasks[."]+id\b\s*=\s*(?:%\(|:\w+|\$\d+)'
        )
        point_lookup = bool(re.search(point_lookup_pattern, predicate))
        multirow_payload = bool(payload_columns and not point_lookup)
        payload_write = bool(payload_columns and query_type == "UPDATE")
        slow_threshold_ms = cls._publication_task_slow_ms()

        categories = []
        if error is not None:
            categories.append("error")
        if json_predicate:
            categories.append("json_predicate")
        if multirow_payload:
            categories.append("multirow_payload")
        if payload_write and duration_ms >= slow_threshold_ms:
            categories.append("slow_payload_write")
        if payload_columns and duration_ms >= slow_threshold_ms:
            categories.append("slow_payload")
        if duration_ms >= slow_threshold_ms:
            categories.append("slow")
        if not categories:
            return None

        return {
            "categories": tuple(categories),
            "fingerprint": cls._sql_fingerprint(compact),
            "operation": query_type,
            "payload_columns": tuple(payload_columns),
            "point_lookup": point_lookup,
        }

    @staticmethod
    def _publication_task_slow_ms():
        try:
            return max(50.0, float(os.environ.get("PUBLICATION_TASK_SLOW_MS", "500")))
        except (TypeError, ValueError):
            return 500.0

    @staticmethod
    def _sql_fingerprint(statement):
        normalized = _sql_parameter_pattern.sub("?", statement.lower())
        normalized = _sql_literal_pattern.sub("?", normalized)
        normalized = " ".join(normalized.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _log_publication_task_query(
        self,
        statement,
        *,
        duration_ms,
        request_id,
        diagnostic,
        error=None,
        dialect_name="unknown",
    ):
        hot_table_logger = setup_colored_logger("publication_tasks_query")
        error_name = type(error).__name__ if error is not None else "none"
        error_text = str(error or "").replace("\n", " ")[:500]
        hot_table_logger.warning(
            "publication_tasks SQL categories=%s fingerprint=%s duration_ms=%s "
            "operation=%s payload=%s point_lookup=%s request_id=%s pid=%s "
            "role=%s process=%s dialect=%s process_started_at=%s origin=%s "
            "error_type=%s error=%s sql=%s",
            ",".join(diagnostic["categories"]),
            diagnostic["fingerprint"],
            duration_ms,
            diagnostic["operation"],
            ",".join(diagnostic["payload_columns"]) or "none",
            diagnostic["point_lookup"],
            request_id,
            os.getpid(),
            self._process_role(),
            self._process_name(),
            dialect_name,
            _process_started_at,
            self._query_origin(),
            error_name,
            error_text or "none",
            statement,
        )

    @staticmethod
    def _process_role():
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            return "test"
        return os.environ.get("DB_PROCESS_ROLE") or "unknown"

    @staticmethod
    def _process_name():
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            return "pytest"
        return Path(sys.argv[0] or "python").name

    @staticmethod
    def _request_id():
        try:
            return getattr(g, "request_id", "unknown")
        except RuntimeError:
            return "unknown"

    @staticmethod
    def _celery_task_context():
        try:
            request = current_task.request
            task_id = str(getattr(request, "id", "") or "unknown")
            task_name = str(getattr(current_task, "name", "") or "unknown")
            return task_id, task_name
        except Exception:
            return "unknown", "unknown"

    @staticmethod
    def _query_origin():
        for frame in reversed(traceback.extract_stack(limit=50)):
            if str(frame.filename).startswith("<"):
                continue
            try:
                relative = Path(frame.filename).resolve().relative_to(_backend_root)
            except (OSError, ValueError):
                continue
            if relative.as_posix() == "middleware/database_logger.py":
                continue
            if frame.name in _query_origin_helper_names:
                continue
            return f"{relative.as_posix()}:{frame.lineno}:{frame.name}"
        return "unknown"

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
