#!/usr/bin/env python3
"""
完整功能测试脚本
测试学生证生成器的所有功能包括正面和背面生成
"""

import webbrowser
import time
import threading
from app import create_app

def run_complete_test():
    """运行完整功能测试"""
    print("🚀 启动学生证生成器完整功能测试...")
    
    app = create_app()
    
    # 在后台启动服务器
    def run_server():
        app.run(host='127.0.0.1', port=8082, debug=False, use_reloader=False)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 等待服务器启动
    time.sleep(3)

    print("✅ 服务器已启动在 http://127.0.0.1:8082")
    print("📖 访问页面: http://127.0.0.1:8082/student-id-generator.html")
    print()
    print("🎯 测试项目:")
    print("   1. ✨ 随机填充功能 - 点击 'ランダム入力' 按钮")
    print("   2. 📸 照片上传功能 - 拖拽或点击上传照片")
    print("   3. 📱 实时预览功能 - 输入内容时查看预览变化")
    print("   4. 🎴 正面生成功能 - 点击 '表面を生成する' 按钮")
    print("   5. 💾 正面下载功能 - 生成后点击下载按钮")
    print("   6. 🔄 背面生成功能 - 点击 '裏面を生成する' 按钮")
    print("   7. 🖨️ 打印功能测试")
    print()
    print("🎨 新功能特点:")
    print("   • 学校名称显示在蓝色顶栏中")
    print("   • 有效期采用通栏样式布局")
    print("   • 背面包含学校信息、注意事项和版本信息")
    print("   • 下载功能已修复，支持正确的文件命名")
    print()
    print("🎲 测试建议:")
    print("   • 测试不同长度的学校名称")
    print("   • 测试各种有效期组合")
    print("   • 验证正面和背面的视觉效果")
    print("   • 检查文件下载功能")
    print()
    print("🔄 按Ctrl+C停止服务器")

    try:
        # 保持服务器运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n👋 测试完成，服务器已停止')

if __name__ == "__main__":
    run_complete_test()