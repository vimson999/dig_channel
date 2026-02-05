from flask import Blueprint, request, jsonify, send_file
from services.thumbnail_service import thumbnail_service
import logging
import os

bp = Blueprint('thumbnail', __name__, url_prefix='/api/v1/thumbnail')
logger = logging.getLogger(__name__)

@bp.route('/generate', methods=['POST'])
def generate_thumbnail():
    """
    Generate a thumbnail image based on parameters.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON body'}), 400
            
        main_title = data.get('main_title')
        if not main_title:
            return jsonify({'error': 'main_title is required'}), 400
            
        # Optional params
        sub_title = data.get('sub_title', "")
        date_text = data.get('date_text', "")
        tag_text = data.get('tag_text', "")
        bg_preset_index = int(data.get('bg_preset_index', 0))
        layout = data.get('layout', 'left')
        
        # Generate
        output_path = thumbnail_service.generate_thumbnail(
            main_title=main_title,
            sub_title=sub_title,
            date_text=date_text,
            tag_text=tag_text,
            bg_preset_index=bg_preset_index,
            layout=layout
        )
        
        filename = os.path.basename(output_path)
        download_url = f"/api/v1/thumbnail/download/{filename}"
        
        return jsonify({
            'success': True,
            'message': 'Thumbnail generated successfully',
            'download_url': download_url,
            'filename': filename
        })

    except Exception as e:
        logger.error(f"Thumbnail generation API error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/download/<filename>', methods=['GET'])
def download_thumbnail(filename):
    """Serve the generated thumbnail file."""
    try:
        file_path = os.path.join(thumbnail_service.output_dir, filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
            
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/presets', methods=['GET'])
def get_presets():
    """Return available presets and backgrounds."""
    from services.thumbnail_service import PRESETS, BACKGROUNDS, ORGANIZATIONS
    return jsonify({
        'presets': PRESETS,
        'backgrounds': BACKGROUNDS,
        'organizations': ORGANIZATIONS
    })
