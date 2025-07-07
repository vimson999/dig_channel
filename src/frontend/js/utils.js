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

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Utils;
} else {
    window.Utils = Utils;
}