// 全局函数定义 - 确保在页面加载时立即可用
window.logout = async function() {
    try {
        // 显示确认对话框
        if (!confirm('确定要退出登录吗？')) {
            return;
        }

        // 显示加载状态
        showStatusBar('正在退出登录...');

        // 调用登出API
        await api.logout();

        // 清除本地数据
        api.clearAuth();

        hideStatusBar();
        showToast('已成功退出登录', 'success');

        // 短暂延迟后跳转到登录页
        setTimeout(() => {
            window.location.href = 'login.html';
        }, 1000);

    } catch (error) {
        console.error('登出失败:', error);
        hideStatusBar();
        
        // 即使API失败也清除本地认证信息
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

    // 更新页面中的用户名显示
    const userNameElements = document.querySelectorAll('.user-name');
    userNameElements.forEach(element => {
        element.textContent = user.display_name || user.username;
    });

    // 更新用户头像
    const userAvatarElements = document.querySelectorAll('.user-avatar');
    userAvatarElements.forEach(element => {
        const firstLetter = (user.display_name || user.username).charAt(0).toUpperCase();
        element.textContent = firstLetter;
    });

    console.log('当前用户:', user.username);
};

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
            timePeriod: '7',  // 默认最近7天
            sortBy: 'published_at',
            order: 'desc'
        };

        // 数据缓存
        this.allChannels = [];
        this.channelCategories = [];

        // 收藏状态缓存
        this.favoriteStatus = new Map();
        this.videoRequestGate = VideoPageUtils.createLatestRequestGate();
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.setupSearchableFilters();
        this.loadInitialData();
    }

    setupSearchableFilters() {
        this.categoryCombobox = new SearchableCombobox(
            document.getElementById('filter-category'),
            {
                searchPlaceholder: '搜索频道分类',
                searchLabel: '搜索频道分类',
                emptyText: '没有匹配的频道分类',
            },
        );
        this.channelCombobox = new SearchableCombobox(
            document.getElementById('filter-channel'),
            {
                searchPlaceholder: '搜索频道名称',
                searchLabel: '搜索频道名称',
                emptyText: '没有匹配的频道',
                idleMode: 'recent',
                idleHint: '最近使用与热门频道，输入名称搜索其他频道',
                maxRecentItems: 5,
                maxResults: 8,
                recentStorageKey: 'video-page-recent-channel-filters',
            },
        );
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
                }, 250);
            });
        }
        
        // 筛选功能
        const filterCategory = document.getElementById('filter-category');
        if (filterCategory) {
            filterCategory.addEventListener('change', (e) => {
                this.handleCategoryChange(e.target.value);
            });
        }

        const filterChannel = document.getElementById('filter-channel');
        if (filterChannel) {
            filterChannel.addEventListener('change', (e) => {
                const value = e.target.value;
                this.currentFilters.channel = value ? Number(value) : '';
                this.currentPage = 1;
                this.loadVideos();
            });
        }
        
        // 时间段和排序功能
        const timePeriod = document.getElementById('time-period');
        const sortRule = document.getElementById('sort-rule');
        
        if (timePeriod) {
            timePeriod.addEventListener('change', (e) => {
                console.log('🔄 [VideoManager] 时间段变更:', e.target.value);
                this.currentFilters.timePeriod = e.target.value;
                this.currentPage = 1;
                this.loadVideos();
            });
        }
        
        if (sortRule) {
            sortRule.addEventListener('change', (e) => {
                console.log('🔄 [VideoManager] 排序规则变更:', e.target.value);
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
            // 先解析URL参数，设置初始筛选条件
            this.parseUrlParams();
            
            // 列表优先渲染；筛选元数据并行加载但不阻塞首屏卡片。
            const filterDataPromise = Promise.all([
                this.loadChannels(),
                this.loadCategories(),
            ]);

            await this.loadVideos();
            hideStatusBar();
            await filterDataPromise;

            // 如果有URL参数指定的频道，设置相应的UI状态
            this.applyUrlParams();
        } catch (error) {
            console.error('加载初始数据失败:', error);
            hideStatusBar();
            showToast('加载数据失败：' + error.message, 'error');
        }
    }

    // 解析URL参数
    parseUrlParams() {
        const urlParams = new URLSearchParams(window.location.search);
        
        // 从URL获取筛选信息
        const category = urlParams.get('category');
        const channelId = urlParams.get('channel_id');
        const channelName = urlParams.get('channel_name');
        
        if (category) {
            this.currentFilters.category = decodeURIComponent(category);
        }

        if (channelId) {
            this.currentFilters.channel = parseInt(channelId);
            
            // 如果有频道名称，显示提示信息
            if (channelName) {
                showToast(`已筛选频道：${decodeURIComponent(channelName)}`, 'info', 4000);
            }
        }
    }

    // 应用URL参数到UI
    applyUrlParams() {
        // 先应用分类筛选（会联动刷新频道列表）
        if (this.currentFilters.category) {
            const filterCategory = document.getElementById('filter-category');
            if (filterCategory) {
                filterCategory.value = this.currentFilters.category;
                this.categoryCombobox.refresh();
                this.handleCategoryChange(this.currentFilters.category, { triggerLoad: false, preserveChannel: true });
            }
        } else {
            // 没有分类筛选时也要确保频道列表已渲染
            this.populateChannelOptions();
        }

        // 设置频道筛选下拉框的值
        if (this.currentFilters.channel) {
            const filterChannel = document.getElementById('filter-channel');
            if (filterChannel) {
                filterChannel.value = this.currentFilters.channel;
                this.channelCombobox.refresh();
            }
        }
    }

    // 更新次排序选项，避免与主排序重复
    updateSecondarySortOptions() {
        const secondarySort = document.getElementById('secondary-sort');
        if (!secondarySort) return;
        
        const primarySortValue = this.currentFilters.primarySort;
        const options = secondarySort.querySelectorAll('option');
        
        // 重置所有选项
        options.forEach(option => {
            option.disabled = false;
            option.style.display = 'block';
        });
        
        // 禁用与主排序相同的选项
        options.forEach(option => {
            if (option.value === primarySortValue && option.value !== '') {
                option.disabled = true;
                option.style.display = 'none';
            }
        });
        
        // 如果当前次排序与主排序冲突，重置为无
        if (this.currentFilters.secondarySort === primarySortValue) {
            this.currentFilters.secondarySort = '';
            secondarySort.value = '';
            
            const secondaryOrder = document.getElementById('secondary-order');
            if (secondaryOrder) {
                secondaryOrder.disabled = true;
            }
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
    
    handleCategoryChange(selectedCategory, options = {}) {
        const { triggerLoad = true, preserveChannel = false } = options;
        const normalizedCategory = selectedCategory || '';

        const channelSelect = document.getElementById('filter-channel');
        const previousChannel = this.currentFilters.channel;

        this.currentFilters.category = normalizedCategory;

        if (!preserveChannel) {
            this.currentFilters.channel = '';
            if (channelSelect) {
                channelSelect.value = '';
            }
        }

        this.populateChannelOptions();

        if (preserveChannel && previousChannel) {
            const channelValue = String(previousChannel);
            if (channelSelect && Array.from(channelSelect.options).some(option => option.value === channelValue)) {
                channelSelect.value = channelValue;
                this.currentFilters.channel = previousChannel;
            } else {
                this.currentFilters.channel = '';
                if (channelSelect) {
                    channelSelect.value = '';
                }
            }
        }

        this.currentPage = 1;

        if (triggerLoad) {
            this.loadVideos();
        }
    }

    populateCategoryOptions() {
        const select = document.getElementById('filter-category');
        if (!select) return;

        const currentValue = this.currentFilters.category || '';
        select.innerHTML = '<option value="">所有频道分类</option>';

        this.channelCategories.forEach(category => {
            const name = (category && (category.name || category.category || category)) || '';
            if (!name) return;
            const option = document.createElement('option');
            option.value = name;
            const count = category && category.count;
            option.textContent = count != null ? `${name} (${count})` : name;
            select.appendChild(option);
        });

        if (currentValue) {
            select.value = currentValue;
        }
        this.categoryCombobox?.refresh();
    }

    populateChannelOptions() {
        const select = document.getElementById('filter-channel');
        if (!select) return;

        const categoryFilter = this.currentFilters.category || '';
        const previousChannelValue = this.currentFilters.channel ? String(this.currentFilters.channel) : '';

        select.innerHTML = '<option value="">所有频道</option>';

        const filteredChannels = this.allChannels.filter(channel => {
            if (!categoryFilter) return true;
            const channelCategory = channel.category || '';
            return channelCategory === categoryFilter;
        });

        filteredChannels.forEach(channel => {
            const option = document.createElement('option');
            option.value = channel.id;
            option.dataset.category = channel.category || '';
            const videoCount = channel.video_count != null ? channel.video_count : channel.video_total;
            option.dataset.videoCount = String(videoCount || 0);
            const suffix = videoCount != null ? ` (${videoCount}个视频)` : '';
            option.textContent = `${channel.name}${suffix}`;
            select.appendChild(option);
        });

        const hasChannels = filteredChannels.length > 0;
        select.disabled = !hasChannels;
        select.title = hasChannels ? '' : (categoryFilter ? '该分类下暂无频道' : '暂无频道数据');

        if (previousChannelValue && filteredChannels.some(channel => String(channel.id) === previousChannelValue)) {
            select.value = previousChannelValue;
        } else {
            if (categoryFilter) {
                this.currentFilters.channel = '';
            }
        }
        this.channelCombobox?.refresh();
    }

    async loadChannels() {
        try {
            const response = await api.getVideoChannels();
            if (response.success) {
                this.allChannels = response.data.channels || [];
                this.populateChannelOptions();
            }
        } catch (error) {
            console.error('加载频道列表失败:', error);
        }
    }
    
    async loadCategories() {
        try {
            const response = await api.getChannelCategories();
            if (response.success) {
                this.channelCategories = response.data.categories || [];
                this.populateCategoryOptions();
            }
        } catch (error) {
            console.error('加载分类列表失败:', error);
        }
    }
    
    async loadVideos() {
        const requestId = this.videoRequestGate.next();
        const container = document.getElementById('videos-grid');
        container?.classList.add('is-loading');
        container?.setAttribute('aria-busy', 'true');
        try {
            console.log('🔍 [VideoManager] 当前筛选条件:', this.currentFilters);
            
            const params = {
                page: this.currentPage,
                per_page: this.pageSize,
                sort_by: this.currentFilters.sortBy,
                order: this.currentFilters.order
            };
            
            // 添加时间段筛选
            if (this.currentFilters.timePeriod && this.currentFilters.timePeriod !== 'all') {
                params.time_period = this.currentFilters.timePeriod;
            }
            
            console.log('📝 [VideoManager] API请求参数:', params);
            
            // 添加筛选条件
            if (this.currentFilters.search) {
                params.q = this.currentFilters.search;
            }
            const hasChannelFilter = Boolean(this.currentFilters.channel);

            if (hasChannelFilter) {
                params.channel_id = this.currentFilters.channel;
            }

            // 频道已选中时忽略分类参数，避免后端按照视频分类再次过滤
            if (this.currentFilters.category && !hasChannelFilter) {
                params.category = this.currentFilters.category;
            }

            console.log('🧮 [VideoManager] 最终请求参数:', params);
            
            // 根据是否有搜索条件选择API方法
            const response = this.currentFilters.search 
                ? await api.searchVideos(params)
                : await api.getVideos(params);

            if (!this.videoRequestGate.isCurrent(requestId)) return;
            
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
            if (!this.videoRequestGate.isCurrent(requestId)) return;
            console.error('加载视频列表失败:', error);
            this.renderErrorState();
            showToast('加载视频列表失败：' + error.message, 'error');
        } finally {
            if (this.videoRequestGate.isCurrent(requestId)) {
                container?.classList.remove('is-loading');
                container?.setAttribute('aria-busy', 'false');
            }
        }
    }
    
    async loadFavoriteStatus(videoIds) {
        try {
            const response = await api.batchCheckFavorites(videoIds);
            if (response.success) {
                const statusMap = response.data.favorites_status;
                for (const [videoId, isFavorited] of Object.entries(statusMap)) {
                    this.favoriteStatus.set(videoId, isFavorited);
                }
                
                // 更新UI中的收藏按钮状态
                this.updateFavoriteButtons();
            }
        } catch (error) {
            console.error('加载收藏状态失败:', error);
        }
    }
    
    updateFavoriteButtons() {
        const favoriteButtons = document.querySelectorAll('.favorite-btn');
        favoriteButtons.forEach(button => {
            const videoId = button.getAttribute('data-video-id');
            const isFavorited = this.favoriteStatus.get(videoId);
            
            if (isFavorited) {
                button.classList.remove('bg-pink-500/90', 'hover:bg-pink-600');
                button.classList.add('bg-red-500/90', 'hover:bg-red-600');
                button.querySelector('i').classList.remove('far');
                button.querySelector('i').classList.add('fas');
                button.title = '取消收藏';
            } else {
                button.classList.remove('bg-red-500/90', 'hover:bg-red-600');
                button.classList.add('bg-pink-500/90', 'hover:bg-pink-600');
                button.querySelector('i').classList.remove('fas');
                button.querySelector('i').classList.add('far');
                button.title = '收藏视频';
            }
        });
    }
    
    async toggleFavorite(videoId, videoTitle, buttonElement) {
        try {
            const isFavorited = this.favoriteStatus.get(videoId);
            
            if (isFavorited) {
                // 移除收藏
                const response = await api.removeFavorite(videoId);
                if (response.success) {
                    this.favoriteStatus.set(videoId, false);
                    showToast('已取消收藏', 'success');
                    this.updateButtonState(buttonElement, false);
                } else {
                    showToast('取消收藏失败：' + response.message, 'error');
                }
            } else {
                // 添加收藏 - 从当前视频列表中找到完整的视频数据
                const videoData = this.findVideoDataById(videoId);
                console.log('找到的视频数据:', videoData);
                if (videoData) {
                    // 列表使用轻量卡片载荷；收藏时按需补取完整描述等冷字段。
                    let detailData = {};
                    if (!Object.prototype.hasOwnProperty.call(videoData, 'description')) {
                        const detailResponse = await api.getVideo(videoData.id);
                        if (!detailResponse.success) throw new Error(detailResponse.message);
                        detailData = detailResponse.data.video || {};
                    }
                    const favoriteData = VideoPageUtils.buildFavoritePayload(videoData, detailData);
                    
                    console.log('发送收藏数据:', favoriteData);
                    const response = await api.addFavorite(favoriteData);
                    console.log('收藏API响应:', response);
                    
                    if (response.success) {
                        this.favoriteStatus.set(videoId, true);
                        showToast(`已收藏视频: ${videoTitle}`, 'success');
                        this.updateButtonState(buttonElement, true);
                    } else {
                        showToast('收藏失败：' + response.message, 'error');
                    }
                } else {
                    showToast('无法找到视频数据', 'error');
                }
            }
        } catch (error) {
            console.error('切换收藏状态失败:', error);
            showToast('操作失败：' + error.message, 'error');
        }
    }
    
    updateButtonState(buttonElement, isFavorited) {
        if (isFavorited) {
            buttonElement.classList.remove('bg-pink-500/90', 'hover:bg-pink-600');
            buttonElement.classList.add('bg-red-500/90', 'hover:bg-red-600');
            buttonElement.querySelector('i').classList.remove('far');
            buttonElement.querySelector('i').classList.add('fas');
            buttonElement.title = '取消收藏';
        } else {
            buttonElement.classList.remove('bg-red-500/90', 'hover:bg-red-600');
            buttonElement.classList.add('bg-pink-500/90', 'hover:bg-pink-600');
            buttonElement.querySelector('i').classList.remove('fas');
            buttonElement.querySelector('i').classList.add('far');
            buttonElement.title = '收藏视频';
        }
    }
    
    findVideoDataById(videoId) {
        // 从当前缓存的视频数据中查找
        if (this.currentVideos && this.currentVideos.length > 0) {
            return this.currentVideos.find(video => video.video_id === videoId);
        }
        return null;
    }
    
    renderVideos(videos) {
        const container = document.getElementById('videos-grid');
        if (!container) return;
        
        // 缓存当前视频数据
        this.currentVideos = videos;
        
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
                ${videos.map((video, index) => this.renderVideoCard(video, index)).join('')}
            </div>
        `;
        
        // 加载收藏状态
        const videoIds = videos.map(video => video.video_id);
        this.loadFavoriteStatus(videoIds);
    }
    
    renderVideoCard(video, index = 0) {
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
                         width="480"
                         height="270"
                         loading="${index < 4 ? 'eager' : 'lazy'}"
                         decoding="async"
                         fetchpriority="${index < 4 ? 'high' : 'low'}"
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
                    <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center gap-3">
                        <button onclick="videoManager.showVideoDetail(${video.id})" 
                                class="bg-white/90 hover:bg-white text-gray-800 p-2.5 rounded-full transition-colors" 
                                title="查看详情">
                            <i class="fas fa-eye"></i>
                        </button>
                        <button onclick="videoManager.toggleFavorite('${video.video_id}', '${video.title.replace(/'/g, "\\'")}', this)"
                                class="bg-pink-500/90 hover:bg-pink-600 text-white p-2.5 rounded-full transition-colors favorite-btn"
                                data-video-id="${video.video_id}"
                                title="收藏视频">
                            <i class="far fa-heart"></i>
                        </button>
                        <a href="${video.url}" target="_blank" 
                           class="bg-red-600/90 hover:bg-red-600 text-white p-2.5 rounded-full transition-colors" 
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
    const timePeriod = document.getElementById('time-period');
    const sortRule = document.getElementById('sort-rule');
    
    if (searchInput) searchInput.value = '';
    if (channelFilter) channelFilter.value = '';
    if (categoryFilter) categoryFilter.value = '';
    if (timePeriod) timePeriod.value = '7';  // 默认最近7天
    if (sortRule) sortRule.value = 'published_at:desc';  // 默认最新发布
    
    // 重置过滤器并重新加载
    videoManager.currentFilters = {
        search: '',
        channel: '',
        category: '',
        timePeriod: '7',
        sortBy: 'published_at',
        order: 'desc'
    };
    videoManager.currentPage = 1;
    videoManager.categoryCombobox?.refresh();
    // 重新渲染频道选项，恢复“所有频道”视图
    videoManager.handleCategoryChange('', { triggerLoad: false });
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

async function cleanShortVideos() {
    if (!confirm('确定要清理所有短视频吗？\n\n这将删除所有时长小于60秒的视频记录，此操作不可恢复！')) {
        return;
    }
    
    try {
        showStatusBar('正在清理短视频...');
        
        const response = await api.cleanShortVideos();
        
        if (response.success) {
            showToast(response.message, 'success');
            // 刷新视频列表
            setTimeout(() => {
                videoManager.loadVideos();
            }, 1000);
        } else {
            showToast('清理失败: ' + response.message, 'error');
        }
        
    } catch (error) {
        console.error('清理短视频失败:', error);
        showToast('清理短视频失败: ' + error.message, 'error');
    } finally {
        hideStatusBar();
    }
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
    const useOptimization = document.getElementById('use-optimization').checked;
    const maxWorkers = document.getElementById('max-workers').value;
    const progressDiv = document.getElementById('sync-progress');
    const resultDiv = document.getElementById('sync-result');
    const startBtn = document.getElementById('start-sync-btn');
    
    // 显示进度条
    progressDiv.classList.remove('hidden');
    resultDiv.classList.add('hidden');
    startBtn.disabled = true;
    
    // 更新进度文本
    const progressText = progressDiv.querySelector('.text-sm');
    if (progressText) {
        progressText.textContent = useOptimization ? 
            `正在使用优化同步（${maxWorkers}线程）...` : 
            '正在使用传统同步...';
    }
    
    const startTime = Date.now();
    
    try {
        const syncData = {
            days_back: parseInt(daysBack),
            use_optimization: useOptimization
        };
        
        if (useOptimization) {
            syncData.max_workers = parseInt(maxWorkers);
        }
        
        const response = await api.syncVideos(syncData);
        
        if (response.success) {
            const endTime = Date.now();
            const duration = (endTime - startTime) / 1000;
            
            // 显示成功结果
            let resultHtml = `
                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                    <div class="flex items-center gap-2 text-green-800 mb-3">
                        <i class="fas fa-check-circle"></i>
                        <span class="font-medium">同步完成！</span>
                        ${response.from_cache ? '<span class="ml-2 inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800"><i class="fas fa-bolt mr-1"></i>缓存加速</span>' : ''}
                    </div>
                    <div class="grid grid-cols-2 gap-4 mb-3">
                        <div class="text-sm">
                            <div class="text-gray-600">处理频道</div>
                            <div class="font-semibold text-green-700">${response.data.processed} 个</div>
                        </div>
                        <div class="text-sm">
                            <div class="text-gray-600">新增视频</div>
                            <div class="font-semibold text-blue-700">${response.data.new_videos} 个</div>
                        </div>
                        <div class="text-sm">
                            <div class="text-gray-600">更新视频</div>
                            <div class="font-semibold text-orange-700">${response.data.updated_videos} 个</div>
                        </div>
                        <div class="text-sm">
                            <div class="text-gray-600">用时</div>
                            <div class="font-semibold text-purple-700">${duration.toFixed(1)}s</div>
                        </div>
                    </div>`;
            
            // 显示优化信息
            if (useOptimization && response.data.cache_hits > 0) {
                resultHtml += `
                    <div class="border-t border-green-200 pt-2 mt-2">
                        <div class="text-xs text-green-600 flex items-center gap-2">
                            <i class="fas fa-rocket"></i>
                            <span>优化效果：缓存命中 ${response.data.cache_hits} 次，并行处理 ${maxWorkers} 线程</span>
                        </div>
                    </div>`;
            }
            
            if (response.data.errors > 0) {
                resultHtml += `
                    <div class="border-t border-green-200 pt-2 mt-2">
                        <div class="text-sm text-red-600">
                            <i class="fas fa-exclamation-triangle mr-1"></i>
                            错误: ${response.data.errors} 个
                        </div>
                    </div>`;
            }
            
            resultHtml += '</div>';
            resultDiv.innerHTML = resultHtml;
            
            // 刷新视频列表
            setTimeout(() => {
                videoManager.loadVideos();
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


// 优化选项控制
function toggleOptimizationSettings() {
    const checkbox = document.getElementById('use-optimization');
    const settings = document.getElementById('optimization-settings');
    
    if (checkbox && settings) {
        if (checkbox.checked) {
            settings.style.opacity = '1';
            settings.style.pointerEvents = 'auto';
        } else {
            settings.style.opacity = '0.5';
            settings.style.pointerEvents = 'none';
        }
    }
}

// 监听优化选项变化
document.addEventListener('DOMContentLoaded', () => {
    const optimizationCheckbox = document.getElementById('use-optimization');
    if (optimizationCheckbox) {
        optimizationCheckbox.addEventListener('change', toggleOptimizationSettings);
        // 初始化状态
        toggleOptimizationSettings();
    }
});

// 初始化视频管理器
let videoManager;
document.addEventListener('DOMContentLoaded', async () => {
    // 检查认证状态
    console.log('🔍 [Videos] 开始认证检查...');
    const token = localStorage.getItem('access_token');
    if (!token) {
        console.log('❌ [Videos] 没有Token，重定向到登录页');
        window.location.href = '/login.html';
        return;
    }
    
    try {
        // 验证token
        const response = await api.verifyToken();
        if (response && response.success && response.data && response.data.user) {
            console.log('✅ [Videos] Token验证成功，用户:', response.data.user.username);
            // 更新本地用户信息
            localStorage.setItem('user_info', JSON.stringify(response.data.user));
            window.displayUserInfo();
            
            // 初始化视频管理器
            videoManager = new VideoManager();
        } else {
            console.log('❌ [Videos] Token验证失败，响应格式:', response);
            api.clearAuth();
            window.location.href = 'login.html';
        }
    } catch (error) {
        console.error('❌ [Videos] 认证验证异常:', error);
        // 如果是401错误，说明token无效
        if (error.status === 401) {
            console.log('❌ [Videos] Token已过期，清除认证信息');
        }
        api.clearAuth();
        window.location.href = '/login.html';
    }
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
