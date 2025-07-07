#!/usr/bin/env python3
# API接口测试脚本

import sys
import os
import requests
import json
from datetime import date

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_api():
    """测试所有API接口"""
    print("🔧 API接口测试")
    print("=" * 50)
    
    # API基础URL
    base_url = "http://localhost:5000"
    
    # 1. 测试基础接口
    print("\n1. 测试基础接口...")
    
    try:
        # 健康检查
        response = requests.get(f"{base_url}/health")
        if response.status_code == 200:
            print("✅ 健康检查通过")
            print(f"   {response.json()}")
        else:
            print(f"❌ 健康检查失败: {response.status_code}")
            
        # API信息
        response = requests.get(f"{base_url}/api")
        if response.status_code == 200:
            print("✅ API信息获取成功")
            api_info = response.json()
            print(f"   API名称: {api_info['data']['name']}")
            print(f"   版本: {api_info['data']['version']}")
        else:
            print(f"❌ API信息获取失败: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到API服务器")
        print("   请确保Flask应用正在运行: python app.py")
        return False
    
    # 2. 测试频道API
    print("\n2. 测试频道API...")
    
    # 获取频道列表
    response = requests.get(f"{base_url}/api/v1/channels")
    if response.status_code == 200:
        channels_data = response.json()
        print(f"✅ 获取频道列表成功")
        channels = channels_data['data']['channels']
        print(f"   频道数量: {len(channels)}")
        if channels:
            print(f"   第一个频道: {channels[0]['name']}")
    else:
        print(f"❌ 获取频道列表失败: {response.status_code}")
        print(f"   {response.text}")
    
    # 预览频道（YouTube官方频道）
    preview_data = {
        "url": "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ"
    }
    response = requests.post(
        f"{base_url}/api/v1/channels/preview",
        json=preview_data,
        headers={'Content-Type': 'application/json'}
    )
    if response.status_code == 200:
        preview_result = response.json()
        print("✅ 频道预览成功")
        channel_info = preview_result['data']['channel_info']
        print(f"   频道名: {channel_info['title']}")
        print(f"   订阅数: {channel_info['subscriber_count']:,}")
    else:
        print(f"❌ 频道预览失败: {response.status_code}")
        print(f"   {response.text}")
    
    # 3. 测试排行榜API
    print("\n3. 测试排行榜API...")
    
    # 获取所有排行榜
    response = requests.get(f"{base_url}/api/v1/rankings/all?limit=3")
    if response.status_code == 200:
        rankings_data = response.json()
        print("✅ 获取排行榜成功")
        data = rankings_data['data']
        
        # 显示各排行榜信息
        if data.get('daily_revenue') and data['daily_revenue']['rankings']:
            print(f"   日收入排行榜: {len(data['daily_revenue']['rankings'])}条")
        
        if data.get('monthly_revenue') and data['monthly_revenue']['rankings']:
            print(f"   月收入排行榜: {len(data['monthly_revenue']['rankings'])}条")
            
        if data.get('daily_subscriber') and data['daily_subscriber']['rankings']:
            print(f"   日订阅排行榜: {len(data['daily_subscriber']['rankings'])}条")
            
        if data.get('monthly_subscriber') and data['monthly_subscriber']['rankings']:
            print(f"   月订阅排行榜: {len(data['monthly_subscriber']['rankings'])}条")
    else:
        print(f"❌ 获取排行榜失败: {response.status_code}")
        print(f"   {response.text}")
    
    # 获取排行榜类型
    response = requests.get(f"{base_url}/api/v1/rankings/types")
    if response.status_code == 200:
        types_data = response.json()
        print("✅ 获取排行榜类型成功")
        print(f"   支持的排行榜类型: {types_data['data']['total']}种")
    else:
        print(f"❌ 获取排行榜类型失败: {response.status_code}")
    
    # 4. 测试数据API
    print("\n4. 测试数据API...")
    
    # 获取表格数据
    response = requests.get(f"{base_url}/api/v1/data/table?per_page=5")
    if response.status_code == 200:
        table_data = response.json()
        print("✅ 获取表格数据成功")
        data = table_data['data']
        print(f"   频道数量: {data['total_channels']}")
        print(f"   数据天数: {data['total_days']}")
        print(f"   当前页数据: {len(data['rows'])}行")
    else:
        print(f"❌ 获取表格数据失败: {response.status_code}")
        print(f"   {response.text}")
    
    # 获取统计信息
    response = requests.get(f"{base_url}/api/v1/data/summary")
    if response.status_code == 200:
        summary_data = response.json()
        print("✅ 获取统计信息成功")
        stats = summary_data['data']
        print(f"   活跃频道: {stats['active_channels']}个")
        print(f"   总记录数: {stats['total_records']}条")
        print(f"   今日收入: ${stats['today']['revenue']:.2f}")
    else:
        print(f"❌ 获取统计信息失败: {response.status_code}")
        print(f"   {response.text}")
    
    # 获取图表类型
    response = requests.get(f"{base_url}/api/v1/data/chart-types")
    if response.status_code == 200:
        chart_types_data = response.json()
        print("✅ 获取图表类型成功")
        print(f"   支持的图表类型: {chart_types_data['data']['total']}种")
    else:
        print(f"❌ 获取图表类型失败: {response.status_code}")
    
    # 5. 测试错误处理
    print("\n5. 测试错误处理...")
    
    # 测试404错误
    response = requests.get(f"{base_url}/api/v1/nonexistent")
    if response.status_code == 404:
        print("✅ 404错误处理正常")
    else:
        print(f"❌ 404错误处理异常: {response.status_code}")
    
    # 测试无效JSON
    response = requests.post(
        f"{base_url}/api/v1/channels/preview",
        data="invalid json",
        headers={'Content-Type': 'application/json'}
    )
    if response.status_code == 400:
        print("✅ 无效JSON错误处理正常")
    else:
        print(f"❌ 无效JSON错误处理异常: {response.status_code}")
    
    # 测试缺少必填字段
    response = requests.post(
        f"{base_url}/api/v1/channels/preview",
        json={},
        headers={'Content-Type': 'application/json'}
    )
    if response.status_code == 400:
        print("✅ 缺少必填字段错误处理正常")
    else:
        print(f"❌ 缺少必填字段错误处理异常: {response.status_code}")
    
    print("\n" + "=" * 50)
    print("🎯 API接口测试完成")
    
    return True

def test_specific_endpoints():
    """测试特定API端点的详细功能"""
    print("\n" + "=" * 50)
    print("🔍 详细端点测试")
    
    base_url = "http://localhost:5000"
    
    # 测试分页
    print("\n测试分页功能...")
    response = requests.get(f"{base_url}/api/v1/channels?page=1&per_page=2")
    if response.status_code == 200:
        data = response.json()['data']
        pagination = data['pagination']
        print(f"✅ 分页测试: 第{pagination['page']}页，每页{pagination['per_page']}条，共{pagination['total']}条")
    
    # 测试搜索
    print("\n测试搜索功能...")
    response = requests.get(f"{base_url}/api/v1/channels/search?q=test&limit=5")
    if response.status_code == 200:
        data = response.json()['data']
        print(f"✅ 搜索测试: 关键词'{data['keyword']}'，找到{data['total']}个结果")
    
    # 测试分类
    print("\n测试分类功能...")
    response = requests.get(f"{base_url}/api/v1/channels/categories")
    if response.status_code == 200:
        data = response.json()['data']
        print(f"✅ 分类测试: 共{data['total']}个分类")
        if data['categories']:
            print(f"   示例分类: {data['categories'][0]['name']} ({data['categories'][0]['count']}个频道)")

if __name__ == '__main__':
    print("启动Flask应用进行测试...")
    print("请在另一个终端运行: python app.py")
    print("然后按Enter继续测试...")
    input()
    
    if test_api():
        test_specific_endpoints()