#!/usr/bin/env python3
"""
简单的前端服务器
用于服务静态文件（HTML、CSS、JS）
"""

import http.server
import html
import json
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = 8888

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    zsxq_backend_url = "http://127.0.0.1:5001"

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/bridge/zsxq/health":
            self._handle_zsxq_health()
            return
        if path == "/zsxq-sidebar-bridge.html":
            self._handle_zsxq_health_page()
            return
        super().do_GET()

    def _fetch_zsxq_health(self):
        health_url = f"{self.zsxq_backend_url.rstrip('/')}/health"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(health_url, timeout=3) as response:
            return json.load(response)

    def _handle_zsxq_health(self):
        try:
            payload = self._fetch_zsxq_health()
        except (OSError, ValueError, urllib.error.URLError):
            self._send_json(
                502,
                {
                    "status": "unavailable",
                    "error": "zsxq_backend_unavailable",
                },
            )
            return

        self._send_json(200, payload)

    def _handle_zsxq_health_page(self):
        try:
            payload = self._fetch_zsxq_health()
        except (OSError, ValueError, urllib.error.URLError):
            payload = {
                "status": "unavailable",
                "version": "unknown",
            }

        status = html.escape(str(payload.get("status", "unknown")))
        version = html.escape(str(payload.get("version", "unknown")))
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>知识星球本地桥接</title>
</head>
<body>
  <main>
    <h1>知识星球本地桥接</h1>
    <p>服务状态：{status}</p>
    <p>版本：{version}</p>
  </main>
</body>
</html>
"""
        self._send_html(200, body)

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code, body):
        encoded_body = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded_body)))
        self.end_headers()
        self.wfile.write(encoded_body)

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
    with ReusableThreadingTCPServer(("127.0.0.1", PORT), MyHTTPRequestHandler) as httpd:
        print(f"🌐 前端服务器启动在: http://localhost:{PORT}")
        print(f"📁 服务目录: {frontend_dir}")
        print(f"🔗 可以打开浏览器访问 http://localhost:{PORT}")
        print("按 Ctrl+C 停止服务器")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 前端服务器已停止")
            sys.exit(0)
