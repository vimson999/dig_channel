# API模块
from .v1 import register_blueprints as register_v1_blueprints

def register_all_apis(app):
    """注册所有API版本
    
    Args:
        app: Flask应用实例
    """
    # 注册v1 API
    register_v1_blueprints(app)
    
    # 创建API信息端点
    @app.route('/api', methods=['GET'])
    def api_info():
        """API信息"""
        return {
            'success': True,
            'data': {
                'name': 'YouTube Channel Tracker API',
                'version': '1.0.0',
                'versions': ['v1'],
                'endpoints': {
                    'v1': {
                        'base_url': '/api/v1',
                        'auth': '/api/v1/auth',
                        'channels': '/api/v1/channels',
                        'rankings': '/api/v1/rankings', 
                        'data': '/api/v1/data',
                        'videos': '/api/v1/videos'
                    }
                },
                'documentation': '/api/docs'  # TODO: 添加API文档
            }
        }

__all__ = [
    'register_all_apis'
]