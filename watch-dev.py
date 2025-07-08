#!/usr/bin/env python
"""
YouTube Analytics 开发服务器自动重载工具
使用watchdog监控文件变化，自动重启Flask服务器
"""

import os
import sys
import time
import signal
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FlaskReloadHandler(FileSystemEventHandler):
    def __init__(self, restart_callback):
        self.restart_callback = restart_callback
        self.last_restart = 0
        super().__init__()
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        # 只监控Python文件
        if not event.src_path.endswith('.py'):
            return
        
        # 避免频繁重启（防抖动）
        current_time = time.time()
        if current_time - self.last_restart < 2:
            return
        
        self.last_restart = current_time
        print(f"🔄 检测到文件变化: {event.src_path}")
        self.restart_callback()

class DevServer:
    def __init__(self):
        self.process = None
        self.observer = None
        self.project_root = Path(__file__).parent
        self.backend_dir = self.project_root / "src" / "backend"
        
    def start_flask(self):
        """启动Flask服务器"""
        if self.process:
            self.stop_flask()
        
        print("🚀 启动Flask开发服务器...")
        
        # 设置环境变量
        env = os.environ.copy()
        env.update({
            'FLASK_ENV': 'development',
            'FLASK_DEBUG': '1',
            'PYTHONUNBUFFERED': '1'
        })
        
        # 启动Flask进程
        cmd = [sys.executable, "app.py"]
        self.process = subprocess.Popen(
            cmd,
            cwd=self.backend_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        print(f"✅ Flask服务器启动 (PID: {self.process.pid})")
        print("🌐 服务地址: http://localhost:5001")
        print("📝 自动重载: 启用")
        
    def stop_flask(self):
        """停止Flask服务器"""
        if self.process:
            print("🛑 停止Flask服务器...")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("⚠️ 强制终止Flask进程...")
                self.process.kill()
                self.process.wait()
            finally:
                self.process = None
    
    def restart_flask(self):
        """重启Flask服务器"""
        print("\n" + "="*50)
        self.stop_flask()
        time.sleep(1)  # 等待端口释放
        self.start_flask()
        print("="*50 + "\n")
    
    def setup_watcher(self):
        """设置文件监控"""
        self.observer = Observer()
        handler = FlaskReloadHandler(self.restart_flask)
        
        # 监控后端目录
        self.observer.schedule(handler, str(self.backend_dir), recursive=True)
        self.observer.start()
        print(f"👁️ 文件监控启动，监控目录: {self.backend_dir}")
    
    def run(self):
        """运行开发服务器"""
        print("🎯 YouTube Analytics 开发服务器")
        print("="*50)
        
        # 检查虚拟环境
        if not (self.project_root / "venv").exists():
            print("❌ 虚拟环境不存在，请先运行: python -m venv venv")
            return
        
        try:
            # 启动服务器和监控
            self.start_flask()
            self.setup_watcher()
            
            print("\n💡 提示:")
            print("   - 修改Python文件会自动重启服务器")
            print("   - 按 Ctrl+C 停止开发服务器")
            print("   - 日志会实时显示在下方")
            print("\n📋 实时日志:")
            print("-" * 50)
            
            # 实时显示Flask输出
            while True:
                if self.process and self.process.poll() is None:
                    try:
                        line = self.process.stdout.readline()
                        if line:
                            print(line.rstrip())
                    except:
                        pass
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\n\n🛑 收到停止信号...")
        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """清理资源"""
        print("🧹 清理资源...")
        
        if self.observer:
            self.observer.stop()
            self.observer.join()
        
        self.stop_flask()
        print("✅ 开发服务器已停止")

def main():
    """主函数"""
    # 确保在正确的目录运行
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # 检查Python路径
    backend_dir = script_dir / "src" / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    
    # 启动开发服务器
    server = DevServer()
    server.run()

if __name__ == "__main__":
    main()