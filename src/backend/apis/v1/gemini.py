from flask import Blueprint, request, jsonify
from services.gemini import gemini_service
import logging

bp = Blueprint('gemini', __name__, url_prefix='/api/v1/gemini')
logger = logging.getLogger(__name__)

@bp.route('/generate', methods=['POST'], strict_slashes=False)
def generate():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON body'}), 400
            
        prompt = data.get('prompt')
        model = data.get('model')  # Get model from request
        
        if not prompt:
            return jsonify({'error': 'Prompt is required'}), 400
            
        # Log the request details for debugging
        logger.info(f"Gemini request: model={model}, prompt_len={len(prompt)}")
        
        result = gemini_service.generate_content(prompt, model_name=model)
        return jsonify({'result': result})
        
    except Exception as e:
        logger.error(f"Gemini generation error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/models', methods=['GET'], strict_slashes=False)
def list_models():
    try:
        # Use the configured key from service to list models via new SDK client
        client = gemini_service._get_client()
        
        models = []
        # New SDK method for listing models
        for m in client.models.list():
            # Check supported_actions for 'generateContent'
            if m.supported_actions and 'generateContent' in m.supported_actions:
                models.append({
                    'name': m.name.replace('models/', ''),
                    'displayName': m.display_name, # Note: displayName -> display_name in new SDK
                    'description': m.description
                })
        
        return jsonify({'models': models})
    except Exception as e:
        logger.error(f"Gemini list models error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
