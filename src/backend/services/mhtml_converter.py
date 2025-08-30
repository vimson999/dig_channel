"""
MHTML 转 PDF 转换服务
使用 Chrome Headless 实现高质量转换
"""

import os
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
import shutil
import logging

from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("MHTML_CONVERTER")

class MHTMLToPDFConverter:
    """MHTML 转 PDF 转换器"""
    
    def __init__(self):
        self.chrome_path = self._find_chrome()
        self.temp_dir = tempfile.gettempdir()
        
    def _find_chrome(self):
        """查找系统中的 Chrome 可执行文件"""
        possible_paths = [
            # macOS
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
            # Linux
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
            # Windows
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
            # 通过 PATH 环境变量
            'google-chrome',
            'chromium-browser',
            'chrome',
            'chromium'
        ]
        
        for path in possible_paths:
            if shutil.which(path) or (os.path.isfile(path) and os.access(path, os.X_OK)):
                logger.info(f"✅ 找到 Chrome: {path}")
                return path
        
        logger.error("❌ 未找到 Chrome 浏览器，请安装 Google Chrome 或 Chromium")
        raise FileNotFoundError("未找到 Chrome 浏览器")
    
    def _test_chrome_compatibility(self):
        """测试Chrome兼容性"""
        test_cmd = [
            self.chrome_path,
            '--version'
        ]
        
        try:
            result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                version = result.stdout.strip()
                logger.info(f"Chrome版本检测: {version}")
                return True, version
            else:
                return False, result.stderr
                
        except Exception as e:
            return False, str(e)
    
    def convert(self, mhtml_file_path, output_pdf_path=None, options=None):
        """
        将 MHTML 文件转换为 PDF
        
        Args:
            mhtml_file_path: MHTML 文件路径
            output_pdf_path: 输出 PDF 路径（可选）
            options: 转换选项
        
        Returns:
            转换后的 PDF 文件路径
        """
        if not os.path.exists(mhtml_file_path):
            raise FileNotFoundError(f"MHTML 文件不存在: {mhtml_file_path}")
        
        # 检查文件大小，给出预警
        file_size = os.path.getsize(mhtml_file_path)
        if file_size > 50 * 1024 * 1024:  # 50MB
            logger.warning(f"⚠️ MHTML文件较大 ({file_size/1024/1024:.1f}MB)，转换可能需要3-4分钟")
        elif file_size > 10 * 1024 * 1024:  # 10MB
            logger.info(f"📁 MHTML文件大小: {file_size/1024/1024:.1f}MB，预计转换时间1-2分钟")
        else:
            logger.info(f"📁 MHTML文件大小: {file_size/1024:.0f}KB")
        
        # 生成输出文件路径
        if not output_pdf_path:
            file_stem = Path(mhtml_file_path).stem
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_pdf_path = os.path.join(
                self.temp_dir, 
                f"pdf_{file_stem}_{timestamp}_{uuid.uuid4().hex[:8]}.pdf"
            )
        
        # 默认转换选项
        default_options = {
            'format': 'A4',
            'margin_top': '1cm',
            'margin_right': '1cm',
            'margin_bottom': '1cm',
            'margin_left': '1cm',
            'print_background': True,
            'disable_web_security': True,
            'no_sandbox': True
        }
        
        if options:
            default_options.update(options)
        
        try:
            # 测试Chrome兼容性
            is_compatible, version_info = self._test_chrome_compatibility()
            if not is_compatible:
                logger.error(f"Chrome兼容性测试失败: {version_info}")
                raise Exception("Chrome浏览器不可用或版本不兼容")
            
            logger.info(f"🔄 开始转换 MHTML 到 PDF")
            logger.info(f"   输入文件: {mhtml_file_path}")
            logger.info(f"   输出文件: {output_pdf_path}")
            logger.debug(f"   Chrome版本: {version_info}")
            
            # 检查MHTML文件内容特征
            with open(mhtml_file_path, 'rb') as f:
                header = f.read(1024).decode('utf-8', errors='ignore')
                if 'multipart/related' not in header:
                    logger.warning("⚠️ 文件可能不是标准MHTML格式")
                if 'Content-Location:' in header:
                    logger.debug("📄 检测到MHTML内容定位信息")
                if len(header.splitlines()) > 50:
                    logger.info("📊 MHTML文件包含大量头部信息，可能较复杂")
            
            # 构建 Chrome 命令 - 尝试更简化的参数，专注MHTML处理
            cmd = [
                self.chrome_path,
                '--headless=new',
                '--disable-gpu',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-web-security',  # 允许跨域访问MHTML内容
                '--allow-file-access-from-files',  # 允许文件访问
                '--disable-features=VizDisplayCompositor',
                '--no-first-run',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-background-downloads',
                '--disable-background-networking',
                '--no-proxy-server',  # 不使用代理
                f'--user-data-dir=/tmp/chrome-mhtml-{uuid.uuid4().hex[:8]}',
                f'--print-to-pdf={output_pdf_path}',
                '--print-to-pdf-no-header',  # 不打印页眉
                '--print-to-pdf-no-footer',  # 不打印页脚
                '--print-to-pdf-display-header-footer=false',  # 确保不显示页眉页脚
                f'--virtual-time-budget=45000',  # 45秒等待页面渲染完成，给足够时间
                '--run-all-compositor-stages-before-draw',
                '--disable-partial-raster',  # 禁用部分光栅化，确保完整渲染
                '--disable-features=LazyImageLoading',  # 禁用懒加载，确保所有图片都加载
                '--force-device-scale-factor=1',  # 设置设备缩放比例为1
                '--disable-background-media-suspend',  # 禁用后台媒体暂停
            ]
            
            # 添加页面格式选项
            if default_options.get('format'):
                cmd.append(f'--print-to-pdf-format={default_options["format"]}')
            
            # 添加边距选项
            if default_options.get('margin_top'):
                cmd.append(f'--print-to-pdf-margin-top={default_options["margin_top"]}')
            if default_options.get('margin_right'):
                cmd.append(f'--print-to-pdf-margin-right={default_options["margin_right"]}')
            if default_options.get('margin_bottom'):
                cmd.append(f'--print-to-pdf-margin-bottom={default_options["margin_bottom"]}')
            if default_options.get('margin_left'):
                cmd.append(f'--print-to-pdf-margin-left={default_options["margin_left"]}')
            
            # 添加输入文件（使用 file:// URL）
            input_url = f'file://{os.path.abspath(mhtml_file_path)}'
            cmd.append(input_url)
            
            # 记录临时用户数据目录以便清理
            temp_user_data_dir = None
            for arg in cmd:
                if arg.startswith('--user-data-dir='):
                    temp_user_data_dir = arg.split('=', 1)[1]
                    break
            
            logger.debug(f"Chrome 命令: {' '.join(cmd[:5])}...")  # 只显示前几个参数，避免日志过长
            logger.info("⏳ Chrome进程启动中，请耐心等待...")
            
            # 执行转换
            # 设置环境变量以减少资源使用
            env = {
                **os.environ,
                'DISPLAY': '',  # 确保没有显示相关的环境变量
                'CHROME_DEVEL_SANDBOX': '',  # 禁用沙箱相关功能
                'CHROME_LOG_FILE': '/dev/null',  # 重定向日志到空设备
                'TMPDIR': tempfile.gettempdir(),  # 确保临时目录正确设置
            }
            
            # 给Chrome足够时间等待渲染完成
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=75,  # 75秒超时，配合45秒virtual-time-budget
                env=env,
                # 设置进程优先级和资源限制
                preexec_fn=lambda: (
                    os.nice(10),  # 降低进程优先级
                    None
                )[1] if hasattr(os, 'nice') else None
            )
            
            # 清理临时用户数据目录
            if temp_user_data_dir and os.path.exists(temp_user_data_dir):
                try:
                    import shutil
                    shutil.rmtree(temp_user_data_dir)
                    logger.debug(f"🗑️ 清理临时用户数据目录: {temp_user_data_dir}")
                except Exception as cleanup_error:
                    logger.warning(f"清理临时目录失败: {cleanup_error}")
            
            if result.returncode == 0:
                if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
                    file_size = os.path.getsize(output_pdf_path)
                    logger.info(f"✅ 转换成功! PDF 文件大小: {file_size} bytes")
                    return output_pdf_path
                else:
                    raise Exception("PDF 文件生成失败或为空")
            else:
                error_msg = result.stderr or result.stdout or "未知错误"
                
                # 检查常见的错误模式并提供更友好的错误信息
                if "Too many open files" in error_msg or "resource temporarily unavailable" in error_msg.lower():
                    user_friendly_msg = "系统资源不足，请稍后重试。如问题持续出现，请联系管理员"
                elif "icudtl.dat not found" in error_msg or "icu" in error_msg.lower():
                    user_friendly_msg = "Chrome浏览器组件缺失，请重新安装Chrome或使用系统默认浏览器"
                elif "No such file or directory" in error_msg or "chrome" in error_msg.lower():
                    user_friendly_msg = "找不到Chrome浏览器，请安装Google Chrome或Chromium"
                elif "Permission denied" in error_msg or "access" in error_msg.lower():
                    user_friendly_msg = "没有权限访问Chrome浏览器或创建临时文件"
                elif "timeout" in error_msg.lower() or "time" in error_msg.lower():
                    user_friendly_msg = "转换超时，请检查MHTML文件大小或网络连接"
                elif "memory" in error_msg.lower() or "out of memory" in error_msg.lower():
                    user_friendly_msg = "内存不足，请稍后重试或减小文件大小"
                else:
                    user_friendly_msg = "PDF转换失败，请检查文件格式或稍后重试"
                
                logger.error(f"Chrome错误详情: {error_msg[:500]}...")  # 只记录前500字符
                raise Exception(user_friendly_msg)
                
        except subprocess.TimeoutExpired as e:
            logger.warning(f"⚠️ Chrome进程超时被终止({e.timeout}秒)，检查PDF是否已生成...")
            
            # 检查PDF文件是否在超时前已经生成
            if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
                file_size = os.path.getsize(output_pdf_path)
                logger.info(f"✅ PDF已生成！虽然进程超时，但转换成功完成 ({file_size} bytes)")
                return output_pdf_path
            
            # 如果PDF未生成或为空，则真正失败
            logger.error(f"❌ 转换超时且PDF未生成: Chrome进程运行了{e.timeout}秒后被终止")
            if hasattr(e, 'stdout') and e.stdout:
                logger.debug(f"Chrome标准输出: {e.stdout[:200]}...")
            if hasattr(e, 'stderr') and e.stderr:
                logger.debug(f"Chrome错误输出: {e.stderr[:200]}...")
            
            # 提供详细的问题诊断信息
            file_size_mb = file_size / (1024 * 1024)
            if file_size_mb > 20:
                suggestion = f"文件过大({file_size_mb:.1f}MB)，建议使用专业MHTML转换工具"
            else:
                suggestion = "MHTML文件可能包含Chrome无法处理的复杂内容，建议检查文件格式或使用其他转换方式"
            
            raise Exception(f"Chrome转换超时且未生成PDF。{suggestion}。备用方案：尝试使用在线MHTML转换服务或将MHTML在浏览器中另存为PDF")
        except Exception as e:
            logger.error(f"❌ 转换失败: {str(e)}")
            # 清理可能的部分文件
            if os.path.exists(output_pdf_path):
                try:
                    os.remove(output_pdf_path)
                except:
                    pass
            raise
    
    def batch_convert(self, mhtml_files, output_dir=None):
        """
        批量转换 MHTML 文件
        
        Args:
            mhtml_files: MHTML 文件路径列表
            output_dir: 输出目录（可选）
        
        Returns:
            转换结果列表 [{'input': path, 'output': path, 'success': bool, 'error': str}]
        """
        if not output_dir:
            output_dir = self.temp_dir
        
        results = []
        
        for mhtml_file in mhtml_files:
            result = {
                'input': mhtml_file,
                'output': None,
                'success': False,
                'error': None
            }
            
            try:
                file_stem = Path(mhtml_file).stem
                output_path = os.path.join(output_dir, f"{file_stem}.pdf")
                
                converted_path = self.convert(mhtml_file, output_path)
                result['output'] = converted_path
                result['success'] = True
                
            except Exception as e:
                result['error'] = str(e)
                logger.error(f"❌ 批量转换失败: {mhtml_file} - {str(e)}")
            
            results.append(result)
        
        success_count = sum(1 for r in results if r['success'])
        logger.info(f"📊 批量转换完成: {success_count}/{len(mhtml_files)} 成功")
        
        return results
    
    def validate_mhtml(self, mhtml_file_path):
        """
        验证 MHTML 文件格式
        
        Args:
            mhtml_file_path: MHTML 文件路径
        
        Returns:
            bool: 是否为有效的 MHTML 文件
        """
        try:
            with open(mhtml_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(1024)  # 只读前1KB检查
                
                # MHTML 文件应该包含这些特征
                mhtml_indicators = [
                    'MIME-Version:',
                    'Content-Type: multipart/related',
                    'boundary='
                ]
                
                for indicator in mhtml_indicators:
                    if indicator in content:
                        return True
                        
                return False
                
        except Exception as e:
            logger.error(f"MHTML 验证失败: {str(e)}")
            return False
    
    def cleanup_temp_files(self, max_age_hours=24):
        """
        清理临时PDF文件
        
        Args:
            max_age_hours: 文件最大保留时间（小时）
        """
        try:
            import time
            current_time = time.time()
            cleanup_count = 0
            
            for filename in os.listdir(self.temp_dir):
                if filename.startswith('pdf_') and filename.endswith('.pdf'):
                    file_path = os.path.join(self.temp_dir, filename)
                    try:
                        file_age = current_time - os.path.getmtime(file_path)
                        if file_age > max_age_hours * 3600:  # 转换为秒
                            os.remove(file_path)
                            cleanup_count += 1
                            logger.debug(f"🗑️ 清理过期文件: {filename}")
                    except Exception as e:
                        logger.warning(f"清理文件失败: {filename}, 错误: {str(e)}")
            
            logger.info(f"🧹 临时文件清理完成: 清理了 {cleanup_count} 个过期PDF文件")
            return cleanup_count
            
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")
            return 0

# 全局转换器实例
converter = MHTMLToPDFConverter()