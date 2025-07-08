#!/usr/bin/env python3
"""
批量导入频道脚本
从 docs/channel_list.txt 文件读取频道URL并添加到数据库
"""

import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'src', 'backend'))

from app import create_app
from models import db
from models.channel import Channel
from urllib.parse import urlparse
import time

def extract_channel_handle(url):
    """从URL提取频道handle"""
    try:
        parsed = urlparse(url)
        path = parsed.path
        
        # 处理 @handle 格式
        if path.startswith('/@'):
            return path[2:]  # 移除 /@ 前缀
        
        # 处理其他格式
        if path.startswith('/channel/'):
            return path[9:]  # 移除 /channel/ 前缀
        elif path.startswith('/c/'):
            return path[3:]   # 移除 /c/ 前缀
        elif path.startswith('/user/'):
            return path[6:]   # 移除 /user/ 前缀
            
        return None
    except Exception as e:
        print(f"解析URL失败: {url}, 错误: {e}")
        return None

def import_channels():
    """导入频道"""
    app = create_app()
    
    with app.app_context():
        # 读取频道列表
        channels_file = os.path.join(project_root, 'docs', 'channel_list.txt')
        
        if not os.path.exists(channels_file):
            print(f"错误: 找不到文件 {channels_file}")
            return
        
        with open(channels_file, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
        
        print(f"找到 {len(urls)} 个频道URL")
        
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] 处理频道: {url}")
            
            # 提取频道handle
            handle = extract_channel_handle(url)
            if not handle:
                print(f"  ❌ 无法解析频道URL: {url}")
                error_count += 1
                continue
            
            # 检查是否已存在
            existing = Channel.query.filter(Channel.url == url).first()
            
            if existing:
                print(f"  ⏭️  频道已存在: {existing.name}")
                skip_count += 1
                continue
            
            try:
                print(f"  ➕ 创建基础频道记录...")
                
                # 创建基础频道记录（稍后可以通过同步功能获取详细信息）
                channel = Channel(
                    channel_id=f"handle_{handle}_{int(time.time())}",  # 临时ID
                    name=handle.replace('_', ' ').title(),  # 格式化名称
                    url=url,
                    cpm=1.00,
                    category='AI/Tech',
                    is_active=True
                )
                
                # 保存到数据库
                db.session.add(channel)
                db.session.commit()
                
                print(f"  ✅ 成功添加: {channel.name}")
                success_count += 1
                
            except Exception as e:
                print(f"  ❌ 添加失败: {e}")
                error_count += 1
                db.session.rollback()
        
        print(f"\n" + "="*50)
        print(f"导入完成!")
        print(f"成功: {success_count}")
        print(f"跳过: {skip_count}")
        print(f"失败: {error_count}")
        print(f"总计: {len(urls)}")

if __name__ == '__main__':
    import_channels()