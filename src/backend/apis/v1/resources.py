import os
import time
from flask import Blueprint, request, jsonify, send_from_directory, make_response
from werkzeug.utils import secure_filename
from pathlib import Path
from services.asr_service import asr_service

bp = Blueprint('resources', __name__, url_prefix='/api/v1/resources')

# --- 核心路径定义 (遵循 GEMINI.md 规范) ---
# apis/v1/resources.py -> v1 -> apis -> backend -> src -> project_root (5 levels)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
UPLOAD_FOLDER = os.path.abspath(os.path.join(PROJECT_ROOT, 'data', 'uploads'))

# 确保文件夹存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a', 'webp'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bp.route('/upload', methods=['POST'])
def upload_file():
    """
    上传文件并返回其相对于项目根目录的路径。
    用于“一键发布台”素材预上传。
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # 添加时间戳避免覆盖
        timestamp = int(time.time() * 1000) # 毫秒级精度
        name, ext = os.path.splitext(filename)
        final_filename = f"{name}_{timestamp}{ext}"
        
        save_path = os.path.join(UPLOAD_FOLDER, final_filename)
        file.save(save_path)
        
        # 返回相对于项目根目录的路径 (标准做法)
        rel_path = os.path.relpath(save_path, PROJECT_ROOT)
        
        return jsonify({
            'success': True,
            'message': 'File uploaded successfully',
            'path': rel_path,
            'filename': final_filename
        })
        
    return jsonify({'error': 'File type not allowed'}), 400

@bp.route('/list', methods=['GET'])
def list_resources():
    """
    列出 data/uploads 和 data/input 中的最近文件。
    """
    files = []
    project_root_path = Path(PROJECT_ROOT)
    
    # 定义要扫描的目录
    scan_dirs = [
        ('data/input', 'input'),
        ('data/uploads', 'upload')
    ]
    
    for dir_rel_path, category in scan_dirs:
        p = project_root_path / dir_rel_path
        if not p.exists(): continue
        
        # 排除隐藏文件，按修改时间倒序
        items = [f for f in p.iterdir() if f.is_file() and not f.name.startswith('.')]
        for item in sorted(items, key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
            files.append({
                'name': item.name,
                'path': str(item.relative_to(project_root_path)),
                'type': category,
                'mtime': item.stat().st_mtime
            })
    
    return jsonify({
        'success': True,
        'data': files
    })

@bp.route('/view', methods=['GET'])
def view_resource():
    """
    通过路径查看/下载资源。
    """
    file_path = request.args.get('path')
    if not file_path:
        return "Path is required", 400
        
    # 路径解析：始终相对于 PROJECT_ROOT
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
    
    # 安全检查：防止目录穿越
    if not abs_path.startswith(os.path.abspath(PROJECT_ROOT)):
        return "Access denied", 403
        
    if not os.path.exists(abs_path):
        return f"File not found: {file_path}", 404
        
    directory = os.path.dirname(abs_path)
    filename = os.path.basename(abs_path)
    
    response = make_response(send_from_directory(directory, filename))
    # 核心：允许 Canvas 跨域加载预览
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

@bp.route('/transcribe', methods=['GET', 'POST'])
def transcribe_audio_api():
    """
    使用本地 Whisper 模型转录音频文件为文字。
    """
    data = request.json if request.is_json else {}
    file_path = request.args.get('path') or data.get('path')
    
    if not file_path:
        return jsonify({'success': False, 'error': 'Path is required'}), 400
        
    # 路径解析：始终相对于 PROJECT_ROOT
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
    
    # 安全检查：防止目录穿越
    if not abs_path.startswith(os.path.abspath(PROJECT_ROOT)):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
        
    if not os.path.exists(abs_path):
        return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404
        
    try:
        text = asr_service.transcribe(abs_path)
        return jsonify({
            'success': True,
            'text': text
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

