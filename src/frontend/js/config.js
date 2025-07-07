// 配置文件
const CONFIG = {
    // API基础URL
    API_BASE_URL: 'http://localhost:5001/api/v1',
    
    // API端点
    ENDPOINTS: {
        // 频道相关
        CHANNELS: '/channels',
        CHANNEL_PREVIEW: '/channels/preview',
        CHANNEL_SYNC: '/channels/sync/all',
        CHANNEL_CATEGORIES: '/channels/categories',
        CHANNEL_SEARCH: '/channels/search',
        
        // 排行榜相关
        RANKINGS_ALL: '/rankings/all',
        RANKINGS_DAILY_REVENUE: '/rankings/daily-revenue',
        RANKINGS_MONTHLY_REVENUE: '/rankings/monthly-revenue',
        RANKINGS_DAILY_SUBSCRIBER: '/rankings/daily-subscriber',
        RANKINGS_MONTHLY_SUBSCRIBER: '/rankings/monthly-subscriber',
        RANKINGS_TYPES: '/rankings/types',
        
        // 数据相关
        DATA_TABLE: '/data/table',
        DATA_CHART: '/data/chart-data',
        DATA_SUMMARY: '/data/summary',
        DATA_EXPORT: '/data/export',
        DATA_CHART_TYPES: '/data/chart-types'
    },
    
    // 分页配置
    PAGINATION: {
        DEFAULT_PAGE_SIZE: 10,
        MAX_PAGE_SIZE: 100,
        SHOW_PAGE_SIZE_OPTIONS: [5, 10, 20, 50]
    },
    
    // 图表配置
    CHARTS: {
        DEFAULT_TIME_RANGE: 30, // 天数
        COLORS: {
            PRIMARY: '#0d6efd',
            SUCCESS: '#198754',
            INFO: '#0dcaf0',
            WARNING: '#ffc107',
            DANGER: '#dc3545',
            SECONDARY: '#6c757d'
        },
        // Chart.js 默认配置
        DEFAULT_OPTIONS: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                }
            },
            scales: {
                x: {
                    display: true,
                    grid: {
                        display: false
                    }
                },
                y: {
                    display: true,
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.1)'
                    }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    },
    
    // 数据刷新间隔
    REFRESH_INTERVALS: {
        DASHBOARD: 5 * 60 * 1000, // 5分钟
        RANKINGS: 10 * 60 * 1000, // 10分钟
        CHANNELS: 30 * 60 * 1000  // 30分钟
    },
    
    // 本地存储键名
    STORAGE_KEYS: {
        LAST_REFRESH: 'last_refresh',
        USER_PREFERENCES: 'user_preferences',
        SELECTED_CHANNELS: 'selected_channels',
        TABLE_FILTERS: 'table_filters'
    },
    
    // 错误消息
    ERROR_MESSAGES: {
        NETWORK_ERROR: '网络连接错误，请检查网络连接',
        SERVER_ERROR: '服务器错误，请稍后重试',
        VALIDATION_ERROR: '数据验证失败，请检查输入',
        NOT_FOUND: '请求的资源不存在',
        UNKNOWN_ERROR: '未知错误，请联系管理员'
    },
    
    // 成功消息
    SUCCESS_MESSAGES: {
        CHANNEL_ADDED: '频道添加成功',
        CHANNEL_UPDATED: '频道更新成功',
        CHANNEL_DELETED: '频道删除成功',
        DATA_SYNCED: '数据同步成功',
        DATA_EXPORTED: '数据导出成功'
    },
    
    // UI配置
    UI: {
        LOADING_DELAY: 300, // 显示加载状态的延迟（毫秒）
        TOAST_DURATION: 3000, // 提示消息显示时间（毫秒）
        ANIMATION_DURATION: 300 // 动画持续时间（毫秒）
    },
    
    // 表格配置
    TABLE: {
        MAX_FROZEN_COLUMNS: 3,
        DEFAULT_SORT_ORDER: 'desc',
        EMPTY_CELL_TEXT: '-',
        DATE_FORMAT: 'YYYY-MM-DD'
    },
    
    // 验证规则
    VALIDATION: {
        YOUTUBE_URL_PATTERNS: [
            /^https?:\/\/(www\.)?youtube\.com\/channel\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/c\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/user\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/@[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/watch\?v=[a-zA-Z0-9_-]+/
        ],
        MAX_CHANNEL_NAME_LENGTH: 100,
        MAX_CATEGORY_LENGTH: 50,
        MIN_CPM_VALUE: 0,
        MAX_CPM_VALUE: 100
    },
    
    // 数值格式化
    FORMATTERS: {
        CURRENCY: {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2
        },
        NUMBER: {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        },
        PERCENTAGE: {
            style: 'percent',
            minimumFractionDigits: 1,
            maximumFractionDigits: 1
        }
    }
};

// 全局错误处理
window.addEventListener('error', function(e) {
    // 忽略null错误和Bootstrap内部错误
    if (e.error === null || 
        (e.error && e.error.message && e.error.message.includes('Illegal invocation')) ||
        (e.filename && e.filename.includes('bootstrap'))) {
        return;
    }
    console.error('全局错误:', e.error);
});

window.addEventListener('unhandledrejection', function(e) {
    // 忽略null的Promise拒绝
    if (e.reason === null) {
        return;
    }
    console.error('未处理的Promise拒绝:', e.reason);
});

// 导出配置
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { CONFIG, Utils };
}