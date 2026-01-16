from flask import Blueprint
from .gemini import gemini_bp

api_bp = Blueprint('api', __name__)

api_bp.register_blueprint(gemini_bp, url_prefix='/gemini')
