"""
文档转换API接口
支持Markdown和Word文档的相互转换
"""

import os
import tempfile
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
import pypandoc
import logging

# 创建蓝图
bp = Blueprint('documents', __name__, url_prefix='/api/v1/documents')

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 支持的文件格式
ALLOWED_EXTENSIONS = {
    'markdown': ['.md', '.markdown', '.txt'],
    'docx': ['.docx', '.doc']
}

# 文件大小限制 (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

def allowed_file(filename, source_format):
    """检查文件扩展名是否被允许"""
    if not filename or '.' not in filename:
        return False
    
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS.get(source_format, [])

def get_file_size_str(size_bytes):
    """将字节数转换为可读的文件大小字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

@bp.route('/convert', methods=['POST'])
def convert_document():
    """
    文档转换接口
    """
    logger.info("📨 收到文档转换请求")
    try:
        # 检查是否有文件上传
        if 'file' not in request.files:
            logger.warning("⚠️  请求中没有文件")
            return jsonify({
                'success': False,
                'message': '没有选择文件'
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '没有选择文件'
            }), 400
        
        # 获取转换参数
        source_format = request.form.get('source_format', '').lower()
        target_format = request.form.get('target_format', '').lower()
        
        logger.info(f"📋 转换参数: {source_format} -> {target_format}")
        logger.info(f"📁 文件信息: {file.filename}, 大小: {file.tell() if hasattr(file, 'tell') else '未知'}")
        
        if not source_format or not target_format:
            return jsonify({
                'success': False,
                'message': '缺少源格式或目标格式参数'
            }), 400
        
        # 验证支持的格式
        supported_formats = ['markdown', 'docx']
        if source_format not in supported_formats or target_format not in supported_formats:
            return jsonify({
                'success': False,
                'message': f'不支持的格式。支持的格式：{", ".join(supported_formats)}'
            }), 400
        
        if source_format == target_format:
            return jsonify({
                'success': False,
                'message': '源格式和目标格式不能相同'
            }), 400
        
        # 检查文件扩展名
        if not allowed_file(file.filename, source_format):
            return jsonify({
                'success': False,
                'message': f'不支持的文件类型。{source_format}格式支持的扩展名：{", ".join(ALLOWED_EXTENSIONS[source_format])}'
            }), 400
        
        # 检查文件大小
        file.seek(0, 2)  # 移到文件末尾
        file_size = file.tell()
        file.seek(0)     # 回到文件开头
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'success': False,
                'message': f'文件过大。最大支持 {get_file_size_str(MAX_FILE_SIZE)}'
            }), 400
        
        # 生成唯一的文件名
        unique_id = str(uuid.uuid4())
        secure_name = secure_filename(file.filename)
        base_name = os.path.splitext(secure_name)[0]
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        
        try:
            # 保存上传的文件
            source_ext = ALLOWED_EXTENSIONS[source_format][0]
            if source_format == 'docx':
                source_ext = '.docx'
            
            input_path = os.path.join(temp_dir, f"{unique_id}_input{source_ext}")
            file.save(input_path)
            
            # 设置输出文件路径
            target_ext = ALLOWED_EXTENSIONS[target_format][0]
            if target_format == 'docx':
                target_ext = '.docx'
            
            output_filename = f"{base_name}_converted{target_ext}"
            output_path = os.path.join(temp_dir, output_filename)
            
            logger.info(f"开始转换文档: {source_format} -> {target_format}")
            logger.info(f"输入文件: {input_path}")
            logger.info(f"输出文件: {output_path}")
            
            # 使用pypandoc进行转换
            if source_format == 'markdown' and target_format == 'docx':
                # Markdown 转 Word
                pypandoc.convert_file(
                    input_path,
                    'docx',
                    outputfile=output_path,
                    extra_args=['--standalone']
                )
            elif source_format == 'docx' and target_format == 'markdown':
                # Word 转 Markdown
                pypandoc.convert_file(
                    input_path,
                    'markdown',
                    outputfile=output_path,
                    extra_args=['--standalone']
                )
            
            # 检查输出文件是否生成成功
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                return jsonify({
                    'success': False,
                    'message': '文档转换失败，请检查文件格式是否正确'
                }), 500
            
            logger.info(f"文档转换成功: {output_filename}")
            
            # 返回转换后的文件
            return send_file(
                output_path,
                as_attachment=True,
                download_name=output_filename,
                mimetype='application/octet-stream'
            )
        
        except Exception as e:
            logger.error(f"💥 文档转换过程中出错: {str(e)}")
            logger.error(f"📝 异常详情: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"📊 异常堆栈:\n{traceback.format_exc()}")
            return jsonify({
                'success': False,
                'message': f'文档转换失败: {str(e)}'
            }), 500
        
        finally:
            # 清理临时文件
            try:
                if os.path.exists(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {str(e)}")
    
    except Exception as e:
        logger.error(f"🔥 处理文档转换请求时出现未捕获异常: {str(e)}")
        import traceback
        logger.error(f"📊 顶级异常堆栈:\n{traceback.format_exc()}")
        return jsonify({
            'success': False,
            'message': f'服务器内部错误: {str(e)}'
        }), 500

@bp.route('/supported-formats', methods=['GET'])
def get_supported_formats():
    """
    获取支持的文档格式
    """
    return jsonify({
        'success': True,
        'formats': {
            'markdown': {
                'name': 'Markdown',
                'extensions': ALLOWED_EXTENSIONS['markdown'],
                'description': 'Markdown文档格式'
            },
            'docx': {
                'name': 'Microsoft Word',
                'extensions': ALLOWED_EXTENSIONS['docx'],
                'description': 'Microsoft Word文档格式'
            }
        },
        'max_file_size': MAX_FILE_SIZE,
        'max_file_size_str': get_file_size_str(MAX_FILE_SIZE)
    })

@bp.route('/health', methods=['GET'])
def health_check():
    """
    健康检查接口
    """
    try:
        # 检查pandoc是否可用
        version = pypandoc.get_pandoc_version()
        return jsonify({
            'success': True,
            'message': '文档转换服务正常',
            'pandoc_version': version,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Pandoc不可用: {str(e)}'
        }), 500