"""
MHTML 转 PDF API 端点
提供 MHTML 文件转换为 PDF 的 Web API
"""

import os
import tempfile
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from flask_cors import cross_origin
from werkzeug.utils import secure_filename
import logging

from utils.colored_logger import setup_colored_logger
from utils.auth_utils import require_login
from utils.api_utils import handle_api_error
from services.mhtml_converter import converter

logger = setup_colored_logger("MHTML_PDF_API")

# 创建蓝图
mhtml_pdf_bp = Blueprint('mhtml_pdf', __name__, url_prefix='/api/v1/mhtml-pdf')

# 支持的文件扩展名
ALLOWED_EXTENSIONS = {'mhtml', 'mht'}

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@mhtml_pdf_bp.route('/convert', methods=['POST'])
@cross_origin()
@require_login
def convert_mhtml_to_pdf(current_user):
    """转换 MHTML 文件为 PDF"""
    try:
        # 检查文件是否存在
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': '没有上传文件',
                'data': {}
            }), 400
        
        file = request.files['file']
        
        # 检查文件名
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '没有选择文件',
                'data': {}
            }), 400
        
        # 检查文件类型
        if not allowed_file(file.filename):
            return jsonify({
                'success': False,
                'message': '只支持 .mhtml 和 .mht 文件格式',
                'data': {}
            }), 400
        
        # 获取转换选项
        options = {}
        if request.form.get('format'):
            options['format'] = request.form.get('format')
        if request.form.get('margin_top'):
            options['margin_top'] = request.form.get('margin_top')
        if request.form.get('margin_right'):
            options['margin_right'] = request.form.get('margin_right')
        if request.form.get('margin_bottom'):
            options['margin_bottom'] = request.form.get('margin_bottom')
        if request.form.get('margin_left'):
            options['margin_left'] = request.form.get('margin_left')
        
        logger.info(f"🔄 开始转换 MHTML: 用户={current_user.username}, 文件={file.filename}")
        
        # 保存上传的文件到临时位置
        filename = secure_filename(file.filename)
        temp_dir = tempfile.gettempdir()
        input_path = os.path.join(temp_dir, f"mhtml_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}")
        
        file.save(input_path)
        
        try:
            # 验证 MHTML 文件
            if not converter.validate_mhtml(input_path):
                logger.warning(f"⚠️ 可能不是有效的 MHTML 文件: {filename}")
                # 不中断转换，让Chrome尝试处理
            
            # 执行转换
            output_path = converter.convert(input_path, options=options)
            
            # 获取文件信息
            file_size = os.path.getsize(output_path)
            pdf_filename = os.path.basename(output_path)
            
            logger.info(f"✅ MHTML 转换成功: {filename} -> {pdf_filename} ({file_size} bytes)")
            
            return jsonify({
                'success': True,
                'message': 'MHTML 转 PDF 成功',
                'data': {
                    'pdf_filename': pdf_filename,
                    'original_filename': filename,
                    'file_size': file_size,
                    'conversion_time': datetime.now().isoformat()
                }
            })
            
        finally:
            # 清理上传的临时文件
            try:
                if os.path.exists(input_path):
                    os.remove(input_path)
            except:
                pass
        
    except Exception as e:
        logger.error(f"❌ MHTML 转换失败: {str(e)}")
        return handle_api_error(500, f"MHTML转换失败: {str(e)}", {'error_type': type(e).__name__})

@mhtml_pdf_bp.route('/download/<filename>', methods=['GET'])
@cross_origin()
@require_login
def download_pdf(current_user, filename):
    """下载转换后的 PDF 文件"""
    try:
        # 安全检查文件名
        safe_filename = secure_filename(filename)
        if not safe_filename or not safe_filename.endswith('.pdf'):
            return jsonify({
                'success': False,
                'message': '无效的文件名',
                'data': {}
            }), 400
        
        # 检查文件是否存在
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, safe_filename)
        
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': 'PDF 文件不存在或已过期',
                'data': {}
            }), 404
        
        logger.info(f"📥 下载 PDF 文件: {safe_filename}, 用户: {current_user.username}")
        
        # 发送文件
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_filename,
            mimetype='application/pdf'
        )
        
    except Exception as e:
        logger.error(f"❌ 下载 PDF 文件失败: {str(e)}")
        return handle_api_error(500, f"下载PDF文件失败: {str(e)}", {'error_type': type(e).__name__})

@mhtml_pdf_bp.route('/batch-convert', methods=['POST'])
@cross_origin()
@require_login
def batch_convert_mhtml_to_pdf(current_user):
    """批量转换 MHTML 文件为 PDF"""
    try:
        # 检查文件
        if 'files' not in request.files:
            return jsonify({
                'success': False,
                'message': '没有上传文件',
                'data': {}
            }), 400
        
        files = request.files.getlist('files')
        
        if not files or len(files) == 0:
            return jsonify({
                'success': False,
                'message': '没有选择文件',
                'data': {}
            }), 400
        
        # 限制批量处理数量
        if len(files) > 10:
            return jsonify({
                'success': False,
                'message': '批量处理最多支持 10 个文件',
                'data': {}
            }), 400
        
        logger.info(f"🔄 开始批量转换 MHTML: 用户={current_user.username}, 文件数={len(files)}")
        
        # 保存所有上传文件
        temp_dir = tempfile.gettempdir()
        input_files = []
        
        try:
            for file in files:
                if file.filename == '' or not allowed_file(file.filename):
                    continue
                
                filename = secure_filename(file.filename)
                input_path = os.path.join(temp_dir, f"batch_mhtml_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}")
                file.save(input_path)
                input_files.append(input_path)
            
            if not input_files:
                return jsonify({
                    'success': False,
                    'message': '没有有效的 MHTML 文件',
                    'data': {}
                }), 400
            
            # 执行批量转换
            results = converter.batch_convert(input_files)
            
            # 整理结果
            converted_files = []
            failed_files = []
            
            for result in results:
                if result['success']:
                    file_size = os.path.getsize(result['output'])
                    converted_files.append({
                        'original_name': os.path.basename(result['input']),
                        'pdf_filename': os.path.basename(result['output']),
                        'file_size': file_size
                    })
                else:
                    failed_files.append({
                        'original_name': os.path.basename(result['input']),
                        'error': result['error']
                    })
            
            logger.info(f"✅ 批量转换完成: 成功 {len(converted_files)} 个, 失败 {len(failed_files)} 个")
            
            return jsonify({
                'success': True,
                'message': f'批量转换完成: 成功 {len(converted_files)} 个, 失败 {len(failed_files)} 个',
                'data': {
                    'converted_files': converted_files,
                    'failed_files': failed_files,
                    'total_processed': len(results),
                    'success_count': len(converted_files),
                    'failed_count': len(failed_files)
                }
            })
            
        finally:
            # 清理上传的临时文件
            for input_path in input_files:
                try:
                    if os.path.exists(input_path):
                        os.remove(input_path)
                except:
                    pass
        
    except Exception as e:
        logger.error(f"❌ 批量 MHTML 转换失败: {str(e)}")
        return handle_api_error(500, f"批量MHTML转换失败: {str(e)}", {'error_type': type(e).__name__})

@mhtml_pdf_bp.route('/cleanup', methods=['POST'])
@cross_origin()
@require_login
def cleanup_temp_files(current_user):
    """清理临时 PDF 文件"""
    try:
        max_age_hours = int(request.json.get('max_age_hours', 24)) if request.json else 24
        
        cleaned_count = converter.cleanup_temp_files(max_age_hours)
        
        logger.info(f"🧹 临时文件清理: 用户={current_user.username}, 清理了 {cleaned_count} 个文件")
        
        return jsonify({
            'success': True,
            'message': f'清理完成，删除了 {cleaned_count} 个过期 PDF 文件',
            'data': {
                'cleaned_count': cleaned_count,
                'max_age_hours': max_age_hours
            }
        })
        
    except Exception as e:
        logger.error(f"❌ 清理临时文件失败: {str(e)}")
        return handle_api_error(500, f"清理临时文件失败: {str(e)}", {'error_type': type(e).__name__})

@mhtml_pdf_bp.route('/status', methods=['GET'])
@cross_origin()
def get_converter_status():
    """获取转换器状态"""
    try:
        # 检查 Chrome 是否可用
        chrome_available = True
        chrome_path = None
        error_message = None
        
        try:
            chrome_path = converter.chrome_path
        except Exception as e:
            chrome_available = False
            error_message = str(e)
        
        # 统计临时目录中的 PDF 文件
        temp_dir = tempfile.gettempdir()
        pdf_count = 0
        try:
            pdf_count = len([f for f in os.listdir(temp_dir) 
                           if f.startswith('pdf_') and f.endswith('.pdf')])
        except:
            pass
        
        status = {
            'chrome_available': chrome_available,
            'chrome_path': chrome_path,
            'temp_pdf_files': pdf_count,
            'supported_formats': list(ALLOWED_EXTENSIONS),
            'error_message': error_message
        }
        
        return jsonify({
            'success': True,
            'message': 'MHTML 转 PDF 服务状态获取成功',
            'data': status
        })
        
    except Exception as e:
        logger.error(f"❌ 获取转换器状态失败: {str(e)}")
        return handle_api_error(500, f"获取转换器状态失败: {str(e)}", {'error_type': type(e).__name__})