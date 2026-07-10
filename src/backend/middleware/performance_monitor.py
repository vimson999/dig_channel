"""
性能监控中间件
记录系统性能指标，包括响应时间、内存使用、CPU使用率等
"""
import os
import time
import psutil
import threading
from flask import g, request
from functools import wraps
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger(__name__)

class PerformanceMonitor:
    def __init__(self, app=None):
        self.app = app
        self.metrics = {
            'request_count': 0,
            'total_response_time': 0,
            'max_response_time': 0,
            'min_response_time': float('inf'),
            'error_count': 0,
            'slow_requests': 0
        }
        self.lock = threading.Lock()
        self._monitor_lock = threading.Lock()
        self._monitor_thread = None
        self._monitor_pid = None
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app, *, start_system_monitor=True):
        """初始化性能监控"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        
        if start_system_monitor:
            self._start_system_monitor()
    
    def before_request(self):
        """请求开始前记录"""
        g.perf_start_time = time.time()
        g.perf_start_memory = psutil.Process().memory_info().rss / 1024 / 1024  # MB
    
    def after_request(self, response):
        """请求结束后记录性能指标"""
        if hasattr(g, 'perf_start_time'):
            duration = time.time() - g.perf_start_time
            duration_ms = round(duration * 1000, 2)
            
            # 记录内存使用变化
            if hasattr(g, 'perf_start_memory'):
                end_memory = psutil.Process().memory_info().rss / 1024 / 1024  # MB
                memory_delta = round(end_memory - g.perf_start_memory, 2)
                
                if abs(memory_delta) > 1:  # 内存变化超过1MB才记录
                    memory_icon = '📈' if memory_delta > 0 else '📉'
                    logger.debug(f"{memory_icon} 内存变化: {memory_delta:+.2f}MB")
            
            # 更新性能统计
            self._update_metrics(duration_ms, response.status_code)
            
            # 记录性能指标
            self._log_performance_metrics(duration_ms, response.status_code)
        
        return response
    
    def _update_metrics(self, duration_ms, status_code):
        """更新性能统计"""
        with self.lock:
            self.metrics['request_count'] += 1
            self.metrics['total_response_time'] += duration_ms
            self.metrics['max_response_time'] = max(self.metrics['max_response_time'], duration_ms)
            self.metrics['min_response_time'] = min(self.metrics['min_response_time'], duration_ms)
            
            if status_code >= 400:
                self.metrics['error_count'] += 1
            
            if duration_ms > 1000:  # 超过1秒的请求
                self.metrics['slow_requests'] += 1
    
    def _log_performance_metrics(self, duration_ms, status_code):
        """记录性能指标"""
        request_id = getattr(g, 'request_id', 'unknown')
        
        # 根据响应时间选择不同的日志级别
        if duration_ms > 2000:  # 超过2秒
            logger.warning(f"🐌 [{request_id}] 响应时间过长: {duration_ms}ms")
        elif duration_ms > 1000:  # 超过1秒
            logger.info(f"⏱️ [{request_id}] 响应时间较长: {duration_ms}ms")
        else:
            logger.debug(f"⚡ [{request_id}] 响应时间: {duration_ms}ms")
        
        # 记录资源使用情况
        if logger.isEnabledFor(10):  # DEBUG level
            self._log_resource_usage(request_id)
    
    def _log_resource_usage(self, request_id):
        """记录资源使用情况"""
        try:
            process = psutil.Process()
            
            # CPU使用率
            cpu_percent = process.cpu_percent()
            if cpu_percent > 80:
                logger.warning(f"🔥 [{request_id}] CPU使用率过高: {cpu_percent}%")
            elif cpu_percent > 50:
                logger.info(f"📊 [{request_id}] CPU使用率: {cpu_percent}%")
            
            # 内存使用
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            if memory_mb > 500:  # 超过500MB
                logger.warning(f"💾 [{request_id}] 内存使用过高: {memory_mb:.2f}MB")
            elif memory_mb > 200:  # 超过200MB
                logger.info(f"💾 [{request_id}] 内存使用: {memory_mb:.2f}MB")
            
            # 打开的文件描述符数量
            num_fds = process.num_fds()
            if num_fds > 100:
                logger.warning(f"📂 [{request_id}] 文件描述符过多: {num_fds}")
            
        except Exception as e:
            logger.debug(f"⚠️ [{request_id}] 获取资源使用情况失败: {e}")
    
    def _start_system_monitor(self):
        """Start one system monitor thread per process."""
        def monitor_system():
            while True:
                try:
                    # 每5分钟记录一次系统整体状态
                    time.sleep(300)
                    self._log_system_metrics()
                except Exception as e:
                    logger.error(f"🚨 系统监控异常: {e}")
        
        with self._monitor_lock:
            current_pid = os.getpid()
            if (
                self._monitor_pid == current_pid
                and self._monitor_thread is not None
                and self._monitor_thread.is_alive()
            ):
                return False

            monitor_thread = threading.Thread(
                target=monitor_system,
                name="performance-system-monitor",
                daemon=True,
            )
            monitor_thread.start()
            self._monitor_pid = current_pid
            self._monitor_thread = monitor_thread
            return True
    
    def _log_system_metrics(self):
        """记录系统整体指标"""
        try:
            # 系统CPU和内存
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # 记录系统状态
            logger.info(f"🖥️ 系统状态 - CPU: {cpu_percent}%, 内存: {memory.percent}%, 磁盘: {disk.percent}%")
            
            # 记录应用性能统计
            with self.lock:
                if self.metrics['request_count'] > 0:
                    avg_response_time = self.metrics['total_response_time'] / self.metrics['request_count']
                    error_rate = (self.metrics['error_count'] / self.metrics['request_count']) * 100
                    slow_rate = (self.metrics['slow_requests'] / self.metrics['request_count']) * 100
                    
                    logger.info(f"📊 应用统计 - 平均响应时间: {avg_response_time:.2f}ms, "
                              f"错误率: {error_rate:.2f}%, 慢请求率: {slow_rate:.2f}%")
                
                # 重置统计（避免数据过大）
                self.metrics = {
                    'request_count': 0,
                    'total_response_time': 0,
                    'max_response_time': 0,
                    'min_response_time': float('inf'),
                    'error_count': 0,
                    'slow_requests': 0
                }
        
        except Exception as e:
            logger.error(f"🚨 记录系统指标失败: {e}")
    
    def get_metrics(self):
        """获取当前性能指标"""
        with self.lock:
            metrics = self.metrics.copy()
            if metrics['request_count'] > 0:
                metrics['avg_response_time'] = metrics['total_response_time'] / metrics['request_count']
                metrics['error_rate'] = (metrics['error_count'] / metrics['request_count']) * 100
                metrics['slow_rate'] = (metrics['slow_requests'] / metrics['request_count']) * 100
            return metrics

def monitor_performance(threshold_ms=1000):
    """装饰器：监控函数执行性能"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            request_id = getattr(g, 'request_id', 'unknown')
            
            # 记录开始
            logger.debug(f"🔍 [{request_id}] 开始执行 {func.__name__}")
            
            try:
                result = func(*args, **kwargs)
                duration_ms = round((time.time() - start_time) * 1000, 2)
                
                # 根据执行时间选择日志级别
                if duration_ms > threshold_ms:
                    logger.warning(f"🐌 [{request_id}] {func.__name__} 执行耗时: {duration_ms}ms (超过阈值)")
                elif duration_ms > threshold_ms / 2:
                    logger.info(f"⏱️ [{request_id}] {func.__name__} 执行耗时: {duration_ms}ms")
                else:
                    logger.debug(f"⚡ [{request_id}] {func.__name__} 执行耗时: {duration_ms}ms")
                
                return result
                
            except Exception as e:
                duration_ms = round((time.time() - start_time) * 1000, 2)
                logger.error(f"💥 [{request_id}] {func.__name__} 执行失败({duration_ms}ms): {e}")
                raise
        
        return wrapper
    return decorator
