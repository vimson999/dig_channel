#!/usr/bin/env python3
"""
数据库健康检查工具

用于诊断和修复数据库连接问题
"""

import time
import sys
import os
from datetime import datetime
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import db
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("DB_HEALTH")

class DatabaseHealthChecker:
    """数据库健康检查器"""
    
    def __init__(self, app):
        self.app = app
        self.db = db
        
    @contextmanager
    def timer(self, operation):
        """计时上下文管理器"""
        start = time.time()
        try:
            yield
            duration = (time.time() - start) * 1000
            if duration > 1000:
                logger.warning(f"⏰ {operation} 耗时: {duration:.2f}ms (超过1秒)")
            else:
                logger.info(f"⚡ {operation} 耗时: {duration:.2f}ms")
        except Exception as e:
            duration = (time.time() - start) * 1000
            logger.error(f"❌ {operation} 失败 (耗时: {duration:.2f}ms): {str(e)}")
            raise
    
    def check_connection(self):
        """检查数据库连接"""
        logger.info("🔍 开始数据库连接检查...")
        
        try:
            with self.timer("数据库连接"):
                with self.app.app_context():
                    # 测试基本连接 (兼容SQLAlchemy 1.4+)
                    from sqlalchemy import text
                    result = db.session.execute(text('SELECT 1 as test'))
                    test_value = result.scalar()
                    
                    if test_value == 1:
                        logger.info("✅ 数据库连接正常")
                        return True
                    else:
                        logger.error("❌ 数据库连接异常: 返回值不正确")
                        return False
                        
        except Exception as e:
            logger.error(f"❌ 数据库连接失败: {str(e)}")
            return False
    
    def check_pool_status(self):
        """检查连接池状态"""
        logger.info("🔍 检查数据库连接池状态...")
        
        try:
            with self.app.app_context():
                pool = db.engine.pool
                
                logger.info(f"📊 连接池状态:")
                logger.info(f"   - 池大小: {pool.size()}")
                logger.info(f"   - 检出连接: {pool.checkedout()}")
                try:
                    logger.info(f"   - 无效连接: {pool.invalidated()}")
                except AttributeError:
                    logger.info(f"   - 无效连接: 不支持此方法")
                logger.info(f"   - 检入连接: {pool.checkedin()}")
                
                return True
                
        except Exception as e:
            logger.error(f"❌ 检查连接池状态失败: {str(e)}")
            return False
    
    def check_table_access(self):
        """检查表访问权限"""
        logger.info("🔍 检查表访问权限...")
        
        test_queries = [
            ("用户表", "SELECT COUNT(*) FROM users"),
            ("频道表", "SELECT COUNT(*) FROM channels"),
            ("视频表", "SELECT COUNT(*) FROM videos")
        ]
        
        all_success = True
        
        with self.app.app_context():
            for table_name, query in test_queries:
                try:
                    with self.timer(f"查询{table_name}"):
                        from sqlalchemy import text
                        result = db.session.execute(text(query))
                        count = result.scalar()
                        logger.info(f"✅ {table_name}: {count} 条记录")
                        
                except Exception as e:
                    logger.error(f"❌ {table_name}访问失败: {str(e)}")
                    all_success = False
        
        return all_success
    
    def check_slow_queries(self):
        """检查慢查询"""
        logger.info("🔍 检查潜在的慢查询...")
        
        slow_queries = [
            ("复杂用户查询", """
                SELECT u.id, u.username, COUNT(v.id) as video_count 
                FROM users u 
                LEFT JOIN channels c ON u.id = c.user_id 
                LEFT JOIN videos v ON c.id = v.channel_id 
                GROUP BY u.id, u.username 
                LIMIT 5
            """),
            ("视频统计查询", """
                SELECT COUNT(*) as total, 
                       AVG(view_count) as avg_views,
                       MAX(view_count) as max_views
                FROM videos 
                WHERE is_active = true
            """)
        ]
        
        with self.app.app_context():
            for query_name, query in slow_queries:
                try:
                    with self.timer(f"执行{query_name}"):
                        from sqlalchemy import text
                        result = db.session.execute(text(query))
                        rows = result.fetchall()
                        logger.info(f"✅ {query_name}: 返回 {len(rows)} 行")
                        
                except Exception as e:
                    logger.error(f"❌ {query_name}失败: {str(e)}")
                    return False
        
        return True
    
    def fix_connection_issues(self):
        """尝试修复连接问题"""
        logger.info("🔧 尝试修复数据库连接问题...")
        
        try:
            with self.app.app_context():
                # 关闭所有连接
                logger.info("🔄 关闭现有连接...")
                db.engine.dispose()
                
                # 等待一段时间
                time.sleep(2)
                
                # 重新测试连接
                logger.info("🔄 重新建立连接...")
                from sqlalchemy import text
                result = db.session.execute(text('SELECT 1'))
                
                if result.scalar() == 1:
                    logger.info("✅ 连接修复成功")
                    return True
                else:
                    logger.error("❌ 连接修复失败")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ 修复过程中出错: {str(e)}")
            return False
    
    def run_full_check(self):
        """运行完整的健康检查"""
        logger.info("🚀 开始完整的数据库健康检查...")
        
        checks = [
            ("基本连接检查", self.check_connection),
            ("连接池状态检查", self.check_pool_status),
            ("表访问权限检查", self.check_table_access),
            ("慢查询检查", self.check_slow_queries)
        ]
        
        results = {}
        
        for check_name, check_func in checks:
            logger.info(f"📋 执行: {check_name}")
            try:
                results[check_name] = check_func()
            except Exception as e:
                logger.error(f"❌ {check_name}过程中出错: {str(e)}")
                results[check_name] = False
        
        # 输出总结
        logger.info("📊 检查结果总结:")
        for check_name, success in results.items():
            status = "✅ 通过" if success else "❌ 失败"
            logger.info(f"   - {check_name}: {status}")
        
        success_count = sum(results.values())
        total_count = len(results)
        
        if success_count == total_count:
            logger.info("🎉 所有检查都通过了!")
            return True
        else:
            logger.warning(f"⚠️ {total_count - success_count} 个检查失败")
            
            # 如果有失败，尝试修复
            logger.info("🔧 尝试自动修复...")
            if self.fix_connection_issues():
                logger.info("✅ 自动修复成功，建议重启应用")
            else:
                logger.error("❌ 自动修复失败，需要手动处理")
            
            return False

def main():
    """主函数"""
    from app import create_app
    
    app = create_app()
    checker = DatabaseHealthChecker(app)
    
    success = checker.run_full_check()
    
    if not success:
        logger.error("数据库存在问题，建议检查以下几点:")
        logger.error("1. 数据库服务是否正常运行")
        logger.error("2. 网络连接是否稳定")
        logger.error("3. 数据库配置是否正确")
        logger.error("4. 是否有长时间运行的查询或锁定")
        sys.exit(1)
    else:
        logger.info("数据库健康状况良好!")
        sys.exit(0)

if __name__ == "__main__":
    main()