// 工具函数集合
/**
 * 通用工具函数类
 * 提供格式化、颜色、日期等常用功能
 */

class Utils {
    // 数字格式化
    static formatNumber(num) {
        if (num === null || num === undefined) return '-';
        
        const number = Number(num);
        if (isNaN(number)) return '-';
        
        // 大于10万的数字用K、M、B等单位
        if (Math.abs(number) >= 1000000000) {
            return (number / 1000000000).toFixed(1) + 'B';
        }
        if (Math.abs(number) >= 1000000) {
            return (number / 1000000).toFixed(1) + 'M';
        }
        if (Math.abs(number) >= 1000) {
            return (number / 1000).toFixed(1) + 'K';
        }
        
        return number.toLocaleString();
    }

    // 货币格式化
    static formatCurrency(amount, currency = 'USD') {
        if (amount === null || amount === undefined) return '$-';
        
        const number = Number(amount);
        if (isNaN(number)) return '$-';
        
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: currency,
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(number);
    }

    // 变化值格式化（带正负号）
    static formatChange(change, showPlus = true) {
        if (change === null || change === undefined) return '-';
        
        const number = Number(change);
        if (isNaN(number)) return '-';
        
        const formatted = this.formatNumber(Math.abs(number));
        
        if (number > 0) {
            return showPlus ? `+${formatted}` : formatted;
        } else if (number < 0) {
            return `-${formatted}`;
        } else {
            return '0';
        }
    }

    // 百分比格式化
    static formatPercentage(percentage, decimals = 1) {
        if (percentage === null || percentage === undefined) return '-';
        
        const number = Number(percentage);
        if (isNaN(number)) return '-';
        
        return number.toFixed(decimals) + '%';
    }

    // 日期格式化
    static formatDate(date, format = 'YYYY-MM-DD') {
        if (!date) return '-';
        
        let dateObj;
        if (typeof date === 'string') {
            dateObj = new Date(date);
        } else {
            dateObj = date;
        }
        
        if (isNaN(dateObj.getTime())) return '-';
        
        const year = dateObj.getFullYear();
        const month = String(dateObj.getMonth() + 1).padStart(2, '0');
        const day = String(dateObj.getDate()).padStart(2, '0');
        const hours = String(dateObj.getHours()).padStart(2, '0');
        const minutes = String(dateObj.getMinutes()).padStart(2, '0');
        
        switch (format) {
            case 'YYYY-MM-DD':
                return `${year}-${month}-${day}`;
            case 'MM-DD':
                return `${month}-${day}`;
            case 'YYYY-MM-DD HH:mm':
                return `${year}-${month}-${day} ${hours}:${minutes}`;
            case 'MM/DD/YYYY':
                return `${month}/${day}/${year}`;
            default:
                return `${year}-${month}-${day}`;
        }
    }

    // 时间差格式化（如：2天前）
    static formatTimeAgo(date) {
        if (!date) return '-';
        
        const dateObj = new Date(date);
        if (isNaN(dateObj.getTime())) return '-';
        
        const now = new Date();
        const diffMs = now.getTime() - dateObj.getTime();
        const diffSecs = Math.floor(diffMs / 1000);
        const diffMins = Math.floor(diffSecs / 60);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);
        
        if (diffDays > 0) {
            return `${diffDays}天前`;
        } else if (diffHours > 0) {
            return `${diffHours}小时前`;
        } else if (diffMins > 0) {
            return `${diffMins}分钟前`;
        } else {
            return '刚刚';
        }
    }

    // 获取颜色（用于图表）
    static getColor(index) {
        const colors = [
            '#0d6efd', // Primary
            '#198754', // Success
            '#dc3545', // Danger
            '#ffc107', // Warning
            '#0dcaf0', // Info
            '#6c757d', // Secondary
            '#6f42c1', // Purple
            '#d63384', // Pink
            '#fd7e14', // Orange
            '#20c997'  // Teal
        ];
        
        return colors[index % colors.length];
    }

    // 深度克隆对象
    static deepClone(obj) {
        if (obj === null || typeof obj !== 'object') {
            return obj;
        }
        
        if (obj instanceof Date) {
            return new Date(obj.getTime());
        }
        
        if (obj instanceof Array) {
            return obj.map(item => this.deepClone(item));
        }
        
        if (typeof obj === 'object') {
            const cloned = {};
            for (const key in obj) {
                if (obj.hasOwnProperty(key)) {
                    cloned[key] = this.deepClone(obj[key]);
                }
            }
            return cloned;
        }
    }

    // 防抖函数
    static debounce(func, wait, immediate = false) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                timeout = null;
                if (!immediate) func.apply(this, args);
            };
            const callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) func.apply(this, args);
        };
    }

    // 节流函数
    static throttle(func, limit) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }

    // 验证URL格式
    static isValidURL(string) {
        try {
            new URL(string);
            return true;
        } catch (_) {
            return false;
        }
    }

    // 验证YouTube URL
    static isYouTubeURL(url) {
        if (!this.isValidURL(url)) return false;
        
        const patterns = [
            /^https?:\/\/(www\.)?youtube\.com\/channel\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/c\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/user\/[a-zA-Z0-9_-]+/,
            /^https?:\/\/(www\.)?youtube\.com\/@[a-zA-Z0-9_-]+/
        ];
        
        return patterns.some(pattern => pattern.test(url));
    }

    // 从URL提取频道ID
    static extractChannelId(url) {
        const patterns = [
            { regex: /\/channel\/([a-zA-Z0-9_-]+)/, type: 'channel' },
            { regex: /\/c\/([a-zA-Z0-9_-]+)/, type: 'custom' },
            { regex: /\/user\/([a-zA-Z0-9_-]+)/, type: 'user' },
            { regex: /\/@([a-zA-Z0-9_-]+)/, type: 'handle' }
        ];
        
        for (const pattern of patterns) {
            const match = url.match(pattern.regex);
            if (match) {
                return {
                    id: match[1],
                    type: pattern.type
                };
            }
        }
        
        return null;
    }

    // 生成随机ID
    static generateId(prefix = '') {
        const random = Math.random().toString(36).substr(2, 9);
        return prefix ? `${prefix}-${random}` : random;
    }

    // 计算频道年龄（从创建到现在）
    static calculateChannelAge(publishedAt) {
        if (!publishedAt) return null;
        
        const published = new Date(publishedAt);
        const now = new Date();
        const diffTime = Math.abs(now - published);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        
        if (diffDays < 30) {
            return `${diffDays}天`;
        } else if (diffDays < 365) {
            const months = Math.floor(diffDays / 30);
            return `${months}个月`;
        } else {
            const years = Math.floor(diffDays / 365);
            const remainingMonths = Math.floor((diffDays % 365) / 30);
            if (remainingMonths > 0) {
                return `${years}年${remainingMonths}个月`;
            } else {
                return `${years}年`;
            }
        }
    }

    // 格式化频道创建时间
    static formatChannelCreatedDate(publishedAt) {
        if (!publishedAt) return '未知';
        
        const date = new Date(publishedAt);
        return date.toLocaleDateString('zh-CN', { 
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
    }

    // 计算视频热度评分
    static calculateHotScore(viewCount = 0, likeCount = 0, commentCount = 0) {
        // 归一化权重：播放量(50%) + 点赞(30%) + 评论(20%)
        // 使用对数函数平滑大数值差异
        const normalizedViews = Math.log10(Math.max(viewCount, 1));
        const normalizedLikes = Math.log10(Math.max(likeCount, 1));
        const normalizedComments = Math.log10(Math.max(commentCount, 1));
        
        // 加权计算，最高分100
        const score = (normalizedViews * 0.5 + normalizedLikes * 0.3 + normalizedComments * 0.2) * 10;
        
        // 限制在0-100范围内
        return Math.min(Math.max(score, 0), 100);
    }

    // 获取热度等级和颜色
    static getHotScoreLevel(score) {
        if (score >= 80) return { level: '🔥 超火', color: 'text-red-500', bgColor: 'bg-red-50' };
        if (score >= 60) return { level: '🌟 很火', color: 'text-orange-500', bgColor: 'bg-orange-50' };
        if (score >= 40) return { level: '📈 中等', color: 'text-yellow-500', bgColor: 'bg-yellow-50' };
        if (score >= 20) return { level: '📊 一般', color: 'text-blue-500', bgColor: 'bg-blue-50' };
        return { level: '💭 冷门', color: 'text-gray-500', bgColor: 'bg-gray-50' };
    }

    // 收藏管理功能
    static getFavorites() {
        const favorites = localStorage.getItem('trending_favorites');
        return favorites ? JSON.parse(favorites) : [];
    }

    static addToFavorites(video) {
        const favorites = this.getFavorites();
        const exists = favorites.find(fav => fav.video_id === video.video_id);
        
        if (!exists) {
            const favoriteItem = {
                ...video,
                favorited_at: new Date().toISOString(),
                id: this.generateId('fav')
            };
            favorites.unshift(favoriteItem); // 添加到开头
            localStorage.setItem('trending_favorites', JSON.stringify(favorites));
            return true;
        }
        return false;
    }

    static removeFromFavorites(videoId) {
        const favorites = this.getFavorites();
        const filtered = favorites.filter(fav => fav.video_id !== videoId);
        localStorage.setItem('trending_favorites', JSON.stringify(filtered));
        return true;
    }

    static isFavorited(videoId) {
        const favorites = this.getFavorites();
        return favorites.some(fav => fav.video_id === videoId);
    }

    static clearAllFavorites() {
        localStorage.removeItem('trending_favorites');
        return true;
    }

    // 获取查询参数
    static getQueryParams() {
        const params = {};
        const urlParams = new URLSearchParams(window.location.search);
        for (const [key, value] of urlParams) {
            params[key] = value;
        }
        return params;
    }

    // 设置查询参数
    static setQueryParam(key, value) {
        const url = new URL(window.location);
        if (value === null || value === undefined || value === '') {
            url.searchParams.delete(key);
        } else {
            url.searchParams.set(key, value);
        }
        window.history.replaceState({}, '', url);
    }

    // 下载文件
    static downloadFile(content, filename, contentType = 'text/plain') {
        const blob = new Blob([content], { type: contentType });
        const url = window.URL.createObjectURL(blob);
        
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        window.URL.revokeObjectURL(url);
    }

    // 复制到剪贴板
    static async copyToClipboard(text) {
        try {
            if (navigator.clipboard) {
                await navigator.clipboard.writeText(text);
                return true;
            } else {
                // 兼容旧浏览器
                const textArea = document.createElement('textarea');
                textArea.value = text;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                return true;
            }
        } catch (error) {
            console.error('复制到剪贴板失败:', error);
            return false;
        }
    }

    // 滚动到元素
    static scrollToElement(element, behavior = 'smooth') {
        if (typeof element === 'string') {
            element = document.querySelector(element);
        }
        
        if (element) {
            element.scrollIntoView({ behavior, block: 'start' });
        }
    }

    // 检查元素是否在视口中
    static isElementInViewport(element) {
        if (typeof element === 'string') {
            element = document.querySelector(element);
        }
        
        if (!element) return false;
        
        const rect = element.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.right <= (window.innerWidth || document.documentElement.clientWidth)
        );
    }

    // 格式化文件大小
    static formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // 等待指定时间
    static sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // 重试函数
    static async retry(fn, maxAttempts = 3, delay = 1000) {
        let lastError;
        
        for (let attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                return await fn();
            } catch (error) {
                lastError = error;
                console.warn(`尝试 ${attempt}/${maxAttempts} 失败:`, error.message);
                
                if (attempt < maxAttempts) {
                    await this.sleep(delay * attempt); // 递增延迟
                }
            }
        }
        
        throw lastError;
    }
}

// 状态栏管理
function showStatusBar(message) {
    const statusBar = document.getElementById('status-bar');
    const statusText = document.getElementById('status-text');
    
    if (statusBar && statusText) {
        statusText.textContent = message;
        statusBar.classList.remove('hidden');
    }
}

function hideStatusBar() {
    const statusBar = document.getElementById('status-bar');
    if (statusBar) {
        statusBar.classList.add('hidden');
    }
}

// Toast 提示管理
function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast bg-white border-l-4 rounded-lg shadow-lg p-4 mb-2 transform transition-all duration-300 translate-x-full`;
    
    // 根据类型设置颜色
    const colors = {
        'success': 'border-green-500 bg-green-50',
        'error': 'border-red-500 bg-red-50',
        'warning': 'border-yellow-500 bg-yellow-50',
        'info': 'border-blue-500 bg-blue-50'
    };
    
    const icons = {
        'success': 'fas fa-check-circle text-green-500',
        'error': 'fas fa-times-circle text-red-500',
        'warning': 'fas fa-exclamation-triangle text-yellow-500',
        'info': 'fas fa-info-circle text-blue-500'
    };
    
    toast.className += ` ${colors[type] || colors.info}`;
    
    toast.innerHTML = `
        <div class="flex items-center">
            <i class="${icons[type] || icons.info} mr-3"></i>
            <span class="flex-1 text-sm font-medium text-gray-800">${message}</span>
            <button onclick="this.parentElement.parentElement.remove()" class="ml-2 text-gray-400 hover:text-gray-600">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `;
    
    container.appendChild(toast);
    
    // 动画显示
    setTimeout(() => {
        toast.classList.remove('translate-x-full');
        toast.classList.add('translate-x-0');
    }, 10);
    
    // 自动移除
    setTimeout(() => {
        toast.classList.remove('translate-x-0');
        toast.classList.add('translate-x-full');
        setTimeout(() => {
            if (toast.parentElement) {
                toast.parentElement.removeChild(toast);
            }
        }, 300);
    }, duration);
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Utils;
} else {
    window.Utils = Utils;
}