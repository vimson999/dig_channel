from flask import Blueprint, request, send_file, jsonify, current_app, url_for
import os
import logging
from services.watermark.pdf_processor import PDFProcessor
from services.watermark.utils import allowed_file, safe_filename

# Define Blueprint
# No template folder needed for API mode
watermark_bp = Blueprint('watermark', __name__, url_prefix='/watermark')

logger = logging.getLogger(__name__)

@watermark_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'files' not in request.files:
        return jsonify({'success': False, 'message': 'No file part'}), 400
    
    files = request.files.getlist('files')
    processed_files = []
    errors = []
    
    # Log the start of a batch upload
    valid_files_count = len([f for f in files if f.filename])
    logger.info(f"BATCH START: Received upload request with {valid_files_count} file(s).")
    
    # In future versions, threshold could be a request parameter
    processor = PDFProcessor(threshold=200)

    # Use instance path or configured path
    # Default to instance/uploads if not set
    upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(current_app.instance_path, 'uploads'))
    download_folder = current_app.config.get('DOWNLOAD_FOLDER', os.path.join(current_app.instance_path, 'downloads'))
    
    os.makedirs(upload_folder, exist_ok=True)
    os.makedirs(download_folder, exist_ok=True)

    for file in files:
        if file.filename == '' or not file:
            continue
        
        if not allowed_file(file.filename):
            logger.warning(f"SKIPPED: {file.filename} - Invalid extension.")
            errors.append(f"{file.filename}: Invalid extension (only .pdf allowed)")
            continue

        filename = safe_filename(file.filename)
        input_path = os.path.join(upload_folder, filename)
        
        try:
            file.save(input_path)
            logger.info(f"UPLOADED: {filename} saved successfully. Starting processing...")
        except Exception as e:
            logger.error(f"UPLOAD ERROR: Failed to save file {filename}: {e}")
            errors.append(f"{filename}: Save failed - {str(e)}")
            continue
        
        # Prepare output filename
        base_name = os.path.splitext(filename)[0]
        output_filename = f"{base_name}_cleaned.pdf"
        output_path = os.path.join(download_folder, output_filename)
        
        # Call Service
        success, message = processor.remove_watermark(input_path, output_path)
        
        if success:
            logger.info(f"SUCCESS: {filename} -> {output_filename}. Processing complete.")
            # Construct API URL for download
            # We use _external=True to get absolute URL if needed, but relative is usually fine for API
            download_url = url_for('watermark.download', filename=output_filename)
            preview_url = url_for('watermark.download', filename=output_filename, preview='true')
            processed_files.append({
                'name': output_filename,
                'original_name': file.filename,
                'download_url': download_url,
                'preview_url': preview_url
            })
        else:
            logger.error(f"FAILURE: {filename} processing failed. Reason: {message}")
            errors.append(f"{filename}: Processing failed - {message}")

    logger.info(f"BATCH END: Processed {len(processed_files)}/{valid_files_count} files successfully.")

    if not processed_files and errors:
        return jsonify({
            'success': False,
            'message': 'No files were processed successfully.',
            'errors': errors
        }), 400

    return jsonify({
        'success': True,
        'files': processed_files,
        'errors': errors
    })

@watermark_bp.route('/download/<filename>')
def download(filename):
    # Security Check: Prevent Path Traversal
    filename = safe_filename(filename)
    download_folder = current_app.config.get('DOWNLOAD_FOLDER', os.path.join(current_app.instance_path, 'downloads'))
    path = os.path.join(download_folder, filename)
    
    # Check for preview mode
    is_preview = request.args.get('preview') == 'true'
    
    if os.path.exists(path):
        return send_file(path, as_attachment=not is_preview)
    else:
        return jsonify({'success': False, 'message': 'File not found'}), 404