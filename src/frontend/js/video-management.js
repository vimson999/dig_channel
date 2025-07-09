// 状态栏和Toast函数
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

// 视频管理页面功能
class VideoManager {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 20;
        this.totalRecords = 0;
        this.currentFilters = {
            search: '',
            channel: '',
            category: '',
            sortBy: 'published_at',
            order: 'desc'
        };
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.loadInitialData();
    }
    
    setupEventListeners() {
        // 搜索功能
        const searchInput = document.getElementById('search-videos');
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    this.currentFilters.search = e.target.value.trim();
                    this.currentPage = 1;
                    this.loadVideos();
                }, 500);
            });
        }
        
        // 筛选功能
        const filterChannel = document.getElementById('filter-channel');
        if (filterChannel) {
            filterChannel.addEventListener('change', (e) => {
                this.currentFilters.channel = e.target.value;
                this.currentPage = 1;
                this.loadVideos();
            });
        }
        
        const filterCategory = document.getElementById('filter-category');
        if (filterCategory) {
            filterCategory.addEventListener('change', (e) => {
                this.currentFilters.category = e.target.value;
                this.currentPage = 1;
                this.loadVideos();
            });
        }
        
        // 排序功能
        const sortBy = document.getElementById('sort-by');
        if (sortBy) {
            sortBy.addEventListener('change', (e) => {
                const [field, order] = e.target.value.split(':');
                this.currentFilters.sortBy = field;
                this.currentFilters.order = order;
                this.currentPage = 1;
                this.loadVideos();
            });
        }
    }
    
    async loadInitialData() {
        showStatusBar('正在加载视频数据...');
        
        try {
            // 并行加载数据
            await Promise.all([
                this.loadVideoStats(),
                this.loadChannels(),
                this.loadCategories(),
                this.loadVideos()
            ]);
            
            hideStatusBar();
        } catch (error) {
            console.error('加载初始数据失败:', error);
            hideStatusBar();
            showToast('加载数据失败：' + error.message, 'error');
        }
    }
    
    async loadVideoStats() {
        try {
            const response = await api.getVideoStats();
            if (response.success) {
                const stats = response.data;
                
                // 更新统计卡片
                this.updateStatCard('total-videos', stats.total_videos || 0);
                this.updateStatCard('today-videos', stats.today_videos || 0);
                this.updateStatCard('week-videos', stats.week_videos || 0);
                
                // 计算总播放量
                let totalViews = 0;
                if (stats.top_videos && stats.top_videos.length > 0) {
                    totalViews = stats.top_videos.reduce((sum, video) => sum + video.view_count, 0);
                }
                this.updateStatCard('total-views', this.formatNumber(totalViews));
                this.updateStatCard('hot-videos', stats.top_videos ? stats.top_videos.length : 0);
            }
        } catch (error) {
            console.error('加载视频统计失败:', error);
        }
    }
    
    async loadChannels() {
        try {
            const response = await api.getVideoChannels();
            if (response.success) {
                const channels = response.data.channels;
                const select = document.getElementById('filter-channel');
                
                if (select) {
                    // 清空现有选项（保留第一个"所有频道"）
                    select.innerHTML = '<option value="">所有频道</option>';
                    
                    channels.forEach(channel => {
                        const option = document.createElement('option');
                        option.value = channel.id;
                        option.textContent = `${channel.name} (${channel.video_count || 0}个视频)`;
                        select.appendChild(option);
                    });
                }
            }
        } catch (error) {
            console.error('加载频道列表失败:', error);
        }
    }
    
    async loadCategories() {
        try {
            const response = await api.getVideoCategories();
            if (response.success) {
                const categories = response.data.categories;
                const select = document.getElementById('filter-category');
                
                if (select) {
                    // 清空现有选项（保留第一个"所有分类"）
                    select.innerHTML = '<option value="">所有分类</option>';
                    
                    categories.forEach(category => {
                        const option = document.createElement('option');
                        option.value = category.name;
                        option.textContent = `${category.name} (${category.count}个视频)`;
                        select.appendChild(option);
                    });
                }
            }
        } catch (error) {
            console.error('加载分类列表失败:', error);
        }
    }
    
    async loadVideos() {
        try {
            const params = {
                page: this.currentPage,
                per_page: this.pageSize,
                sort_by: this.currentFilters.sortBy,
                order: this.currentFilters.order
            };
            
            // 添加筛选条件
            if (this.currentFilters.search) {
                params.q = this.currentFilters.search;
            }
            if (this.currentFilters.channel) {
                params.channel_id = this.currentFilters.channel;
            }
            if (this.currentFilters.category) {
                params.category = this.currentFilters.category;
            }
            
            // 根据是否有搜索条件选择API方法
            const response = this.currentFilters.search 
                ? await api.searchVideos(params)
                : await api.getVideos(params);
            
            if (response.success) {
                // 调试输出
                console.log('=== 视频管理调试信息 ===');
                console.log('API响应:', response);
                console.log('视频数据:', response.data.videos);
                console.log('分页信息:', response.data.pagination);
                
                this.renderVideos(response.data.videos);
                this.updatePagination(response.data.pagination);
                this.updateVideosCount(response.data.pagination.total);
            } else {
                throw new Error(response.message);
            }
        } catch (error) {
            console.error('加载视频列表失败:', error);
            this.renderErrorState();
            showToast('加载视频列表失败：' + error.message, 'error');
        }
    }
    
    renderVideos(videos) {
        const container = document.getElementById('videos-grid');
        if (!container) return;
        
        if (videos.length === 0) {
            container.innerHTML = `
                <div class="flex flex-col items-center justify-center py-12">
                    <i class="fas fa-video-slash text-4xl text-gray-300 mb-4"></i>
                    <p class="text-gray-500 text-lg mb-2">暂无视频数据</p>
                    <p class="text-gray-400 text-sm">请先同步频道视频数据</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = `
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                ${videos.map(video => this.renderVideoCard(video)).join('')}
            </div>
        `;
    }
    
    renderVideoCard(video) {
        const publishedDate = new Date(video.published_at);
        const formattedDate = publishedDate.toLocaleDateString('zh-CN', {
            year: 'numeric',
            month: 'numeric',
            day: 'numeric'
        });
        
        return `
            <div class="bg-white rounded-xl shadow-soft border border-gray-100 overflow-hidden hover:shadow-medium transition-all duration-300 hover:-translate-y-1 group">
                <!-- 视频缩略图 -->
                <div class="relative">
                    <img src="${video.thumbnail_url || '/images/video-placeholder.jpg'}" 
                         alt="${video.title}" 
                         class="w-full h-48 object-cover">
                    <div class="absolute bottom-2 right-2 bg-black/80 text-white px-2 py-1 rounded text-xs font-medium">
                        ${this.formatDuration(video.duration)}
                    </div>
                    <div class="absolute top-2 left-2">
                        <span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-white/90 text-gray-800">
                            ${video.category_name || '未分类'}
                        </span>
                    </div>
                    <!-- 悬停时显示操作按钮 -->
                    <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center gap-4">
                        <button onclick="videoManager.showVideoDetail(${video.id})" 
                                class="bg-white/90 hover:bg-white text-gray-800 p-3 rounded-full transition-colors" 
                                title="查看详情">
                            <i class="fas fa-eye"></i>
                        </button>
                        <a href="${video.url}" target="_blank" 
                           class="bg-red-600/90 hover:bg-red-600 text-white p-3 rounded-full transition-colors" 
                           title="在YouTube查看">
                            <i class="fab fa-youtube"></i>
                        </a>
                    </div>
                </div>
                
                <!-- 视频信息 -->
                <div class="p-4">
                    <h3 class="text-sm font-semibold text-gray-900 line-clamp-2 mb-2" title="${video.title}">
                        ${video.title}
                    </h3>
                    
                    <div class="text-xs text-gray-600 mb-3">
                        ${video.channel_name || '未知频道'}
                    </div>
                    
                    <!-- 数据统计 -->
                    <div class="grid grid-cols-3 gap-3 mb-3 text-xs">
                        <div class="text-center">
                            <div class="font-semibold text-blue-600">${this.formatNumber(video.view_count)}</div>
                            <div class="text-gray-500">播放</div>
                        </div>
                        <div class="text-center">
                            <div class="font-semibold text-green-600">${this.formatNumber(video.like_count)}</div>
                            <div class="text-gray-500">点赞</div>
                        </div>
                        <div class="text-center">
                            <div class="font-semibold text-purple-600">${this.formatNumber(video.comment_count)}</div>
                            <div class="text-gray-500">评论</div>
                        </div>
                    </div>
                    
                    <!-- 标签 -->
                    ${video.tags && video.tags.length > 0 ? `
                        <div class="flex flex-wrap gap-1 mb-3">
                            ${video.tags.slice(0, 2).map(tag => `
                                <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                                    ${tag}
                                </span>
                            `).join('')}
                            ${video.tags.length > 2 ? `<span class="text-xs text-gray-400">+${video.tags.length - 2}</span>` : ''}
                        </div>
                    ` : ''}
                    
                    <!-- 发布时间 -->
                    <div class="text-xs text-gray-500 border-t border-gray-100 pt-3">
                        发布于 ${formattedDate}
                    </div>
                </div>
            </div>
        `;
    }
    
    renderErrorState() {
        const container = document.getElementById('videos-grid');
        if (!container) return;
        
        container.innerHTML = `
            <div class="flex flex-col items-center justify-center py-12">
                <i class="fas fa-exclamation-triangle text-4xl text-red-300 mb-4"></i>
                <p class="text-red-500 text-lg mb-2">加载失败</p>
                <button onclick="videoManager.loadVideos()" 
                        class="px-4 py-2 bg-primary-600 text-white rounded-button hover:bg-primary-700 transition-colors">
                    重试
                </button>
            </div>
        `;
    }
    
    updateStatCard(elementId, value) {
        const element = document.getElementById(elementId);
        if (element) {
            element.textContent = value;
        }
    }
    
    updateVideosCount(count) {
        const element = document.getElementById('videos-count');
        if (element) {
            element.textContent = this.formatNumber(count);
        }
    }
    
    updatePagination(pagination) {
        this.totalRecords = pagination.total;
        
        // 更新页面信息
        const pageStart = document.getElementById('page-start');
        const pageEnd = document.getElementById('page-end');
        const totalRecords = document.getElementById('total-records');
        
        if (pageStart) pageStart.textContent = ((pagination.page - 1) * pagination.per_page + 1);
        if (pageEnd) pageEnd.textContent = Math.min(pagination.page * pagination.per_page, pagination.total);
        if (totalRecords) totalRecords.textContent = this.formatNumber(pagination.total);
        
        // 更新分页按钮
        const prevBtn = document.getElementById('prev-page');
        const nextBtn = document.getElementById('next-page');
        
        if (prevBtn) {
            prevBtn.disabled = !pagination.has_prev;
        }
        if (nextBtn) {
            nextBtn.disabled = !pagination.has_next;
        }
        
        // 生成页码按钮
        this.generatePageNumbers(pagination);
    }
    
    generatePageNumbers(pagination) {
        const container = document.getElementById('page-numbers');
        if (!container) return;
        
        container.innerHTML = '';
        
        const currentPage = pagination.page;
        const totalPages = pagination.pages;
        
        // 计算显示的页码范围
        let startPage = Math.max(1, currentPage - 2);
        let endPage = Math.min(totalPages, currentPage + 2);
        
        // 调整范围以显示5个页码
        if (endPage - startPage < 4) {
            if (startPage === 1) {
                endPage = Math.min(totalPages, startPage + 4);
            } else {
                startPage = Math.max(1, endPage - 4);
            }
        }
        
        // 生成页码按钮
        for (let i = startPage; i <= endPage; i++) {
            const button = document.createElement('button');
            button.textContent = i;
            button.className = i === currentPage 
                ? 'px-3 py-2 bg-primary-600 text-white rounded-button text-sm font-medium'
                : 'px-3 py-2 border border-gray-300 rounded-button text-sm text-gray-700 bg-white hover:bg-gray-50';
            button.onclick = () => this.goToPage(i);
            container.appendChild(button);
        }
    }
    
    goToPage(page) {
        this.currentPage = page;
        this.loadVideos();
    }
    
    async showVideoDetail(videoId) {
        try {
            showStatusBar('正在加载视频详情...');
            
            const response = await api.getVideo(videoId);
            if (response.success) {
                this.renderVideoDetail(response.data.video);
                this.showVideoDetailModal();
            } else {
                throw new Error(response.message);
            }
        } catch (error) {
            console.error('加载视频详情失败:', error);
            showToast('加载视频详情失败：' + error.message, 'error');
        } finally {
            hideStatusBar();
        }
    }
    
    renderVideoDetail(video) {
        const content = document.getElementById('video-detail-content');
        if (!content) return;
        
        const publishedDate = new Date(video.published_at).toLocaleString('zh-CN');
        
        content.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- 视频基本信息 -->
                <div class="space-y-4">
                    <div class="relative">
                        <img src="${video.thumbnail_url || '/images/video-placeholder.jpg'}" 
                             alt="${video.title}" 
                             class="w-full rounded-lg border border-gray-200">
                        <span class="absolute bottom-2 right-2 bg-black/80 text-white px-2 py-1 rounded text-sm">
                            ${video.duration_display || this.formatDuration(video.duration)}
                        </span>
                    </div>
                    
                    <div>
                        <h3 class="text-lg font-bold text-gray-900 mb-2">${video.title}</h3>
                        <p class="text-sm text-gray-600 leading-relaxed">
                            ${video.description || '暂无描述'}
                        </p>
                    </div>
                    
                    ${video.tags && video.tags.length > 0 ? `
                        <div>
                            <h4 class="text-sm font-medium text-gray-700 mb-2">标签</h4>
                            <div class="flex flex-wrap gap-2">
                                ${video.tags.map(tag => `
                                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                                        ${tag}
                                    </span>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                </div>
                
                <!-- 视频数据 -->
                <div class="space-y-4">
                    <div class="grid grid-cols-2 gap-4">
                        <div class="bg-blue-50 rounded-lg p-4">
                            <div class="text-2xl font-bold text-blue-600">${this.formatNumber(video.view_count)}</div>
                            <div class="text-sm text-blue-800">播放量</div>
                        </div>
                        <div class="bg-green-50 rounded-lg p-4">
                            <div class="text-2xl font-bold text-green-600">${this.formatNumber(video.like_count)}</div>
                            <div class="text-sm text-green-800">点赞数</div>
                        </div>
                        <div class="bg-purple-50 rounded-lg p-4">
                            <div class="text-2xl font-bold text-purple-600">${this.formatNumber(video.comment_count)}</div>
                            <div class="text-sm text-purple-800">评论数</div>
                        </div>
                        <div class="bg-orange-50 rounded-lg p-4">
                            <div class="text-2xl font-bold text-orange-600">${video.engagement_rate || 0}%</div>
                            <div class="text-sm text-orange-800">互动率</div>
                        </div>
                    </div>
                    
                    <div class="bg-gray-50 rounded-lg p-4 space-y-3">
                        <h4 class="text-sm font-medium text-gray-700">视频信息</h4>
                        <div class="grid grid-cols-1 gap-2 text-sm">
                            <div class="flex justify-between">
                                <span class="text-gray-500">频道：</span>
                                <span class="font-medium">${video.channel_name || '未知'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-gray-500">分类：</span>
                                <span class="font-medium">${video.category_name || '未分类'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-gray-500">发布时间：</span>
                                <span class="font-medium">${publishedDate}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-gray-500">时长：</span>
                                <span class="font-medium">${video.duration_display || this.formatDuration(video.duration)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-gray-500">语言：</span>
                                <span class="font-medium">${video.language || '未知'}</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="flex gap-3">
                        <a href="${video.url}" target="_blank" 
                           class="flex-1 bg-red-600 text-white px-4 py-2 rounded-button text-center hover:bg-red-700 transition-colors">
                            <i class="fab fa-youtube mr-2"></i>在YouTube查看
                        </a>
                        <button onclick="videoManager.shareVideo('${video.url}')" 
                                class="px-4 py-2 border border-gray-300 rounded-button hover:bg-gray-50 transition-colors">
                            <i class="fas fa-share mr-2"></i>分享
                        </button>
                    </div>
                </div>
            </div>
        `;
    }
    
    showVideoDetailModal() {
        const modal = document.getElementById('videoDetailModal');
        const content = document.getElementById('videoModalContent');
        
        if (modal && content) {
            modal.classList.remove('hidden');
            setTimeout(() => {
                content.classList.remove('scale-95', 'opacity-0');
                content.classList.add('scale-100', 'opacity-100');
            }, 10);
        }
    }
    
    closeVideoDetailModal() {
        const modal = document.getElementById('videoDetailModal');
        const content = document.getElementById('videoModalContent');
        
        if (modal && content) {
            content.classList.remove('scale-100', 'opacity-100');
            content.classList.add('scale-95', 'opacity-0');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }
    }
    
    shareVideo(url) {
        if (navigator.share) {
            navigator.share({
                title: '分享视频',
                url: url
            });
        } else {
            // 复制到剪贴板
            navigator.clipboard.writeText(url).then(() => {
                showToast('视频链接已复制到剪贴板', 'success');
            }).catch(() => {
                showToast('复制失败，请手动复制', 'error');
            });
        }
    }
    
    formatNumber(num) {
        if (num >= 1000000) {
            return (num / 1000000).toFixed(1) + 'M';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        }
        return num.toString();
    }
    
    formatDuration(duration) {
        if (!duration) return '未知';
        
        // 解析ISO 8601格式的时长 (PT4M13S)
        const match = duration.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
        if (!match) return duration;
        
        const hours = parseInt(match[1] || 0);
        const minutes = parseInt(match[2] || 0);
        const seconds = parseInt(match[3] || 0);
        
        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${seconds.toString().padStart(2, '0')}`;
        }
    }
}

// 全局函数
function clearFilters() {
    const searchInput = document.getElementById('search-videos');
    const channelFilter = document.getElementById('filter-channel');
    const categoryFilter = document.getElementById('filter-category');
    const sortBy = document.getElementById('sort-by');
    
    if (searchInput) searchInput.value = '';
    if (channelFilter) channelFilter.value = '';
    if (categoryFilter) categoryFilter.value = '';
    if (sortBy) sortBy.value = 'published_at:desc';
    
    // 重置过滤器并重新加载
    videoManager.currentFilters = {
        search: '',
        channel: '',
        category: '',
        sortBy: 'published_at',
        order: 'desc'
    };
    videoManager.currentPage = 1;
    videoManager.loadVideos();
}

function refreshVideos() {
    videoManager.loadVideos();
}

function changePage(delta) {
    const newPage = videoManager.currentPage + delta;
    if (newPage >= 1) {
        videoManager.goToPage(newPage);
    }
}

function syncAllVideos() {
    showSyncVideoModal();
}

function exportVideos() {
    // 导出视频数据功能
    showToast('导出功能开发中...', 'info');
}

function showSyncVideoModal() {
    const modal = document.getElementById('syncVideoModal');
    const content = document.getElementById('syncModalContent');
    
    if (modal && content) {
        modal.classList.remove('hidden');
        setTimeout(() => {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }, 10);
    }
}

function closeSyncVideoModal() {
    const modal = document.getElementById('syncVideoModal');
    const content = document.getElementById('syncModalContent');
    
    if (modal && content) {
        content.classList.remove('scale-100', 'opacity-100');
        content.classList.add('scale-95', 'opacity-0');
        setTimeout(() => {
            modal.classList.add('hidden');
            // 重置模态框状态
            document.getElementById('sync-progress').classList.add('hidden');
            document.getElementById('sync-result').classList.add('hidden');
            document.getElementById('start-sync-btn').disabled = false;
        }, 300);
    }
}

async function startVideoSync() {
    const daysBack = document.getElementById('sync-days-back').value;
    const progressDiv = document.getElementById('sync-progress');
    const resultDiv = document.getElementById('sync-result');
    const startBtn = document.getElementById('start-sync-btn');
    
    // 显示进度条
    progressDiv.classList.remove('hidden');
    resultDiv.classList.add('hidden');
    startBtn.disabled = true;
    
    try {
        const response = await api.syncVideos({
            days_back: parseInt(daysBack)
        });
        
        if (response.success) {
            // 显示成功结果
            resultDiv.innerHTML = `
                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                    <div class="flex items-center gap-2 text-green-800 mb-2">
                        <i class="fas fa-check-circle"></i>
                        <span class="font-medium">同步完成！</span>
                    </div>
                    <div class="text-sm text-green-700 space-y-1">
                        <div>处理频道: ${response.data.processed} 个</div>
                        <div>新增视频: ${response.data.new_videos} 个</div>
                        <div>更新视频: ${response.data.updated_videos} 个</div>
                        ${response.data.errors > 0 ? `<div class="text-red-600">错误: ${response.data.errors} 个</div>` : ''}
                    </div>
                </div>
            `;
            
            // 刷新视频列表
            setTimeout(() => {
                videoManager.loadVideos();
                videoManager.loadVideoStats();
            }, 1000);
            
        } else {
            throw new Error(response.message);
        }
    } catch (error) {
        console.error('同步视频失败:', error);
        resultDiv.innerHTML = `
            <div class="bg-red-50 border border-red-200 rounded-lg p-4">
                <div class="flex items-center gap-2 text-red-800 mb-2">
                    <i class="fas fa-exclamation-circle"></i>
                    <span class="font-medium">同步失败</span>
                </div>
                <div class="text-sm text-red-700">${error.message}</div>
            </div>
        `;
    } finally {
        progressDiv.classList.add('hidden');
        resultDiv.classList.remove('hidden');
        startBtn.disabled = false;
    }
}

function closeVideoDetailModal() {
    videoManager.closeVideoDetailModal();
}

// 初始化视频管理器
let videoManager;
document.addEventListener('DOMContentLoaded', () => {
    videoManager = new VideoManager();
});

// 移动端菜单切换
function toggleMobileMenu() {
    // 简单实现，可以后续完善
    alert('移动端菜单功能待完善');
}

// 模态框外部点击关闭
document.addEventListener('click', (e) => {
    if (e.target.id === 'videoDetailModal') {
        closeVideoDetailModal();
    }
    if (e.target.id === 'syncVideoModal') {
        closeSyncVideoModal();
    }
});