/**
 * 商机发现管理后台 JavaScript
 * 数据采集管理、关键词配置、平台状态监控
 */

class IndieNavigatorAdmin {
    constructor() {
        this.apiBase = window.API_CONFIG ? window.API_CONFIG.baseURL : '';
        this.currentView = 'overview';
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.loadOverview();
        this.updateStatusBar('管理后台已初始化');
    }
    
    setupEventListeners() {
        // 导航菜单点击
        document.querySelectorAll('[data-view]').forEach(button => {
            button.addEventListener('click', (e) => {
                const view = e.target.closest('[data-view]').dataset.view;
                this.switchView(view);
            });
        });
        
        // 开始采集按钮
        const startCollectionBtn = document.getElementById('start-collection');
        if (startCollectionBtn) {
            startCollectionBtn.addEventListener('click', () => this.startCollection());
        }
        
        // 添加关键词按钮
        const addKeywordBtn = document.getElementById('add-keyword');
        if (addKeywordBtn) {
            addKeywordBtn.addEventListener('click', () => this.showAddKeywordModal());
        }
        
        // 刷新数据按钮
        const refreshBtn = document.getElementById('refresh-data');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.refreshCurrentView());
        }
    }
    
    switchView(viewName) {
        // 更新导航状态
        document.querySelectorAll('[data-view]').forEach(btn => {
            btn.classList.remove('bg-primary-500', 'text-white');
            btn.classList.add('text-gray-700', 'hover:bg-gray-50');
        });
        
        const activeBtn = document.querySelector(`[data-view="${viewName}"]`);
        if (activeBtn) {
            activeBtn.classList.remove('text-gray-700', 'hover:bg-gray-50');
            activeBtn.classList.add('bg-primary-500', 'text-white');
        }
        
        // 显示对应视图
        document.querySelectorAll('.admin-view').forEach(view => {
            view.classList.add('hidden');
        });
        
        const targetView = document.getElementById(`${viewName}-view`);
        if (targetView) {
            targetView.classList.remove('hidden');
        }
        
        this.currentView = viewName;
        
        // 加载视图数据
        switch (viewName) {
            case 'overview':
                this.loadOverview();
                break;
            case 'collection':
                this.loadCollectionJobs();
                break;
            case 'keywords':
                this.loadKeywords();
                break;
            case 'platforms':
                this.loadPlatformStatus();
                break;
        }
    }
    
    async loadOverview() {
        try {
            this.updateStatusBar('加载概览数据...');
            
            // 并发请求多个数据源
            const [statsResponse, jobsResponse, platformsResponse] = await Promise.all([
                fetch(`${this.apiBase}/api/indie-navigator/stats`),
                fetch(`${this.apiBase}/api/data-collection/jobs?limit=5`),
                fetch(`${this.apiBase}/api/data-collection/platforms`)
            ]);
            
            const stats = await statsResponse.json();
            const jobs = await jobsResponse.json();
            const platforms = await platformsResponse.json();
            
            this.renderOverviewStats(stats.data);
            this.renderRecentJobs(jobs.data?.jobs || []);
            this.renderPlatformSummary(platforms.data?.platforms || {});
            
            this.updateStatusBar('概览数据加载完成', 'success');
            
        } catch (error) {
            console.error('加载概览数据失败:', error);
            this.updateStatusBar('加载概览数据失败', 'error');
        }
    }
    
    renderOverviewStats(stats) {
        const elements = {
            'total-opportunities': stats.total_opportunities || 0,
            'high-potential': stats.high_potential || 0,
            'trending-signals': stats.trending_signals || 0,
            'platforms-active': 2  // 从平台数据获取
        };
        
        Object.entries(elements).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) {
                this.animateNumber(element, value);
            }
        });
    }
    
    renderRecentJobs(jobs) {
        const container = document.getElementById('recent-jobs');
        if (!container) return;
        
        if (jobs.length === 0) {
            container.innerHTML = '<p class=\"text-gray-500 text-sm\">暂无采集任务</p>';
            return;
        }
        
        container.innerHTML = jobs.map(job => `
            <div class=\"flex items-center justify-between p-3 bg-gray-50 rounded-lg\">
                <div class=\"flex-1\">
                    <div class=\"text-sm font-medium\">${this.getJobTypeLabel(job.job_type)}</div>
                    <div class=\"text-xs text-gray-500\">
                        ${job.signals_collected} 个信号 • ${this.formatDateTime(job.completed_at)}
                    </div>
                </div>
                <div class=\"flex-shrink-0\">
                    <span class=\"inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${this.getStatusBadgeClass(job.status)}\">
                        ${this.getStatusLabel(job.status)}
                    </span>
                </div>
            </div>
        `).join('');
    }
    
    renderPlatformSummary(platforms) {
        const container = document.getElementById('platform-summary');
        if (!container) return;
        
        const platformList = Object.entries(platforms).map(([key, config]) => `
            <div class=\"flex items-center justify-between py-2\">
                <div class=\"flex items-center\">
                    <div class=\"w-2 h-2 rounded-full ${config.enabled ? 'bg-green-400' : 'bg-gray-300'} mr-2\"></div>
                    <span class=\"text-sm\">${config.name}</span>
                </div>
                <span class=\"text-xs text-gray-500\">${config.enabled ? '活跃' : '未启用'}</span>
            </div>
        `).join('');
        
        container.innerHTML = platformList;
    }
    
    async loadCollectionJobs() {
        try {
            this.updateStatusBar('加载采集任务...');
            
            const response = await fetch(`${this.apiBase}/api/data-collection/jobs`);
            const result = await response.json();
            
            if (result.success) {
                this.renderCollectionJobs(result.data.jobs);
                this.updateStatusBar('采集任务加载完成', 'success');
            } else {
                throw new Error(result.error);
            }
            
        } catch (error) {
            console.error('加载采集任务失败:', error);
            this.updateStatusBar('加载采集任务失败', 'error');
        }
    }
    
    renderCollectionJobs(jobs) {
        const container = document.getElementById('collection-jobs-list');
        if (!container) return;
        
        if (jobs.length === 0) {
            container.innerHTML = `
                <div class=\"text-center py-8\">
                    <i class=\"fas fa-inbox text-4xl text-gray-300 mb-4\"></i>
                    <p class=\"text-gray-500\">暂无采集任务记录</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = jobs.map(job => `
            <div class=\"bg-white rounded-lg border border-gray-200 p-6 hover:shadow-md transition-shadow\">
                <div class=\"flex items-center justify-between mb-4\">
                    <div class=\"flex items-center\">
                        <h3 class=\"text-lg font-semibold\">${this.getJobTypeLabel(job.job_type)}</h3>
                        <span class=\"ml-3 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${this.getStatusBadgeClass(job.status)}\">
                            ${this.getStatusLabel(job.status)}
                        </span>
                    </div>
                    <div class=\"text-sm text-gray-500\">
                        ID: ${job.id}
                    </div>
                </div>
                
                <div class=\"grid grid-cols-3 gap-4 mb-4\">
                    <div class=\"text-center\">
                        <div class=\"text-2xl font-bold text-primary-600\">${job.signals_collected}</div>
                        <div class=\"text-xs text-gray-500\">采集信号</div>
                    </div>
                    <div class=\"text-center\">
                        <div class=\"text-2xl font-bold text-green-600\">${job.signals_saved}</div>
                        <div class=\"text-xs text-gray-500\">保存信号</div>
                    </div>
                    <div class=\"text-center\">
                        <div class=\"text-2xl font-bold text-orange-600\">${Math.round((job.signals_saved / job.signals_collected) * 100)}%</div>
                        <div class=\"text-xs text-gray-500\">通过率</div>
                    </div>
                </div>
                
                <div class=\"text-sm text-gray-600\">
                    <div class=\"flex justify-between items-center mb-2\">
                        <span>开始时间:</span>
                        <span>${this.formatDateTime(job.started_at)}</span>
                    </div>
                    <div class=\"flex justify-between items-center mb-2\">
                        <span>完成时间:</span>
                        <span>${this.formatDateTime(job.completed_at)}</span>
                    </div>
                    <div class=\"flex justify-between items-center\">
                        <span>关键词:</span>
                        <span class=\"text-xs\">${(job.config?.keywords || []).join(', ')}</span>
                    </div>
                </div>
            </div>
        `).join('');
    }
    
    async startCollection() {
        try {
            this.updateStatusBar('启动数据采集...');
            
            // 获取用户选择的参数
            const keywords = document.getElementById('collection-keywords')?.value.split(',').map(k => k.trim()) || [];
            const limitPerPlatform = parseInt(document.getElementById('collection-limit')?.value || '50');
            
            const response = await fetch(`${this.apiBase}/api/data-collection/start`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    keywords: keywords.length > 0 ? keywords : undefined,
                    limit_per_platform: limitPerPlatform
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.showToast('数据采集已启动', 'success');
                this.updateStatusBar(`采集完成：${result.data.total_signals} 个信号`, 'success');
                
                // 刷新任务列表
                if (this.currentView === 'collection') {
                    this.loadCollectionJobs();
                }
            } else {
                throw new Error(result.error);
            }
            
        } catch (error) {
            console.error('启动采集失败:', error);
            this.updateStatusBar('启动采集失败', 'error');
            this.showToast('启动采集失败', 'error');
        }
    }
    
    async loadKeywords() {
        try {
            this.updateStatusBar('加载关键词配置...');
            
            const response = await fetch(`${this.apiBase}/api/data-collection/keywords`);
            const result = await response.json();
            
            if (result.success) {
                this.renderKeywordsList(result.data.keywords);
                this.updateStatusBar('关键词配置加载完成', 'success');
            } else {
                throw new Error(result.error);
            }
            
        } catch (error) {
            console.error('加载关键词失败:', error);
            this.updateStatusBar('加载关键词失败', 'error');
        }
    }
    
    renderKeywordsList(keywords) {
        const container = document.getElementById('keywords-list');
        if (!container) return;
        
        container.innerHTML = keywords.map(keyword => `
            <div class=\"bg-white rounded-lg border border-gray-200 p-4 flex items-center justify-between hover:shadow-sm transition-shadow\">
                <div class=\"flex-1\">
                    <div class=\"flex items-center\">
                        <span class=\"font-medium\">${keyword.keyword}</span>
                        <span class=\"ml-2 inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-800\">
                            ${keyword.category}
                        </span>
                        <div class=\"ml-2 flex items-center\">
                            ${[...Array(keyword.priority)].map(() => '<i class=\"fas fa-star text-yellow-400 text-xs\"></i>').join('')}
                        </div>
                    </div>
                </div>
                <div class=\"flex items-center space-x-2\">
                    <span class=\"text-sm text-gray-500\">${keyword.signals_collected || 0} 信号</span>
                    <button class=\"text-gray-400 hover:text-red-500 transition-colors\" onclick=\"admin.deleteKeyword(${keyword.id})\">
                        <i class=\"fas fa-trash text-sm\"></i>
                    </button>
                </div>
            </div>
        `).join('');
    }
    
    async loadPlatformStatus() {
        try {
            this.updateStatusBar('加载平台状态...');
            
            const response = await fetch(`${this.apiBase}/api/data-collection/platforms`);
            const result = await response.json();
            
            if (result.success) {
                this.renderPlatformStatus(result.data.platforms);
                this.updateStatusBar('平台状态加载完成', 'success');
            } else {
                throw new Error(result.error);
            }
            
        } catch (error) {
            console.error('加载平台状态失败:', error);
            this.updateStatusBar('加载平台状态失败', 'error');
        }
    }
    
    renderPlatformStatus(platforms) {
        const container = document.getElementById('platforms-list');
        if (!container) return;
        
        container.innerHTML = Object.entries(platforms).map(([key, config]) => `
            <div class=\"bg-white rounded-lg border border-gray-200 p-6\">
                <div class=\"flex items-center justify-between mb-4\">
                    <div class=\"flex items-center\">
                        <div class=\"w-3 h-3 rounded-full ${config.enabled ? 'bg-green-400' : 'bg-gray-300'} mr-3\"></div>
                        <h3 class=\"text-lg font-semibold\">${config.name}</h3>
                    </div>
                    <span class=\"inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}\">
                        ${config.enabled ? '已启用' : '未启用'}
                    </span>
                </div>
                
                <p class=\"text-gray-600 text-sm mb-4\">${config.description}</p>
                
                <div class=\"space-y-2 text-sm\">
                    <div class=\"flex justify-between\">
                        <span class=\"text-gray-500\">API限制:</span>
                        <span>${config.rate_limit}</span>
                    </div>
                    <div class=\"flex justify-between\">
                        <span class=\"text-gray-500\">最后采集:</span>
                        <span>${config.last_collection || '未采集'}</span>
                    </div>
                </div>
            </div>
        `).join('');
    }
    
    // 工具方法
    animateNumber(element, targetValue) {
        const startValue = parseInt(element.textContent) || 0;
        const duration = 1000;
        const step = (targetValue - startValue) / (duration / 16);
        
        let currentValue = startValue;
        const timer = setInterval(() => {
            currentValue += step;
            if (Math.abs(currentValue - targetValue) < Math.abs(step)) {
                element.textContent = targetValue;
                clearInterval(timer);
            } else {
                element.textContent = Math.round(currentValue);
            }
        }, 16);
    }
    
    getJobTypeLabel(type) {
        const labels = {
            'full': '全量采集',
            'keyword': '关键词采集',
            'platform': '平台采集'
        };
        return labels[type] || type;
    }
    
    getStatusLabel(status) {
        const labels = {
            'pending': '等待中',
            'running': '运行中',
            'completed': '已完成',
            'failed': '已失败'
        };
        return labels[status] || status;
    }
    
    getStatusBadgeClass(status) {
        const classes = {
            'pending': 'bg-yellow-100 text-yellow-800',
            'running': 'bg-blue-100 text-blue-800',
            'completed': 'bg-green-100 text-green-800',
            'failed': 'bg-red-100 text-red-800'
        };
        return classes[status] || 'bg-gray-100 text-gray-800';
    }
    
    formatDateTime(dateString) {
        if (!dateString) return '未知';
        const date = new Date(dateString);
        return date.toLocaleString('zh-CN');
    }
    
    updateStatusBar(message, type = 'info') {
        console.log(`[${type.toUpperCase()}] ${message}`);
        // 实际应用中可以更新UI状态栏
    }
    
    showToast(message, type = 'info') {
        console.log(`[TOAST ${type.toUpperCase()}] ${message}`);
        // 实际应用中可以显示toast通知
    }
    
    refreshCurrentView() {
        switch (this.currentView) {
            case 'overview':
                this.loadOverview();
                break;
            case 'collection':
                this.loadCollectionJobs();
                break;
            case 'keywords':
                this.loadKeywords();
                break;
            case 'platforms':
                this.loadPlatformStatus();
                break;
        }
    }
}

// 全局实例
let admin;

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    admin = new IndieNavigatorAdmin();
});