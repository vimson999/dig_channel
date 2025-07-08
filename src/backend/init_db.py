# 数据库初始化脚本
"""
YouTube频道数据追踪工具 - 数据库初始化

这个脚本负责：
- 创建数据库表结构
- 创建索引
- 创建排行榜视图
- 插入初始测试数据
"""

import os
import sys
from datetime import datetime, date, timedelta

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, Channel, ChannelData, Video, VideoData

def create_database_views(app):
    """创建排行榜视图"""
    with app.app_context():
        # 删除已存在的视图
        views_to_drop = [
            'daily_revenue_ranking',
            'monthly_revenue_ranking', 
            'daily_subscriber_ranking',
            'monthly_subscriber_ranking'
        ]
        
        for view_name in views_to_drop:
            try:
                db.session.execute(f"DROP VIEW IF EXISTS {view_name}")
            except Exception as e:
                print(f"删除视图 {view_name} 时出错: {e}")
        
        # 创建日收入排行榜视图
        daily_revenue_view = """
        CREATE VIEW daily_revenue_ranking AS
        SELECT 
            c.name,
            c.channel_id,
            cd.estimated_revenue,
            ROW_NUMBER() OVER (ORDER BY cd.estimated_revenue DESC) as rank
        FROM channels c
        JOIN channel_data cd ON c.id = cd.channel_id
        WHERE cd.date = date('now') AND c.is_active = 1
        ORDER BY cd.estimated_revenue DESC
        """
        
        # 创建月收入排行榜视图
        monthly_revenue_view = """
        CREATE VIEW monthly_revenue_ranking AS
        SELECT 
            c.name,
            c.channel_id,
            SUM(cd.estimated_revenue) as total_revenue,
            ROW_NUMBER() OVER (ORDER BY SUM(cd.estimated_revenue) DESC) as rank
        FROM channels c
        JOIN channel_data cd ON c.id = cd.channel_id
        WHERE cd.date >= date('now', '-30 days') AND c.is_active = 1
        GROUP BY c.id, c.name, c.channel_id
        ORDER BY total_revenue DESC
        """
        
        # 创建日订阅增长排行榜视图
        daily_subscriber_view = """
        CREATE VIEW daily_subscriber_ranking AS
        SELECT 
            c.name,
            c.channel_id,
            cd.subscriber_change,
            ROW_NUMBER() OVER (ORDER BY cd.subscriber_change DESC) as rank
        FROM channels c
        JOIN channel_data cd ON c.id = cd.channel_id
        WHERE cd.date = date('now') AND c.is_active = 1
        ORDER BY cd.subscriber_change DESC
        """
        
        # 创建月订阅增长排行榜视图
        monthly_subscriber_view = """
        CREATE VIEW monthly_subscriber_ranking AS
        SELECT 
            c.name,
            c.channel_id,
            SUM(cd.subscriber_change) as total_subscriber_change,
            ROW_NUMBER() OVER (ORDER BY SUM(cd.subscriber_change) DESC) as rank
        FROM channels c
        JOIN channel_data cd ON c.id = cd.channel_id
        WHERE cd.date >= date('now', '-30 days') AND c.is_active = 1
        GROUP BY c.id, c.name, c.channel_id
        ORDER BY total_subscriber_change DESC
        """
        
        try:
            db.session.execute(daily_revenue_view)
            db.session.execute(monthly_revenue_view)
            db.session.execute(daily_subscriber_view)
            db.session.execute(monthly_subscriber_view)
            db.session.commit()
            print("排行榜视图创建成功！")
        except Exception as e:
            print(f"创建视图时出错: {e}")
            db.session.rollback()

def insert_sample_data(app):
    """插入示例数据"""
    with app.app_context():
        # 检查是否已有数据
        if Channel.query.first():
            print("数据库中已有数据，跳过示例数据插入")
            return
        
        # 创建示例频道
        sample_channels = [
            {
                'name': '李子柒 Liziqi',
                'channel_id': 'UCoC47do520os_4DBMEFGg4A',
                'url': 'https://www.youtube.com/channel/UCoC47do520os_4DBMEFGg4A',
                'cpm': 2.50,
                'category': '生活方式'
            },
            {
                'name': '回形针PaperClip',
                'channel_id': 'UCBdZW-fzDEOaqglOVoRjXLA',
                'url': 'https://www.youtube.com/channel/UCBdZW-fzDEOaqglOVoRjXLA',
                'cpm': 3.00,
                'category': '科技教育'
            },
            {
                'name': 'Tech Test Channel',
                'channel_id': 'UC_TEST_CHANNEL_ID_123',
                'url': 'https://www.youtube.com/channel/UC_TEST_CHANNEL_ID_123',
                'cpm': 1.80,
                'category': '科技'
            }
        ]
        
        created_channels = []
        for channel_data in sample_channels:
            try:
                channel = Channel.create_channel(**channel_data)
                created_channels.append(channel)
                print(f"创建频道: {channel.name}")
            except Exception as e:
                print(f"创建频道 {channel_data['name']} 时出错: {e}")
        
        # 为每个频道创建最近几天的示例数据
        for channel in created_channels:
            base_subscribers = 1000000 + (channel.id * 100000)
            base_views = 50000000 + (channel.id * 10000000)
            base_videos = 100 + (channel.id * 50)
            
            # 创建最近7天的数据
            for i in range(7):
                data_date = date.today() - timedelta(days=6-i)
                
                # 模拟数据增长
                daily_sub_growth = 1000 + (i * 200)
                daily_view_growth = 50000 + (i * 10000)
                daily_video_growth = 1 if i % 2 == 0 else 0
                
                subscribers = base_subscribers + (i * daily_sub_growth)
                total_views = base_views + (i * daily_view_growth)
                video_count = base_videos + (i * daily_video_growth)
                
                try:
                    # 创建数据记录
                    data_record = ChannelData.create_data_record(
                        channel_id=channel.id,
                        date_value=data_date,
                        subscribers=subscribers,
                        total_views=total_views,
                        video_count=video_count
                    )
                    
                    # 计算变化（获取前一天数据）
                    if i > 0:
                        previous_date = data_date - timedelta(days=1)
                        previous_data = ChannelData.query.filter_by(
                            channel_id=channel.id, 
                            date=previous_date
                        ).first()
                        data_record.calculate_changes(previous_data)
                    else:
                        data_record.calculate_changes(None)
                    
                    print(f"创建数据记录: {channel.name} - {data_date}")
                    
                except Exception as e:
                    print(f"创建数据记录时出错: {e}")
        
        print("示例数据插入完成！")

def main():
    """主函数"""
    print("开始初始化数据库...")
    
    # 创建Flask应用
    app = create_app()
    
    with app.app_context():
        try:
            # 创建所有表
            db.create_all()
            print("数据库表创建成功！")
            
            # 创建视图
            create_database_views(app)
            
            # 插入示例数据
            insert_sample_data(app)
            
            print("数据库初始化完成！")
            
        except Exception as e:
            print(f"数据库初始化失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    main()