"""
配置管理中心

支持多种配置源：
- 环境变量
- .env 文件  
- YAML配置文件
- JSON配置文件
- Redis配置中心(可选)
"""

import os
import json
from typing import Any, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False



class ConfigManager:
    """统一配置管理器"""
    
    def __init__(self, config_dir: Optional[str] = None):
        """
        初始化配置管理器
        
        Args:
            config_dir: 配置文件目录，默认为项目根目录
        """
        # 项目根目录
        self.project_root = Path(__file__).parent.parent.parent.parent
        self.config_dir = Path(config_dir) if config_dir else self.project_root
        
        # 配置优先级：环境变量 > .env文件 > YAML文件 > JSON文件 > 默认值
        self._config_cache = {}
        self._load_configurations()
    
    def _load_configurations(self):
        """加载所有配置源"""
        # 1. 加载默认配置
        self._load_defaults()
        
        # 2. 加载JSON配置文件
        self._load_json_config()
        
        # 3. 加载YAML配置文件  
        self._load_yaml_config()
        
        # 4. 加载.env文件
        self._load_env_file()
        
        # 5. 环境变量会自动覆盖（os.environ优先级最高）
    
    def _load_defaults(self):
        """加载默认配置"""
        self._config_cache.update({
            # 应用基础配置
            'SECRET_KEY': 'dev-secret-key-change-in-production',
            'FLASK_ENV': 'development',
            'DEBUG': False,
            
            # 数据库配置
            'USE_POSTGRESQL': False,
            'POSTGRES_HOST': 'localhost',
            'POSTGRES_PORT': 5432,
            'POSTGRES_DB': 'youtube_analytics',
            'POSTGRES_USER': 'postgres',
            'POSTGRES_PASSWORD': '',
            
            # Redis配置
            'REDIS_HOST': 'localhost',
            'REDIS_PORT': 6379,
            'REDIS_DB': 0,
            
            # JWT配置
            'JWT_ACCESS_TOKEN_EXPIRES': 3600,
            
            # 管理员配置
            'ADMIN_USERNAME': 'admin',
            'ADMIN_EMAIL': 'admin@example.com',
            'ADMIN_PASSWORD': 'admin123',
            'ADMIN_DISPLAY_NAME': '系统管理员',
            
            # 日志配置
            'LOG_LEVEL': 'INFO',
        })
    
    def _load_json_config(self):
        """加载JSON配置文件"""
        config_files = [
            self.config_dir / 'config.json',
            self.config_dir / f'config.{self.get_env()}.json'
        ]
        
        for config_file in config_files:
            if config_file.exists():
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                        self._config_cache.update(config_data)
                        print(f"📄 已加载JSON配置: {config_file}")
                except Exception as e:
                    print(f"❌ 加载JSON配置失败 {config_file}: {e}")
    
    def _load_yaml_config(self):
        """加载YAML配置文件"""
        if not HAS_YAML:
            return

        config_files = [
            self.config_dir / 'config.yaml',
            self.config_dir / 'config.yml',
            self.config_dir / f'config.{self.get_env()}.yaml',
            self.config_dir / f'config.{self.get_env()}.yml'
        ]
        
        for config_file in config_files:
            if config_file.exists():
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config_data = yaml.safe_load(f)
                        if config_data:
                            self._config_cache.update(config_data)
                            print(f"📄 已加载YAML配置: {config_file}")
                except Exception as e:
                    print(f"❌ 加载YAML配置失败 {config_file}: {e}")
    
    def _load_env_file(self):
        """加载.env文件"""
        env_files = [
            self.config_dir / '.env.local',
            self.config_dir / f'.env.{self.get_env()}',
            self.config_dir / '.env'
        ]
        
        for env_file in env_files:
            if env_file.exists():
                load_dotenv(env_file, override=False)  # 不覆盖已存在的环境变量
                print(f"📄 已加载ENV配置: {env_file}")
                break
    
    def get_env(self) -> str:
        """获取当前环境"""
        return os.environ.get('FLASK_ENV', 'development')
    
    def get(self, key: str, default: Any = None, cast_type: type = str) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键
            default: 默认值
            cast_type: 类型转换
            
        Returns:
            配置值
        """
        # 优先级：环境变量 > 配置缓存 > 默认值
        value = os.environ.get(key, self._config_cache.get(key, default))
        
        if value is None:
            return default
        
        # 类型转换
        if cast_type == bool:
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return bool(value)
        elif cast_type == int:
            return int(value)
        elif cast_type == float:
            return float(value)
        elif cast_type == list:
            if isinstance(value, str):
                return [item.strip() for item in value.split(',') if item.strip()]
            return value if isinstance(value, list) else [value]
        
        return value
    
    def get_database_uri(self) -> str:
        """获取数据库连接URI"""
        if self.get('USE_POSTGRESQL', cast_type=bool):
            # PostgreSQL
            host = self.get('POSTGRES_HOST')
            port = self.get('POSTGRES_PORT', cast_type=int)
            db = self.get('POSTGRES_DB')
            user = self.get('POSTGRES_USER')
            password = self.get('POSTGRES_PASSWORD')
            return f"postgresql://{user}:{password}@{host}:{port}/{db}"
        else:
            # SQLite
            db_path = self.project_root / 'instance' / 'youtube_tracker.db'
            return f"sqlite:///{db_path}"
    
    def get_redis_uri(self) -> str:
        """获取Redis连接URI"""
        host = self.get('REDIS_HOST')
        port = self.get('REDIS_PORT', cast_type=int)
        db = self.get('REDIS_DB', cast_type=int)
        return f"redis://{host}:{port}/{db}"
    
    def get_admin_config(self) -> Dict[str, str]:
        """获取管理员配置"""
        return {
            'username': self.get('ADMIN_USERNAME'),
            'email': self.get('ADMIN_EMAIL'),
            'password': self.get('ADMIN_PASSWORD'),
            'display_name': self.get('ADMIN_DISPLAY_NAME')
        }
    
    def validate_required_configs(self) -> bool:
        """验证必需的配置"""
        required_configs = []
        
        if self.get('USE_POSTGRESQL', cast_type=bool):
            required_configs.extend([
                'POSTGRES_HOST', 'POSTGRES_DB', 
                'POSTGRES_USER', 'POSTGRES_PASSWORD'
            ])
        
        missing_configs = []
        for config in required_configs:
            if not self.get(config):
                missing_configs.append(config)
        
        if missing_configs:
            print(f"❌ 缺少必需配置: {', '.join(missing_configs)}")
            return False
        
        return True
    
    def print_config_summary(self):
        """打印配置摘要"""
        print("🔧 配置摘要:")
        print(f"  环境: {self.get_env()}")
        print(f"  数据库: {'PostgreSQL' if self.get('USE_POSTGRESQL', cast_type=bool) else 'SQLite'}")
        print(f"  Redis: {self.get('REDIS_HOST')}:{self.get('REDIS_PORT')}")
        print(f"  日志级别: {self.get('LOG_LEVEL')}")
        print(f"  管理员: {self.get('ADMIN_USERNAME')}")


# 全局配置实例
config = ConfigManager()

# 兼容性函数
def get_config(key: str, default: Any = None, cast_type: type = str) -> Any:
    """获取配置的便捷函数"""
    return config.get(key, default, cast_type)