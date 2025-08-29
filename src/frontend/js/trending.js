document.addEventListener('DOMContentLoaded', async () => {
    // 认证检查
    const token = localStorage.getItem('access_token');
    if (!token) {
        window.location.href = 'login.html';
        return;
    }

    try {
        const response = await api.verifyToken();
        if (response && response.success) {
            window.trendingSearchInstance = new TrendingSearch();
        } else {
            api.clearAuth();
            window.location.href = 'login.html';
        }
    } catch (error) {
        api.clearAuth();
        window.location.href = 'login.html';
    }
});

class TrendingSearch {
    constructor() {
        this.form = document.getElementById('search-form');
        this.loadingIndicator = document.getElementById('loading-indicator');
        this.videosList = document.getElementById('videos-list');
        this.initialState = document.getElementById('initial-state');
        
        this.setupEventListeners();
        this.setupMultiSelect();
    }

    setupEventListeners() {
        this.form.addEventListener('submit', (e) => {
            e.preventDefault();
            this.performSearch();
        });
    }

    setupMultiSelect() {
        const toggle = document.getElementById('subscriber-range-toggle');
        const dropdown = document.getElementById('subscriber-range-dropdown');
        const label = document.getElementById('subscriber-range-label');
        const checkboxes = document.querySelectorAll('input[name="subscriber_ranges"]');
        
        // 切换下拉菜单
        toggle.addEventListener('click', () => {
            dropdown.classList.toggle('hidden');
        });

        // 点击外部关闭下拉菜单
        document.addEventListener('click', (e) => {
            if (!toggle.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });

        // 处理复选框变化
        checkboxes.forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                this.handleSubscriberRangeChange(e.target);
                this.updateSubscriberRangeLabel();
            });
        });

        // 初始化标签
        this.updateSubscriberRangeLabel();

        // 清除选择按钮
        const clearButton = document.getElementById('clear-subscriber-selection');
        if (clearButton) {
            clearButton.addEventListener('click', () => {
                // 清除所有选择，默认选择"所有博主"
                checkboxes.forEach(checkbox => {
                    checkbox.checked = checkbox.value === 'all';
                });
                this.updateSubscriberRangeLabel();
            });
        }
    }

    updateSubscriberRangeLabel() {
        const checkboxes = document.querySelectorAll('input[name="subscriber_ranges"]:checked');
        const label = document.getElementById('subscriber-range-label');
        const toggle = document.getElementById('subscriber-range-toggle');
        
        if (checkboxes.length === 0) {
            label.textContent = '博主级别';
            toggle.classList.remove('border-primary-500', 'ring-2', 'ring-primary-200');
        } else if (checkboxes.length === 1) {
            const checkbox = checkboxes[0];
            const labelText = checkbox.parentElement.querySelector('span').textContent;
            label.textContent = labelText;
            
            // 如果不是"所有博主"，高亮显示
            if (checkbox.value !== 'all') {
                toggle.classList.add('border-primary-500', 'ring-2', 'ring-primary-200');
            } else {
                toggle.classList.remove('border-primary-500', 'ring-2', 'ring-primary-200');
            }
        } else {
            label.textContent = `已选择 ${checkboxes.length} 个级别`;
            toggle.classList.add('border-primary-500', 'ring-2', 'ring-primary-200');
        }
    }

    handleSubscriberRangeChange(clickedCheckbox) {
        const allCheckbox = document.querySelector('input[name="subscriber_ranges"][value="all"]');
        const otherCheckboxes = document.querySelectorAll('input[name="subscriber_ranges"]:not([value="all"])');
        
        // 如果点击的是"所有博主"
        if (clickedCheckbox.value === 'all') {
            if (clickedCheckbox.checked) {
                // 选中"所有博主"时，取消其他所有选项
                otherCheckboxes.forEach(cb => {
                    cb.checked = false;
                });
            }
        } else {
            // 点击的是其他选项
            if (clickedCheckbox.checked) {
                // 选中其他选项时，取消"所有博主"
                allCheckbox.checked = false;
            } else {
                // 取消其他选项时，检查是否还有其他选项被选中
                const hasOtherSelected = Array.from(otherCheckboxes).some(cb => cb.checked);
                if (!hasOtherSelected) {
                    // 如果没有任何其他选项被选中，自动选中"所有博主"
                    allCheckbox.checked = true;
                }
            }
        }
    }

    async performSearch() {
        const formData = new FormData(this.form);
        
        // 获取选中的博主级别
        const selectedRanges = Array.from(document.querySelectorAll('input[name="subscriber_ranges"]:checked'))
            .map(checkbox => checkbox.value);
        
        const params = {
            keywords: formData.get('keywords'),
            time_range: formData.get('time_range'),
            video_type: formData.get('video_type'),
            subscriber_ranges: selectedRanges, // 改为数组
            max_results: formData.get('max_results'),
            order: 'viewCount', // 默认按播放量排序
        };

        if (!params.keywords || params.keywords.trim() === '') {
            alert('请输入搜索关键词');
            return;
        }

        this.showLoading();

        try {
            const response = await api.searchTrending(params);
            if (response.success) {
                this.renderVideos(response.data.videos);
            } else {
                this.showError(response.message || '搜索失败');
            }
        } catch (error) {
            this.showError(error.message || '发生未知错误');
        } finally {
            this.hideLoading();
        }
    }

    renderVideos(videos) {
        this.currentVideos = videos; // 保存当前视频列表供收藏功能使用
        
        if (videos.length === 0) {
            this.videosList.innerHTML = `<div class="md:col-span-4 flex flex-col items-center justify-center py-12">
                    <i class="fas fa-video-slash text-4xl text-gray-300 mb-4"></i>
                    <p class="text-gray-500 text-lg mb-2">未找到相关视频</p>
                    <p class="text-gray-400 text-sm">请尝试更换关键词或筛选条件</p>
                </div>`;
            return;
        }

        this.videosList.innerHTML = videos.map(video => this.createVideoCard(video)).join('');
        
        // 异步检查收藏状态
        this.updateFavoriteStatus(videos);
    }

    // 异步更新收藏状态
    async updateFavoriteStatus(videos) {
        try {
            const videoIds = videos.map(v => v.video_id);
            const result = await api.batchCheckFavorites(videoIds);
            const favoritesStatus = result.data.favorites_status;
            
            // 更新按钮状态
            videos.forEach(video => {
                const isFavorited = favoritesStatus[video.video_id];
                const button = document.querySelector(`button[onclick*="${video.video_id}"]`);
                if (button) {
                    if (isFavorited) {
                        button.className = 'absolute top-2 right-2 p-2 rounded-full transition-all duration-200 bg-red-500 text-white';
                        button.title = '取消收藏';
                    } else {
                        button.className = 'absolute top-2 right-2 p-2 rounded-full transition-all duration-200 bg-white/80 text-gray-600 hover:bg-white';
                        button.title = '添加收藏';
                    }
                }
            });
        } catch (error) {
            console.error('获取收藏状态失败:', error);
        }
    }

    createVideoCard(video) {
        const publishedDate = new Date(video.published_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
        
        // 格式化统计数据
        const viewCount = Utils.formatNumber(video.view_count || 0);
        const likeCount = Utils.formatNumber(video.like_count || 0);
        const commentCount = Utils.formatNumber(video.comment_count || 0);
        const subscriberCount = Utils.formatNumber(video.channel_subscriber_count || 0);
        
        // 处理视频时长
        const duration = this.formatDuration(video.duration || '');
        
        // 计算频道年龄
        const channelAge = Utils.calculateChannelAge(video.channel_published_at);
        const channelCreatedDate = Utils.formatChannelCreatedDate(video.channel_published_at);
        
        // 计算热度评分
        const hotScore = Utils.calculateHotScore(video.view_count, video.like_count, video.comment_count);
        const hotLevel = Utils.getHotScoreLevel(hotScore);
        
        // 收藏状态将异步检查
        const isFavorited = false; // 默认未收藏，稍后异步更新
        
        return `
            <div class="bg-white rounded-xl shadow-soft border border-gray-100 overflow-hidden hover:shadow-medium transition-all duration-300 hover:-translate-y-1 group">
                <div class="relative">
                    <a href="https://www.youtube.com/watch?v=${video.video_id}" target="_blank" class="block">
                        <img src="${video.thumbnail_url}" alt="${video.title}" class="w-full h-48 object-cover">
                        ${duration ? `<div class="absolute bottom-2 right-2 bg-black/75 text-white text-xs px-2 py-1 rounded">${duration}</div>` : ''}
                        <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center">
                            <i class="fab fa-youtube text-white text-5xl"></i>
                        </div>
                    </a>
                    
                    <!-- 热度评分 -->
                    <div class="absolute top-2 left-2 ${hotLevel.bgColor} ${hotLevel.color} px-2 py-1 rounded-md text-xs font-medium">
                        ${hotLevel.level} ${Math.round(hotScore)}
                    </div>
                    
                    <!-- 收藏按钮 -->
                    <button onclick="event.stopPropagation(); toggleFavorite('${video.video_id}', this)" 
                            class="absolute top-2 right-2 p-2 rounded-full transition-all duration-200 ${isFavorited ? 'bg-red-500 text-white' : 'bg-white/80 text-gray-600 hover:bg-white'}" 
                            title="${isFavorited ? '取消收藏' : '添加收藏'}">
                        <i class="fas fa-heart text-sm"></i>
                    </button>
                </div>
                <div class="p-4">
                    <h3 class="font-semibold text-gray-800 text-sm line-clamp-2 h-10 mb-3" title="${video.title}">${video.title}</h3>
                    
                    <!-- 频道信息 -->
                    <div class="flex items-center gap-2 mb-3">
                        <img src="${video.channel_thumbnail_url || 'https://via.placeholder.com/32x32/6b7280/ffffff?text=' + encodeURIComponent(video.channel_title.charAt(0))}" 
                             class="w-6 h-6 rounded-full object-cover bg-gray-300" 
                             alt="${video.channel_title}">
                        <div class="flex-1 min-w-0">
                            <p class="text-xs font-medium text-gray-700 truncate">${video.channel_title}</p>
                            <p class="text-xs text-gray-500">${subscriberCount} 订阅者</p>
                            ${channelAge ? `<p class="text-xs text-blue-500">创建 ${channelAge}</p>` : ''}
                        </div>
                    </div>
                    
                    <!-- 视频统计数据 -->
                    <div class="grid grid-cols-3 gap-2 text-xs text-gray-600 mb-3">
                        <div class="flex items-center gap-1">
                            <i class="fas fa-eye text-gray-400"></i>
                            <span>${viewCount}</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <i class="fas fa-thumbs-up text-gray-400"></i>
                            <span>${likeCount}</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <i class="fas fa-comments text-gray-400"></i>
                            <span>${commentCount}</span>
                        </div>
                    </div>
                    
                    <!-- 发布时间和频道信息 -->
                    <div class="pt-3 border-t border-gray-100">
                        <p class="text-xs text-gray-400">发布于 ${publishedDate}</p>
                        ${channelAge ? `<p class="text-xs text-gray-400" title="频道创建于 ${channelCreatedDate}">频道创建 ${channelAge}</p>` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    // 处理收藏功能
    async handleFavoriteToggle(videoId, button) {
        const video = this.currentVideos?.find(v => v.video_id === videoId);
        if (!video) return;

        // 显示加载状态
        const originalHTML = button.innerHTML;
        button.innerHTML = '<i class="fas fa-spinner fa-spin text-sm"></i>';
        button.disabled = true;
        
        try {
            // 检查当前收藏状态
            const checkResult = await api.checkFavorite(videoId);
            const isFavorited = checkResult.data.is_favorited;
            
            if (isFavorited) {
                // 移除收藏
                await api.removeFavorite(videoId);
                button.className = 'absolute top-2 right-2 p-2 rounded-full transition-all duration-200 bg-white/80 text-gray-600 hover:bg-white';
                button.title = '添加收藏';
                this.showToast('已从收藏中移除', 'info');
            } else {
                // 添加收藏
                await api.addFavorite(video);
                button.className = 'absolute top-2 right-2 p-2 rounded-full transition-all duration-200 bg-red-500 text-white';
                button.title = '取消收藏';
                this.showToast('已添加到收藏', 'success');
            }
        } catch (error) {
            console.error('收藏操作失败:', error);
            this.showToast('操作失败，请稍后重试', 'error');
        } finally {
            // 恢复按钮状态
            button.innerHTML = originalHTML;
            button.disabled = false;
        }
    }

    // 简单的提示功能
    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `fixed top-4 right-4 px-4 py-2 rounded-lg text-white text-sm z-50 transform transition-all duration-300 translate-x-full
            ${type === 'success' ? 'bg-green-500' : type === 'error' ? 'bg-red-500' : 'bg-blue-500'}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => toast.classList.remove('translate-x-full'), 100);
        setTimeout(() => {
            toast.classList.add('translate-x-full');
            setTimeout(() => document.body.removeChild(toast), 300);
        }, 2000);
    }

    // 格式化视频时长
    formatDuration(duration) {
        if (!duration) return '';
        
        // YouTube API返回的时长格式如 "PT4M13S" 或 "PT1H2M3S"
        const match = duration.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
        if (!match) return '';
        
        const hours = parseInt(match[1] || 0);
        const minutes = parseInt(match[2] || 0);
        const seconds = parseInt(match[3] || 0);
        
        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${seconds.toString().padStart(2, '0')}`;
        }
    }

    showLoading() {
        this.initialState.classList.add('hidden');
        this.loadingIndicator.classList.remove('hidden');
        this.videosList.innerHTML = '';
    }

    hideLoading() {
        this.loadingIndicator.classList.add('hidden');
    }

    showError(message) {
        this.videosList.innerHTML = `<div class="md:col-span-4 flex flex-col items-center justify-center py-12">
                <i class="fas fa-exclamation-triangle text-4xl text-red-300 mb-4"></i>
                <p class="text-red-500 text-lg mb-2">发生错误</p>
                <p class="text-gray-400 text-sm">${message}</p>
            </div>`;
    }
}

// 全局收藏切换函数
window.toggleFavorite = function(videoId, button) {
    const trendingSearch = window.trendingSearchInstance;
    if (trendingSearch) {
        trendingSearch.handleFavoriteToggle(videoId, button);
        updateFavoritesCount();
    }
};

// 更新收藏数量显示
async function updateFavoritesCount() {
    try {
        const result = await api.getFavoriteStats();
        const count = result.data.total_count;
        const countElement = document.getElementById('favorites-count');
        if (countElement) {
            countElement.textContent = count;
        }
    } catch (error) {
        console.error('获取收藏数量失败:', error);
        const countElement = document.getElementById('favorites-count');
        if (countElement) {
            countElement.textContent = '?';
        }
    }
}

// 显示收藏列表
window.showFavorites = async function() {
    const trendingSearch = window.trendingSearchInstance;
    
    try {
        // 显示加载状态
        trendingSearch.videosList.innerHTML = `<div class="md:col-span-3 flex flex-col items-center justify-center py-12">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mb-4"></div>
                <p class="text-gray-500">正在加载收藏列表...</p>
            </div>`;
        
        const result = await api.getFavorites({ per_page: 100 });
        const favorites = result.data.favorites;
        
        if (favorites.length === 0) {
            // 隐藏初始状态提示
            trendingSearch.initialState.classList.add('hidden');
            trendingSearch.videosList.innerHTML = `<div class="md:col-span-3 flex flex-col items-center justify-center py-12">
                    <i class="fas fa-heart text-4xl text-gray-300 mb-4"></i>
                    <p class="text-gray-500 text-lg mb-2">暂无收藏视频</p>
                    <p class="text-gray-400 text-sm">收藏感兴趣的视频以便后续研究</p>
                    <button onclick="hideFavorites()" class="mt-4 px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors">
                        返回搜索
                    </button>
                </div>`;
            return;
        }
        
        // 隐藏初始状态提示
        trendingSearch.initialState.classList.add('hidden');
        
        // 显示收藏的视频
        trendingSearch.currentVideos = favorites;
        trendingSearch.videosList.innerHTML = `
            <div class="md:col-span-3 mb-6">
                <div class="flex items-center justify-between p-4 bg-blue-50 rounded-lg border border-blue-200">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-heart text-blue-600"></i>
                        <h3 class="font-semibold text-blue-800">我的收藏 (${favorites.length}个视频)</h3>
                    </div>
                    <div class="flex gap-2">
                        <button onclick="hideFavorites()" class="px-3 py-1 text-sm bg-white text-blue-600 border border-blue-300 rounded hover:bg-blue-50 transition-colors">
                            返回搜索
                        </button>
                        <button onclick="clearAllFavorites()" class="px-3 py-1 text-sm bg-red-500 text-white rounded hover:bg-red-600 transition-colors">
                            清空收藏
                        </button>
                    </div>
                </div>
            </div>
            ${favorites.map(video => trendingSearch.createVideoCard(video)).join('')}
        `;
        
        // 更新收藏状态（全部应该是已收藏）
        trendingSearch.updateFavoriteStatus(favorites);
        
    } catch (error) {
        console.error('获取收藏列表失败:', error);
        // 隐藏初始状态提示
        trendingSearch.initialState.classList.add('hidden');
        trendingSearch.videosList.innerHTML = `<div class="md:col-span-3 flex flex-col items-center justify-center py-12">
                <i class="fas fa-exclamation-triangle text-4xl text-red-300 mb-4"></i>
                <p class="text-red-500 text-lg mb-2">加载失败</p>
                <p class="text-gray-400 text-sm">请稍后重试</p>
                <button onclick="hideFavorites()" class="mt-4 px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors">
                    返回搜索
                </button>
            </div>`;
    }
};

// 隐藏收藏列表
window.hideFavorites = function() {
    const trendingSearch = window.trendingSearchInstance;
    trendingSearch.initialState.classList.remove('hidden');
    trendingSearch.videosList.innerHTML = '';
};

// 清空所有收藏
window.clearAllFavorites = async function() {
    if (confirm('确定要清空所有收藏吗？此操作不可撤销。')) {
        try {
            await api.clearAllFavorites();
            updateFavoritesCount();
            showFavorites(); // 刷新显示
            
            const trendingSearch = window.trendingSearchInstance;
            if (trendingSearch) {
                trendingSearch.showToast('已清空所有收藏', 'info');
            }
        } catch (error) {
            console.error('清空收藏失败:', error);
            const trendingSearch = window.trendingSearchInstance;
            if (trendingSearch) {
                trendingSearch.showToast('清空失败，请稍后重试', 'error');
            }
        }
    }
};

// 页面加载时更新收藏数量
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(updateFavoritesCount, 100);
});
