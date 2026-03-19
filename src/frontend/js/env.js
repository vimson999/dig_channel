// 环境配置文件
const ENV_CONFIG = {
    // 开发环境配置
    development: {
        API_BASE_URL: 'http://localhost:5001/api/v1'
    },
    
    // 生产环境配置  
    production: {
        API_BASE_URL: '/api/v1'
    }
};

// 判断当前环境
const isDevelopment = window.location.hostname === 'localhost' || 
                     window.location.hostname === '127.0.0.1' ||
                     window.location.port === '8888' ||
                     window.location.port === '8080';

// 获取当前环境配置
const currentEnv = isDevelopment ? 'development' : 'production';
const API_BASE_URL = ENV_CONFIG[currentEnv].API_BASE_URL;

// 显式挂载到 window 对象，供其他脚本使用
window.API_BASE_URL = API_BASE_URL;
window.CONFIG = {
    API_BASE_URL: API_BASE_URL
};

console.log('🌍 [ENV] 当前环境:', currentEnv);
console.log('🌍 [ENV] API地址:', window.API_BASE_URL);