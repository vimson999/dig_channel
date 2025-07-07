// API调用模块
class APIClient {
    constructor(baseURL = CONFIG.API_BASE_URL) {
        this.baseURL = baseURL;
        this.defaultHeaders = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
    }

    // 构建完整URL
    buildURL(endpoint, params = {}) {
        const url = new URL(this.baseURL + endpoint);
        
        // 添加查询参数
        Object.keys(params).forEach(key => {
            if (params[key] !== null && params[key] !== undefined) {
                url.searchParams.append(key, params[key]);
            }
        });
        
        return url.toString();
    }

    // 通用请求方法
    async request(method, endpoint, data = null, params = {}) {
        try {
            const url = this.buildURL(endpoint, params);
            
            const options = {
                method: method.toUpperCase(),
                headers: { ...this.defaultHeaders },
                mode: 'cors'
            };

            // 添加请求体
            if (data && ['POST', 'PUT', 'PATCH'].includes(method.toUpperCase())) {
                options.body = JSON.stringify(data);
            }

            console.log(`API请求: ${method.toUpperCase()} ${url}`);
            
            const response = await fetch(url, options);
            
            // 检查响应状态
            if (!response.ok) {
                throw new APIError(
                    response.status,
                    `HTTP ${response.status}: ${response.statusText}`,
                    await this.parseErrorResponse(response)
                );
            }

            // 解析响应
            const result = await response.json();
            
            console.log(`API响应:`, result);
            
            return result;

        } catch (error) {
            if (error instanceof APIError) {
                throw error;
            }
            
            // 网络错误或其他错误
            throw new APIError(
                0,
                CONFIG.ERROR_MESSAGES.NETWORK_ERROR,
                { originalError: error.message }
            );
        }
    }

    // 解析错误响应
    async parseErrorResponse(response) {
        try {
            const errorData = await response.json();
            return errorData.error || errorData;
        } catch {
            return { message: response.statusText };
        }
    }

    // GET请求
    async get(endpoint, params = {}) {
        return this.request('GET', endpoint, null, params);
    }

    // POST请求
    async post(endpoint, data = {}, params = {}) {
        return this.request('POST', endpoint, data, params);
    }

    // PUT请求
    async put(endpoint, data = {}, params = {}) {
        return this.request('PUT', endpoint, data, params);
    }

    // DELETE请求
    async delete(endpoint, params = {}) {
        return this.request('DELETE', endpoint, null, params);
    }

    // 频道相关API
    async getChannels(params = {}) {
        return this.get(CONFIG.ENDPOINTS.CHANNELS, params);
    }

    async getChannel(channelId) {
        return this.get(`${CONFIG.ENDPOINTS.CHANNELS}/${channelId}`);
    }

    async createChannel(channelData) {
        return this.post(CONFIG.ENDPOINTS.CHANNELS, channelData);
    }

    async updateChannel(channelId, channelData) {
        return this.put(`${CONFIG.ENDPOINTS.CHANNELS}/${channelId}`, channelData);
    }

    async deleteChannel(channelId) {
        return this.delete(`${CONFIG.ENDPOINTS.CHANNELS}/${channelId}`);
    }

    async previewChannel(url) {
        return this.post(CONFIG.ENDPOINTS.CHANNEL_PREVIEW, { url });
    }

    async syncChannel(channelId, date = null) {
        const data = date ? { date } : {};
        return this.post(`${CONFIG.ENDPOINTS.CHANNELS}/${channelId}/sync`, data);
    }

    async syncAllChannels(date = null) {
        const data = date ? { date } : {};
        return this.post(CONFIG.ENDPOINTS.CHANNEL_SYNC, data);
    }

    async getChannelCategories() {
        return this.get(CONFIG.ENDPOINTS.CHANNEL_CATEGORIES);
    }

    async searchChannels(keyword, params = {}) {
        return this.get(CONFIG.ENDPOINTS.CHANNEL_SEARCH, { q: keyword, ...params });
    }

    async getChannelStatistics(channelId) {
        return this.get(`${CONFIG.ENDPOINTS.CHANNELS}/${channelId}/statistics`);
    }

    // 排行榜相关API
    async getAllRankings(limit = 3) {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_ALL, { limit });
    }

    async getDailyRevenueRanking(limit = 10) {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_DAILY_REVENUE, { limit });
    }

    async getMonthlyRevenueRanking(limit = 10) {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_MONTHLY_REVENUE, { limit });
    }

    async getDailySubscriberRanking(limit = 10) {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_DAILY_SUBSCRIBER, { limit });
    }

    async getMonthlySubscriberRanking(limit = 10) {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_MONTHLY_SUBSCRIBER, { limit });
    }

    async getChannelRankings(channelId) {
        return this.get(`${CONFIG.ENDPOINTS.RANKINGS_ALL.replace('/all', '')}/channel/${channelId}`);
    }

    async getRankingTypes() {
        return this.get(CONFIG.ENDPOINTS.RANKINGS_TYPES);
    }

    // 数据相关API
    async getTableData(params = {}) {
        return this.get(CONFIG.ENDPOINTS.DATA_TABLE, params);
    }

    async getChartData(chartType, params = {}) {
        return this.get(CONFIG.ENDPOINTS.DATA_CHART, { chart_type: chartType, ...params });
    }

    async getChannelTrend(channelId, days = 30) {
        return this.get(`${CONFIG.ENDPOINTS.DATA_TABLE.replace('/table', '')}/trend/${channelId}`, { days });
    }

    async getSummaryStatistics() {
        return this.get(CONFIG.ENDPOINTS.DATA_SUMMARY);
    }

    async getChartTypes() {
        return this.get(CONFIG.ENDPOINTS.DATA_CHART_TYPES);
    }

    // 导出数据
    async exportData(params = {}) {
        try {
            const url = this.buildURL(CONFIG.ENDPOINTS.DATA_EXPORT, params);
            
            // 创建一个隐藏的链接来下载文件
            const link = document.createElement('a');
            link.href = url;
            link.download = `youtube_data_${Utils.formatDate(new Date())}.csv`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            return { success: true, message: '导出开始下载' };
        } catch (error) {
            throw new APIError(0, '导出失败', { originalError: error.message });
        }
    }
}

// API错误类
class APIError extends Error {
    constructor(status, message, details = null) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.details = details;
    }

    // 获取用户友好的错误消息
    getUserMessage() {
        // 如果服务器返回了用户友好的消息，优先使用
        if (this.details && this.details.message) {
            return this.details.message;
        }
        
        // 根据状态码返回通用消息
        switch (this.status) {
            case 400:
                return this.message || CONFIG.ERROR_MESSAGES.VALIDATION_ERROR;
            case 401:
                return "未授权访问，请先登录";
            case 403:
                return "访问被拒绝，权限不足";
            case 404:
                return CONFIG.ERROR_MESSAGES.NOT_FOUND;
            case 405:
                return "请求方法不被允许";
            case 409:
                return "资源冲突，请刷新后重试";
            case 422:
                return "数据验证失败，请检查输入格式";
            case 429:
                return "请求过于频繁，请稍后重试";
            case 500:
                return CONFIG.ERROR_MESSAGES.SERVER_ERROR;
            case 502:
                return "网关错误，服务暂时不可用";
            case 503:
                return "服务暂时不可用，请稍后重试";
            case 504:
                return "请求超时，请稍后重试";
            case 0:
                return CONFIG.ERROR_MESSAGES.NETWORK_ERROR;
            default:
                return this.message || CONFIG.ERROR_MESSAGES.UNKNOWN_ERROR;
        }
    }
    
    // 获取错误ID（用于错误追踪）
    getErrorId() {
        return this.details?.error_id || null;
    }
    
    // 是否为网络错误
    isNetworkError() {
        return this.status === 0;
    }
    
    // 是否为客户端错误
    isClientError() {
        return this.status >= 400 && this.status < 500;
    }
    
    // 是否为服务器错误
    isServerError() {
        return this.status >= 500;
    }
}

// 创建全局API客户端实例
const api = new APIClient();

// 响应拦截器 - 处理通用错误
const originalRequest = api.request.bind(api);
api.request = async function(...args) {
    try {
        return await originalRequest(...args);
    } catch (error) {
        // 记录错误
        console.error('API错误:', error);
        
        // 显示错误提示
        if (typeof showToast === 'function') {
            showToast(error.getUserMessage(), 'error');
        }
        
        throw error;
    }
};

// 请求重试功能
class RetryableAPIClient extends APIClient {
    constructor(baseURL, maxRetries = 3, retryDelay = 1000) {
        super(baseURL);
        this.maxRetries = maxRetries;
        this.retryDelay = retryDelay;
    }

    async request(method, endpoint, data = null, params = {}) {
        let lastError;
        
        for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
            try {
                return await super.request(method, endpoint, data, params);
            } catch (error) {
                lastError = error;
                
                // 只在网络错误或5xx错误时重试
                const shouldRetry = (error.status === 0 || error.status >= 500) && 
                                  attempt < this.maxRetries;
                
                if (!shouldRetry) {
                    break;
                }
                
                console.log(`API请求失败，${this.retryDelay}ms后重试... (${attempt + 1}/${this.maxRetries})`);
                await new Promise(resolve => setTimeout(resolve, this.retryDelay));
            }
        }
        
        throw lastError;
    }
}

// 批量API请求工具
class BatchAPIClient {
    constructor(apiClient = api) {
        this.api = apiClient;
        this.pending = new Map();
    }

    // 批量请求
    async batch(requests) {
        const promises = requests.map(request => {
            const { method, endpoint, data, params } = request;
            return this.api.request(method, endpoint, data, params);
        });

        try {
            const results = await Promise.allSettled(promises);
            return results.map((result, index) => ({
                success: result.status === 'fulfilled',
                data: result.status === 'fulfilled' ? result.value : null,
                error: result.status === 'rejected' ? result.reason : null,
                request: requests[index]
            }));
        } catch (error) {
            console.error('批量请求失败:', error);
            throw error;
        }
    }

    // 防重复请求
    async deduplicatedRequest(key, requestFn) {
        if (this.pending.has(key)) {
            return this.pending.get(key);
        }

        const promise = requestFn().finally(() => {
            this.pending.delete(key);
        });

        this.pending.set(key, promise);
        return promise;
    }
}

// 创建批量API客户端实例
const batchApi = new BatchAPIClient();

// 导出API相关类和实例
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { 
        APIClient, 
        APIError, 
        RetryableAPIClient, 
        BatchAPIClient,
        api, 
        batchApi 
    };
}