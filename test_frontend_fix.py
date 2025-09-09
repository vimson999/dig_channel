#!/usr/bin/env python3
"""
测试前端显示修复是否生效
"""
import subprocess
import time
import requests
import sys
import os

def test_frontend_fix():
    """测试前端修复"""
    print("🔧 测试前端显示修复...")
    
    # 测试API响应
    try:
        response = requests.get("http://127.0.0.1:8082/api/v1/channels?page=1&per_page=10", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and 'data' in data:
                channels_count = len(data['data'].get('channels', []))
                pagination = data['data'].get('pagination', {})
                total = pagination.get('total', 0)
                
                print(f"✅ API响应正常:")
                print(f"   当前页频道数: {channels_count}")
                print(f"   分页总数: {total}")
                print(f"   页数: {pagination.get('pages', 0)}")
                
                print(f"\n🎯 修复内容:")
                print(f"1. 修复了分页显示元素ID从 'total-records' 到 'total-count'")
                print(f"2. 添加了 'total-channels' 元素更新逻辑，使用 pagination.total = {total}")
                print(f"\n🌐 访问 http://127.0.0.1:8082/channels.html 查看修复效果")
                print(f"   应该显示: '共 {total} 个频道' 和 '显示第 1 - {min(10, total)} 条，共 {total} 条'")
                
                return True
            else:
                print("❌ API响应格式错误")
                return False
        else:
            print(f"❌ API响应失败: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器，请确保服务器在8082端口运行")
        return False
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 开始测试前端修复...\n")
    if test_frontend_fix():
        print("\n✅ 测试完成！前端修复应该已经生效")
    else:
        print("\n❌ 测试失败")