#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob

def fix_missing_navigation(file_path):
    """检查和修复缺少shared-navigation.js的HTML文件"""
    filename = os.path.basename(file_path)
    print(f"🔍 检查文件: {filename}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否包含shared-navigation.js的script标签
        if '<script src="js/shared-navigation.js"></script>' in content:
            print(f"  ✅ 已正确包含shared-navigation.js")
            return True
        
        # 检查是否在</body>前有其他script标签
        if '</body>' not in content:
            print(f"  ❌ 找不到</body>标签")
            return False
        
        # 在最后一个script标签后添加shared-navigation.js
        # 找到</body>前的位置
        body_pos = content.rfind('</body>')
        
        # 插入shared-navigation.js
        new_script = '    <script src="js/shared-navigation.js"></script>\n'
        new_content = content[:body_pos] + new_script + content[body_pos:]
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"  ✅ 已添加shared-navigation.js")
        return True
        
    except Exception as e:
        print(f"  ❌ 处理失败: {str(e)}")
        return False

def main():
    """主函数"""
    print("🚀 检查和修复缺失的导航脚本...")
    
    # 查找所有HTML文件
    html_files = glob.glob('/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/src/frontend/*.html')
    
    success_count = 0
    fixed_count = 0
    total_count = len(html_files)
    
    print(f"📋 找到 {total_count} 个HTML文件")
    print("-" * 50)
    
    for file_path in sorted(html_files):
        if fix_missing_navigation(file_path):
            success_count += 1
    
    print("-" * 50)
    print(f"🎉 检查完成!")
    print(f"📊 成功处理: {success_count}/{total_count} 个文件")

if __name__ == "__main__":
    main()