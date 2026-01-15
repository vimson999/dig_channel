import os
import re
from werkzeug.datastructures import FileStorage
from flask import current_app

def allowed_file(filename: str) -> bool:
    """
    Check if the file extension is allowed based on app configuration.
    """
    # Fallback if config is missing (for safety)
    allowed = current_app.config.get('ALLOWED_EXTENSIONS', {'pdf'})
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed

def safe_filename(filename: str) -> str:
    """
    Custom secure_filename that preserves non-ASCII characters (like Chinese).
    Removes directory traversal sequences and unsafe characters for file systems.
    """
    if not filename:
        return "unnamed_file"

    # 1. Get the basename to avoid directory traversal
    filename = os.path.basename(filename)
    
    # 2. Remove path separators
    filename = filename.replace(os.path.sep, '_')
    if os.path.altsep:
        filename = filename.replace(os.path.altsep, '_')
        
    # 3. Strip whitespace
    filename = filename.strip(' .')
    
    # Fallback
    if not filename:
        filename = "unnamed_file"
        
    return filename

def get_unique_filepath(directory: str, filename: str) -> str:
    """
    Generates a unique filepath to avoid overwriting existing files.
    (Optional future enhancement, currently just returns path)
    """
    return os.path.join(directory, filename)
