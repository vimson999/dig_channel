#!/usr/bin/env python3
"""
测试下载功能修复
"""

import webbrowser
import time
import threading
from app import create_app

def test_download_functionality():
    """测试下载功能"""
    print("🔄 测试学生证下载功能修复...")
    
    app = create_app()
    
    # 在后台启动服务器
    def run_server():
        app.run(host='127.0.0.1', port=8083, debug=False, use_reloader=False)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 等待服务器启动
    time.sleep(3)

    print("✅ 服务器已启动在 http://127.0.0.1:8083")
    print("📖 访问页面: http://127.0.0.1:8083/student-id-generator.html")
    print()
    print("🔧 下载功能修复内容:")
    print("   1. ✅ 在生成学生证前自动更新学生数据")
    print("   2. ✅ 确保 generatedImageUrl 和 generatedBlob 正确设置")
    print("   3. ✅ 背面生成也更新了数据同步")
    print()
    print("🧪 测试步骤:")
    print("   1. 点击绿色 '石川県立看護大学' 按钮填充数据")
    print("   2. 点击 '表面を生成する' 生成正面")
    print("   3. 点击 'ダウンロード' 按钮测试下载")
    print("   4. 点击 '裏面を生成する' 测试背面下载")
    print()
    print("🎯 预期效果:")
    print("   • 正面下载：文件名包含学生姓名和日期")
    print("   • 背面下载：自动下载，无需额外点击")
    print("   • 图片完整：通栏完全贴边")
    print()
    print("🔄 按Ctrl+C停止服务器")

    try:
        # 保持服务器运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n👋 测试完成，服务器已停止')

if __name__ == "__main__":
    test_download_functionality()