// 趋势雷达 JavaScript 主文件

// 全局变量
let currentCategory = '';
let currentTrending = '';
let trendingData = {};

// 趋势配置数据
const TRENDING_CONFIG = {
    'general': {
        name: '通用热榜',
        icon: 'fas fa-fire',
        items: [
            { id: 'baidu', name: '百度热搜', logo: 'baidu', endpoint: '/api/topsearch/baidu' },
            { id: 'weibo', name: '微博热搜', logo: 'weibo', endpoint: '/api/topsearch/weibo' },
            { id: 'douyin', name: '抖音热榜', logo: 'douyin', endpoint: '/api/topsearch/douyin' },
            { id: 'bilibili', name: 'Bilibili', logo: 'bilibili', endpoint: '/api/topsearch/bilibili' },
            { id: 'zhihu', name: '知乎热榜', logo: 'zhihu', endpoint: '/api/topsearch/zhihu' },
            { id: 'tencent', name: '腾讯新闻', logo: 'tencent', endpoint: '/api/topsearch/tencent' },
            { id: 'toutiao', name: '今日头条', logo: 'default', endpoint: '/api/topsearch/toutiao' },
            { id: 'wangyi', name: '网易新闻', logo: 'default', endpoint: '/api/topsearch/wangyi' },
            { id: 'douban', name: '豆瓣', logo: 'default', endpoint: '/api/topsearch/douban' }
        ]
    },
    'github': {
        name: 'GitHub',
        icon: 'fab fa-github',
        items: [
            { id: 'github-all', name: 'GitHub总榜', logo: 'github', endpoint: '/api/topsearch/github/allstars' },
            { id: 'github-day', name: 'GitHub日榜', logo: 'github', endpoint: '/api/topsearch/github/daystars' },
            { id: 'github-week', name: 'GitHub周榜', logo: 'github', endpoint: '/api/topsearch/github/weekstars' },
            { id: 'github-month', name: 'GitHub月榜', logo: 'github', endpoint: '/api/topsearch/github/monthstars' },
            { id: 'github-year', name: 'GitHub年榜', logo: 'github', endpoint: '/api/topsearch/github/yearstars' }
        ]
    },
    'global': {
        name: '全球热榜',
        icon: 'fas fa-globe',
        items: [
            { id: 'youtube', name: 'YouTube', logo: 'youtube', endpoint: '/api/topsearch/global/youtube' },
            { id: 'huggingface-spaces', name: 'HF Spaces', logo: 'default', endpoint: '/api/topsearch/global/huggingfacespacestrending' },
            { id: 'huggingface-models', name: 'HF Models', logo: 'default', endpoint: '/api/topsearch/global/huggingfacemodelstrending' }
        ]
    }
};

// 趋势雷达管理器
class TrendingRadarManager {
    constructor() {
        this.baseURL = 'https://trendapi.tgmeng.com';
        this.cache = new Map();
        this.cacheTimeout = 5 * 60 * 1000; // 5分钟缓存
        
        this.init();
    }
    
    init() {
        this.renderCategories();
        this.setupEventListeners();
        
        // 默认选择第一个分类
        if (Object.keys(TRENDING_CONFIG).length > 0) {
            this.selectCategory(Object.keys(TRENDING_CONFIG)[0]);
        }
    }
    
    setupEventListeners() {
        // 搜索功能
        const searchInput = document.getElementById('search-trends');
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    this.filterTrendingItems(e.target.value.trim());
                }, 300);
            });
        }
    }
    
    renderCategories() {
        const container = document.getElementById('category-list');
        if (!container) return;
        
        container.innerHTML = '';
        
        Object.entries(TRENDING_CONFIG).forEach(([key, category]) => {
            const categoryElement = document.createElement('div');
            categoryElement.className = 'category-item';
            categoryElement.dataset.category = key;
            categoryElement.innerHTML = `
                <div class="category-icon">
                    <i class="${category.icon}"></i>
                </div>
                <span>${category.name}</span>
            `;
            
            categoryElement.addEventListener('click', () => {
                this.selectCategory(key);
            });
            
            container.appendChild(categoryElement);
        });
    }
    
    selectCategory(categoryKey) {
        // 更新UI状态
        document.querySelectorAll('.category-item').forEach(item => {
            item.classList.remove('active');
        });
        
        const selectedItem = document.querySelector(`[data-category="${categoryKey}"]`);
        if (selectedItem) {
            selectedItem.classList.add('active');
        }
        
        currentCategory = categoryKey;
        this.renderTrendingList(categoryKey);
        
        // 清空详情面板
        this.clearDetailPanel();
    }
    
    renderTrendingList(categoryKey) {
        const container = document.getElementById('trending-list');
        const countElement = document.getElementById('trending-count');
        
        if (!container || !TRENDING_CONFIG[categoryKey]) return;
        
        const category = TRENDING_CONFIG[categoryKey];
        const items = category.items;
        
        // 更新计数
        if (countElement) {
            countElement.textContent = `${items.length}个榜单`;
        }
        
        // 渲染列表
        container.innerHTML = items.map(item => `
            <div class="trending-item" data-trending="${item.id}" data-endpoint="${item.endpoint}">
                <div class="trending-logo ${item.logo}">
                    ${this.getLogoText(item.name)}
                </div>
                <div class="flex-1">
                    <div class="font-medium text-gray-900 text-sm">${item.name}</div>
                    <div class="text-xs text-gray-500">点击查看热榜</div>
                </div>
                <div class="text-gray-400">
                    <i class="fas fa-chevron-right text-xs"></i>
                </div>
            </div>
        `).join('');
        
        // 绑定点击事件
        container.querySelectorAll('.trending-item').forEach(item => {
            item.addEventListener('click', () => {
                this.selectTrending(item.dataset.trending, item.dataset.endpoint);
            });
        });
    }
    
    async selectTrending(trendingId, endpoint) {
        // 更新UI状态
        document.querySelectorAll('.trending-item').forEach(item => {
            item.classList.remove('active');
        });
        
        const selectedItem = document.querySelector(`[data-trending="${trendingId}"]`);
        if (selectedItem) {
            selectedItem.classList.add('active');
        }
        
        currentTrending = trendingId;
        
        // 显示加载状态
        this.showDetailLoading();
        
        try {
            const data = await this.fetchTrendingData(endpoint);
            this.renderDetailPanel(data, trendingId);
        } catch (error) {
            console.error('加载热榜数据失败:', error);
            this.showDetailError(error.message);
        }
    }
    
    async fetchTrendingData(endpoint) {
        // 检查缓存
        const cacheKey = endpoint;
        const cached = this.cache.get(cacheKey);
        if (cached && Date.now() - cached.timestamp < this.cacheTimeout) {
            console.log('使用缓存数据:', endpoint);
            return cached.data;
        }
        
        // 发起请求
        const url = this.baseURL + endpoint;
        console.log('请求趋势数据:', url);
        
        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (data.code !== 200) {
            throw new Error(data.message || '数据获取失败');
        }
        
        // 缓存数据
        this.cache.set(cacheKey, {
            data: data.data,
            timestamp: Date.now()
        });
        
        return data.data;
    }
    
    renderDetailPanel(data, trendingId) {
        const titleElement = document.getElementById('detail-title');
        const subtitleElement = document.getElementById('detail-subtitle');
        const updateTimeElement = document.getElementById('update-time');
        const contentElement = document.getElementById('detail-content');
        
        if (!data || !data.dataInfo) {
            this.showDetailError('数据格式错误');
            return;
        }
        
        // 更新标题
        if (titleElement) {
            titleElement.textContent = data.dataCardName || '热榜详情';
        }
        if (subtitleElement) {
            subtitleElement.textContent = `${data.dataSize || 0}条 · ${data.dataCardCategory || ''}`;
        }
        if (updateTimeElement) {
            updateTimeElement.textContent = `更新: ${data.dataUpdateTime || '未知'}`;
        }
        
        // 渲染内容
        if (contentElement) {
            if (data.dataInfo.length === 0) {
                contentElement.innerHTML = `
                    <div class="flex flex-col items-center justify-center py-12">
                        <i class="fas fa-inbox text-4xl text-gray-300 mb-4"></i>
                        <p class="text-gray-500">暂无数据</p>
                    </div>
                `;
                return;
            }
            
            contentElement.innerHTML = data.dataInfo.map((item, index) => `
                <div class="trending-rank-item" onclick="window.open('${item.url}', '_blank')" style="cursor: ${item.url ? 'pointer' : 'default'}">
                    <div class="rank-number ${this.getRankClass(index + 1)}">
                        ${index + 1}
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center justify-between">
                            <h4 class="font-medium text-gray-900 text-sm truncate mr-4">
                                ${this.escapeHtml(item.keyword || '无标题')}
                            </h4>
                            <div class="flex items-center gap-2 flex-shrink-0">
                                ${item.hotScore ? `
                                    <span class="hot-score">
                                        <i class="fas fa-fire text-xs"></i>
                                        ${this.formatHotScore(item.hotScore)}
                                    </span>
                                ` : ''}
                                ${item.author ? `
                                    <span class="text-xs text-gray-500">
                                        @${this.escapeHtml(item.author)}
                                    </span>
                                ` : ''}
                                ${item.url ? `
                                    <i class="fas fa-external-link-alt text-xs text-gray-400"></i>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        }
    }
    
    showDetailLoading() {
        const contentElement = document.getElementById('detail-content');
        if (!contentElement) return;
        
        const skeletonItems = Array(10).fill(0).map(() => `
            <div class="skeleton skeleton-item"></div>
        `).join('');
        
        contentElement.innerHTML = `
            <div class="space-y-3">
                ${skeletonItems}
            </div>
        `;
    }
    
    showDetailError(message) {
        const contentElement = document.getElementById('detail-content');
        if (!contentElement) return;
        
        contentElement.innerHTML = `
            <div class="flex flex-col items-center justify-center py-12">
                <i class="fas fa-exclamation-triangle text-4xl text-red-300 mb-4"></i>
                <p class="text-red-500 text-center mb-4">${message}</p>
                <button onclick="trendingRadar.refreshCurrentTrending()" 
                        class="px-4 py-2 bg-primary-600 text-white rounded-button hover:bg-primary-700 transition-colors">
                    重试
                </button>
            </div>
        `;
    }
    
    clearDetailPanel() {
        const titleElement = document.getElementById('detail-title');
        const subtitleElement = document.getElementById('detail-subtitle');
        const updateTimeElement = document.getElementById('update-time');
        const contentElement = document.getElementById('detail-content');
        
        if (titleElement) titleElement.textContent = '热榜详情';
        if (subtitleElement) subtitleElement.textContent = '选择榜单查看详情';
        if (updateTimeElement) updateTimeElement.textContent = '';
        
        if (contentElement) {
            contentElement.innerHTML = `
                <div class="flex flex-col items-center justify-center py-12">
                    <i class="fas fa-chart-line text-4xl text-gray-300 mb-4"></i>
                    <p class="text-gray-500 text-center">选择热榜查看实时数据<br>发现最新趋势</p>
                </div>
            `;
        }
    }
    
    filterTrendingItems(keyword) {
        if (!currentCategory) return;
        
        const container = document.getElementById('trending-list');
        if (!container) return;
        
        const items = container.querySelectorAll('.trending-item');
        let visibleCount = 0;
        
        items.forEach(item => {
            const name = item.querySelector('.font-medium').textContent.toLowerCase();
            const match = !keyword || name.includes(keyword.toLowerCase());
            
            item.style.display = match ? 'flex' : 'none';
            if (match) visibleCount++;
        });
        
        // 更新计数
        const countElement = document.getElementById('trending-count');
        if (countElement) {
            const total = TRENDING_CONFIG[currentCategory]?.items.length || 0;
            countElement.textContent = keyword 
                ? `${visibleCount}/${total}个榜单` 
                : `${total}个榜单`;
        }
    }
    
    async refreshCurrentTrending() {
        if (!currentTrending) return;
        
        const selectedItem = document.querySelector(`[data-trending="${currentTrending}"]`);
        if (!selectedItem) return;
        
        const endpoint = selectedItem.dataset.endpoint;
        
        // 清除缓存
        this.cache.delete(endpoint);
        
        // 重新加载
        await this.selectTrending(currentTrending, endpoint);
    }
    
    async refreshAllData() {
        // 清除所有缓存
        this.cache.clear();
        
        // 如果有选中的趋势，重新加载
        if (currentTrending) {
            await this.refreshCurrentTrending();
        }
        
        showToast('数据已刷新', 'success');
    }
    
    // 工具方法
    getLogoText(name) {
        return name.charAt(0).toUpperCase();
    }
    
    getRankClass(rank) {
        if (rank <= 3) return 'top3';
        if (rank <= 10) return 'top10';
        return 'normal';
    }
    
    getRankEmoji(rank) {
        const emojis = {
            1: '👑',  // 王冠
            2: '🥈',  // 银牌
            3: '🥉'   // 铜牌
        };
        return emojis[rank] || rank;
    }
    
    getHotLevel(hotScore) {
        if (!hotScore) return 0;
        
        // 尝试从热度分数中提取数字
        const scoreNum = parseInt(hotScore.toString().replace(/[^\d]/g, '')) || 0;
        
        if (scoreNum > 100000) return 5;      // 超热
        if (scoreNum > 50000) return 4;       // 很热
        if (scoreNum > 10000) return 3;       // 热门
        if (scoreNum > 1000) return 2;        // 温热
        if (scoreNum > 0) return 1;           // 微热
        return 0;                             // 无热度
    }
    
    formatHotScore(score) {
        if (!score) return '';
        
        // 处理数字格式
        const num = parseInt(score.toString().replace(/[^0-9]/g, ''));
        if (num >= 100000) {
            return (num / 10000).toFixed(1) + '万';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        }
        
        return score;
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// 全局函数
window.logout = async function() {
    try {
        if (!confirm('确定要退出登录吗？')) {
            return;
        }

        showStatusBar('正在退出登录...');
        await api.logout();
        api.clearAuth();
        hideStatusBar();
        showToast('已成功退出登录', 'success');

        setTimeout(() => {
            window.location.href = 'login.html';
        }, 1000);

    } catch (error) {
        console.error('登出失败:', error);
        hideStatusBar();
        api.clearAuth();
        showToast('退出登录', 'info');
        
        setTimeout(() => {
            window.location.href = 'login.html';
        }, 1000);
    }
};

window.displayUserInfo = function() {
    const user = api.getCurrentUser();
    if (!user) return;

    const userNameElements = document.querySelectorAll('.user-name');
    userNameElements.forEach(element => {
        element.textContent = user.display_name || user.username;
    });

    const userAvatarElements = document.querySelectorAll('.user-avatar');
    userAvatarElements.forEach(element => {
        const firstLetter = (user.display_name || user.username).charAt(0).toUpperCase();
        element.textContent = firstLetter;
    });

    console.log('当前用户:', user.username);
};

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

function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast bg-white border-l-4 rounded-lg shadow-lg p-4 mb-2 transform transition-all duration-300 translate-x-full`;
    
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
    
    setTimeout(() => {
        toast.classList.remove('translate-x-full');
        toast.classList.add('translate-x-0');
    }, 10);
    
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

function refreshAllData() {
    if (window.trendingRadar) {
        window.trendingRadar.refreshAllData();
    }
}

function refreshCurrentTrending() {
    if (window.trendingRadar) {
        window.trendingRadar.refreshCurrentTrending();
    }
}

// 初始化
let trendingRadar;
document.addEventListener('DOMContentLoaded', async () => {
    console.log('🔍 [TrendingRadar] 开始认证检查...');
    const token = localStorage.getItem('access_token');
    if (!token) {
        console.log('❌ [TrendingRadar] 没有Token，重定向到登录页');
        window.location.href = 'login.html';
        return;
    }
    
    try {
        const response = await api.verifyToken();
        if (response && response.success && response.data && response.data.user) {
            console.log('✅ [TrendingRadar] Token验证成功，用户:', response.data.user.username);
            localStorage.setItem('user_info', JSON.stringify(response.data.user));
            window.displayUserInfo();
            
            // 初始化趋势雷达
            trendingRadar = new TrendingRadarManager();
            window.trendingRadar = trendingRadar;
        } else {
            console.log('❌ [TrendingRadar] Token验证失败');
            api.clearAuth();
            window.location.href = 'login.html';
        }
    } catch (error) {
        console.error('❌ [TrendingRadar] 认证验证异常:', error);
        if (error.status === 401) {
            console.log('❌ [TrendingRadar] Token已过期');
        }
        api.clearAuth();
        window.location.href = 'login.html';
    }
});