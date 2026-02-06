#!/usr/bin/env python3
"""
批量修复所有页面的导航，统一使用共享导航组件
"""

import os
import re
import glob

def replace_navigation_in_file(file_path):
    """替换单个文件中的导航部分"""
    print(f"处理文件: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. 移除完整的header导航部分
    # 匹配从 <!-- 主导航栏 --> 或 <header 到 </header> 的内容
    header_patterns = [
        r'<!-- 主导航栏 -->.*?</header>',
        r'<header[^>]*>.*?</header>',
    ]
    
    for pattern in header_patterns:
        content = re.sub(pattern, '<!-- 导航由 shared-navigation.js 统一管理，实现真正的组件复用 -->', content, flags=re.DOTALL)
    
    # 2. 移除旧的navigation.js引用
    content = re.sub(r'<script src="js/navigation\.js[^"]*"></script>\s*', '', content)
    
    # 3. 添加共享导航脚本引用（如果还没有）
    if 'shared-navigation.js' not in content:
        # 在</body>之前添加脚本引用
        scripts_to_add = [
            '<script src="js/config.js"></script>',
            '<script src="js/api.js"></script>', 
            '<script src="js/utils.js"></script>',
            '<script src="js/shared-navigation.js"></script>'
        ]
        
        # 检查哪些脚本已经存在
        existing_scripts = []
        for script in scripts_to_add:
            script_name = script.split('src="')[1].split('"')[0]
            if script_name in content:
                existing_scripts.append(script)
        
        # 只添加不存在的脚本
        new_scripts = [s for s in scripts_to_add if s not in existing_scripts]
        
        if new_scripts:
            scripts_html = '    ' + '\n    '.join(new_scripts) + '\n'
            
            # 在其他脚本之前插入，或在</body>之前插入
            if '<script src="js/' in content:
                # 在第一个现有脚本之前插入
                content = re.sub(r'(\s*<script src="js/)', scripts_html + r'\1', content, count=1)
            else:
                # 在</body>之前插入
                content = re.sub(r'</body>', scripts_html + '</body>', content)
    
    # 4. 确保shared-navigation.js在其他页面特定脚本之前加载
    # 找到所有script标签，确保shared-navigation.js排在前面
    script_pattern = r'<script src="js/([^"]+)"></script>'
    scripts = re.findall(script_pattern, content)
    
    if 'shared-navigation.js' in scripts:
        # 移除所有js脚本引用
        content = re.sub(r'<script src="js/[^"]+"></script>\s*', '', content)
        
        # 按正确顺序重新添加
        ordered_scripts = ['config.js', 'api.js', 'utils.js', 'shared-navigation.js']
        other_scripts = [s for s in scripts if s not in ordered_scripts]
        all_scripts = ordered_scripts + other_scripts
        
        scripts_html = '\n'.join(f'    <script src="js/{script}"></script>' for script in all_scripts if script)
        scripts_html += '\n'
        
        content = re.sub(r'</body>', scripts_html + '</body>', content)
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ 完成: {file_path}")

def main():
    """主函数"""
    frontend_dir = 'src/frontend'
    
    # 找到所有HTML文件
    html_files = glob.glob(os.path.join(frontend_dir, '*.html'))
    
    print(f"找到 {len(html_files)} 个HTML文件")
    print("开始批量替换导航...")
    
    for html_file in html_files:
        try:
            replace_navigation_in_file(html_file)
        except Exception as e:
            print(f"❌ 处理文件 {html_file} 时出错: {e}")
    
    print("\n🎉 批量导航替换完成!")
    print("现在所有页面都使用统一的shared-navigation.js组件")

if __name__ == "__main__":
    main()