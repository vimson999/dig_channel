import os
import time
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from pathlib import Path

bp = Blueprint('resources', __name__, url_prefix='/api/v1/resources')

UPLOAD_FOLDER = os.path.abspath(os.path.join(os.getcwd(), 'data', 'uploads'))
# Ensure folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bp.route('/upload', methods=['POST'])
def upload_file():
    """
    Upload a file and return its server-side path.
    Used for 'One-Click Publisher' pre-upload.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Add timestamp to avoid overwrite
        timestamp = int(time.time())
        name, ext = os.path.splitext(filename)
        final_filename = f"{name}_{timestamp}{ext}"
        
        save_path = os.path.join(UPLOAD_FOLDER, final_filename)
        file.save(save_path)
        
        # Return relative path for display, absolute for backend usage?
        # Let's return relative to project root for clarity
        project_root = os.getcwd()
        rel_path = os.path.relpath(save_path, project_root)
        
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
    List recent files in data/uploads and data/input.
    """
    files = []
    
    # Helper to scan dir
    def scan_dir(path_str, category):
        p = Path(path_str)
        if not p.exists(): return
        # Sort by mtime desc
        for item in sorted(p.iterdir(), key=os.path.getmtime, reverse=True)[:20]:
            if item.is_file() and item.name != '.gitkeep':
                files.append({
                    'name': item.name,
                    'path': str(item.relative_to(Path.cwd())),
                    'type': category,
                    'mtime': item.stat().st_mtime
                })

    scan_dir('data/input', 'input')
    scan_dir('data/uploads', 'upload')
    
    return jsonify({'data': files})
