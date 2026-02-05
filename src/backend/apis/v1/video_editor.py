import os
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from services.video_editor import video_editor_service
import logging

bp = Blueprint('video_editor', __name__, url_prefix='/api/v1/video-editor')
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bp.route('/synthesize', methods=['POST'])
def synthesize_video():
    """
    Synthesize video from Video A + [Video C + Audio C](Optional) + Image + Audio.
    Expects multipart/form-data with 'video', 'image', 'audio' files, and optional 'video_c', 'audio_c'.
    """
    try:
        # 1. Validation
        if 'video' not in request.files or 'image' not in request.files or 'audio' not in request.files:
            return jsonify({'error': 'Missing required files (video, image, audio)'}), 400
            
        video_file = request.files['video']
        image_file = request.files['image']
        audio_file = request.files['audio']
        
        # Optional files for C
        video_c_file = request.files.get('video_c')
        audio_c_file = request.files.get('audio_c')
        
        if video_file.filename == '' or image_file.filename == '' or audio_file.filename == '':
            return jsonify({'error': 'No selected file for required fields'}), 400

        audio_start = float(request.form.get('audio_start', 7.0))
        audio_end_offset = float(request.form.get('audio_end_offset', 0.0))
        custom_filename = request.form.get('custom_filename')
        
        # Sanitize custom filename (simple version to allow Chinese)
        if custom_filename:
            custom_filename = custom_filename.strip().replace('/', '').replace('\\', '')
            if not custom_filename:
                custom_filename = None

        # 2. Save uploaded files
        upload_dir = video_editor_service.upload_folder
        
        video_path = os.path.join(upload_dir, secure_filename(video_file.filename))
        image_path = os.path.join(upload_dir, secure_filename(image_file.filename))
        audio_path = os.path.join(upload_dir, secure_filename(audio_file.filename))
        
        video_file.save(video_path)
        image_file.save(image_path)
        audio_file.save(audio_path)
        
        # Handle C files
        video_c_path = None
        audio_c_path = None
        
        if video_c_file and video_c_file.filename:
            video_c_path = os.path.join(upload_dir, secure_filename(video_c_file.filename))
            video_c_file.save(video_c_path)
            
            # Audio is optional for C
            if audio_c_file and audio_c_file.filename:
                audio_c_path = os.path.join(upload_dir, secure_filename(audio_c_file.filename))
                audio_c_file.save(audio_c_path)
        
        # 3. Process video
        output_path = video_editor_service.process_mixed_content(
            video_path, image_path, audio_path, 
            video_c_path=video_c_path, audio_c_path=audio_c_path,
            audio_start=audio_start, 
            audio_end_offset=audio_end_offset, output_filename=custom_filename
        )
        
        # 4. Return result (URL to download)
        # Note: In production, upload to S3/OSS. Here we return a local download link.
        filename = os.path.basename(output_path)
        download_url = f"/api/v1/video-editor/download/{filename}"
        
        return jsonify({
            'success': True,
            'message': 'Video synthesized successfully',
            'download_url': download_url,
            'filename': filename
        })

    except Exception as e:
        logger.error(f"Synthesize API error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    """Serve the generated video file."""
    try:
        return send_file(
            os.path.join(os.getcwd(), video_editor_service.output_folder, filename),
            as_attachment=True
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 404
