#!/usr/bin/env python3
"""批量同步频道CPM为分类默认值的脚本"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 将项目根目录与backend模块加入搜索路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / 'src' / 'backend'
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from services.channel_service import ChannelService


def main() -> None:
    """命令行入口"""
    parser = argparse.ArgumentParser(description='同步频道CPM为分类默认值')
    parser.add_argument('--user-id', type=int, default=None, help='仅同步指定用户ID的频道')
    parser.add_argument('--dry-run', action='store_true', help='仅预览变更数量，不提交数据库')

    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = ChannelService.sync_category_defaults(user_id=args.user_id, commit=not args.dry_run)

    scope = f"用户 {args.user_id}" if args.user_id is not None else '所有用户'
    print('📊 CPM同步结果')
    print(f'  范围: {scope}')
    print(f"  频道总数: {result['total']}")
    print(f"  调整数量: {result['updated']}")
    if args.dry_run:
        print('  注意: dry-run 模式未执行数据库提交')


if __name__ == '__main__':
    main()
