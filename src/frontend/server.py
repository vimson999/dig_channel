#!/usr/bin/env python3
"""
简单的前端服务器
用于服务静态文件（HTML、CSS、JS）
"""

import http.server
import socketserver
import os
import sys

PORT = 8888

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 添加CORS头部
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        super().end_headers()

class ReusableThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    # 切换到前端目录
    frontend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(frontend_dir)
    
    # 启动服务器
    with ReusableThreadingTCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"🌐 前端服务器启动在: http://localhost:{PORT}")
        print(f"📁 服务目录: {frontend_dir}")
        print(f"🔗 可以打开浏览器访问 http://localhost:{PORT}")
        print("按 Ctrl+C 停止服务器")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 前端服务器已停止")
            sys.exit(0)
