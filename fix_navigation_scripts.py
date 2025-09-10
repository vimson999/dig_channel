#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob

def fix_navigation_scripts_in_file(file_path):
    """修复单个HTML文件的导航脚本引用"""
    print(f"🔧 处理文件: {os.path.basename(file_path)}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # 检查是否已经有shared-navigation.js引用
        if 'shared-navigation.js' in content:
            print(f"  ✅ 已包含shared-navigation.js引用")
            return True
        
        # 查找JavaScript部分的位置（通常在</body>前面）
        # 寻找现有的JavaScript文件引用模式
        js_pattern = r'(<script src="js/[^"]*\.js"></script>\s*)+(\s*</body>)'
        
        if re.search(js_pattern, content):
            # 在现有JS文件后添加shared-navigation.js
            def replace_js_section(match):
                existing_scripts = match.group(1)
                body_close = match.group(2)
                
                # 如果还没有shared-navigation.js，添加它
                if 'shared-navigation.js' not in existing_scripts:
                    new_script = '    <script src="js/shared-navigation.js"></script>\n'
                    return existing_scripts + new_script + body_close
                return match.group(0)
            
            content = re.sub(js_pattern, replace_js_section, content)
        else:
            # 如果没找到现有JS模式，在</body>前添加基本的脚本
            body_close_pattern = r'(\s*</body>)'
            if re.search(body_close_pattern, content):
                scripts_to_add = '''    <!-- JavaScript -->
    <script src="js/env.js"></script>
    <script>
    // 设置全局API配置
    window.API_BASE_URL = API_BASE_URL;
    </script>
    <script src="js/config.js"></script>
    <script src="js/utils.js"></script>
    <script src="js/api.js"></script>
    <script src="js/shared-navigation.js"></script>
'''
                content = re.sub(body_close_pattern, scripts_to_add + r'\1', content)
            else:
                print(f"  ❌ 找不到</body>标签")
                return False
        
        # 只有内容发生变化时才写入文件
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  ✅ 已添加shared-navigation.js引用")
        else:
            print(f"  ℹ️  无需修改")
            
        return True
        
    except Exception as e:
        print(f"  ❌ 处理失败: {str(e)}")
        return False

def main():
    """主函数：批量修复所有HTML文件的导航脚本引用"""
    print("🚀 开始修复导航脚本引用...")
    
    # 查找所有HTML文件
    html_files = glob.glob('/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/src/frontend/*.html')
    
    success_count = 0
    total_count = len(html_files)
    
    print(f"📋 找到 {total_count} 个HTML文件")
    print("-" * 50)
    
    for file_path in sorted(html_files):
        if fix_navigation_scripts_in_file(file_path):
            success_count += 1
    
    print("-" * 50)
    print(f"🎉 修复完成!")
    print(f"📊 成功处理: {success_count}/{total_count} 个文件")
    print("✅ 现在所有页面都应该能正确显示导航了!")

if __name__ == "__main__":
    main()