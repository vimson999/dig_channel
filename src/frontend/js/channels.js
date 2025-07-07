// 频道管理模块
class Channels {
    constructor() {
        this.currentPage = 1;
        this.pageSize = CONFIG.PAGINATION.DEFAULT_PAGE_SIZE;
        this.searchKeyword = '';
        this.selectedCategory = '';
        this.channels = [];
        this.categories = [];
        this.totalPages = 0;
        
        this.init();
    }

    // 初始化
    init() {
        this.bindEvents();
        this.loadCategories();
        this.loadChannels();
    }

    // 绑定事件
    bindEvents() {
        // 搜索输入框回车事件
        const searchInput = document.getElementById('channel-search');
        if (searchInput) {
            searchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.searchChannels();
                }
            });
        }

        // 分类筛选变化事件
        const categoryFilter = document.getElementById('category-filter');
        if (categoryFilter) {
            categoryFilter.addEventListener('change', (e) => {
                this.selectedCategory = e.target.value;
                this.searchChannels();
            });
        }
    }

    // 加载分类列表
    async loadCategories() {
        try {
            const response = await api.getChannelCategories();
            this.categories = response.data.categories;
            this.renderCategoryFilter();
        } catch (error) {
            console.error('加载分类失败:', error);
        }
    }

    // 渲染分类筛选器
    renderCategoryFilter() {
        const categoryFilter = document.getElementById('category-filter');
        if (!categoryFilter) return;

        const options = this.categories.map(category => 
            `<option value="${category.name}">${category.name} (${category.count})</option>`
        ).join('');

        categoryFilter.innerHTML = `
            <option value="">所有分类</option>
            ${options}
        `;
    }

    // 加载频道列表
    async loadChannels() {
        try {
            this.showLoading('正在加载频道列表...');

            const params = {
                page: this.currentPage,
                per_page: this.pageSize,
                active_only: true
            };

            if (this.searchKeyword) {
                params.search = this.searchKeyword;
            }

            if (this.selectedCategory) {
                params.category = this.selectedCategory;
            }

            const response = await api.getChannels(params);
            
            this.channels = response.data.channels;
            this.totalPages = response.data.pagination.pages;
            
            this.renderChannelsList();
            this.renderPagination(response.data.pagination);
            
            this.hideLoading();

        } catch (error) {
            console.error('加载频道列表失败:', error);
            this.hideLoading();
            this.showError('加载频道列表失败');
        }
    }

    // 渲染频道列表
    renderChannelsList() {
        const container = document.getElementById('channels-list');
        if (!container) return;

        if (this.channels.length === 0) {
            container.innerHTML = this.getEmptyState();
            return;
        }

        const html = this.channels.map(channel => this.renderChannelCard(channel)).join('');
        container.innerHTML = html;
    }

    // 渲染单个频道卡片
    renderChannelCard(channel) {
        const statusClass = channel.is_active ? 'status-active' : 'status-inactive';
        const statusText = channel.is_active ? '活跃' : '停用';
        const statusIcon = channel.is_active ? 'check-circle' : 'x-circle';

        return `
            <div class="channel-card" data-channel-id="${channel.id}">
                <div class="channel-header">
                    <div class="channel-info">
                        <div class="channel-name">${channel.name}</div>
                        <div class="channel-id">${channel.channel_id}</div>
                        <div class="small text-muted">
                            <i class="bi bi-tag"></i> ${channel.category || '未分类'} | 
                            CPM: $${channel.cpm} | 
                            <span class="${statusClass}">
                                <i class="bi bi-${statusIcon}"></i> ${statusText}
                            </span>
                        </div>
                    </div>
                    <div class="channel-actions">
                        <button class="btn btn-sm btn-outline-primary" onclick="syncChannelData(${channel.id})" title="同步数据">
                            <i class="bi bi-arrow-clockwise"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-info" onclick="viewChannelStatistics(${channel.id})" title="查看统计">
                            <i class="bi bi-graph-up"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="editChannel(${channel.id})" title="编辑">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteChannel(${channel.id})" title="删除">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="channel-stats" id="channel-stats-${channel.id}">
                    <div class="text-muted">加载统计数据中...</div>
                </div>
            </div>
        `;
    }

    // 获取空状态HTML
    getEmptyState() {
        const message = this.searchKeyword || this.selectedCategory ? 
            '未找到匹配的频道' : '暂无频道数据';
        
        return `
            <div class="empty-state">
                <i class="bi bi-collection"></i>
                <div>${message}</div>
                <button class="btn btn-primary mt-3" onclick="openAddChannelModal()">
                    <i class="bi bi-plus-circle"></i> 添加频道
                </button>
            </div>
        `;
    }

    // 渲染分页
    renderPagination(pagination) {
        const container = document.querySelector('#channels-pagination .pagination');
        if (!container) return;

        if (pagination.pages <= 1) {
            container.innerHTML = '';
            return;
        }

        let html = '';

        // 上一页
        html += `
            <li class="page-item ${!pagination.has_prev ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="channelsInstance.goToPage(${pagination.page - 1})">
                    <i class="bi bi-chevron-left"></i>
                </a>
            </li>
        `;

        // 页码
        const startPage = Math.max(1, pagination.page - 2);
        const endPage = Math.min(pagination.pages, pagination.page + 2);

        if (startPage > 1) {
            html += `<li class="page-item"><a class="page-link" href="#" onclick="channelsInstance.goToPage(1)">1</a></li>`;
            if (startPage > 2) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            html += `
                <li class="page-item ${i === pagination.page ? 'active' : ''}">
                    <a class="page-link" href="#" onclick="channelsInstance.goToPage(${i})">${i}</a>
                </li>
            `;
        }

        if (endPage < pagination.pages) {
            if (endPage < pagination.pages - 1) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
            html += `<li class="page-item"><a class="page-link" href="#" onclick="channelsInstance.goToPage(${pagination.pages})">${pagination.pages}</a></li>`;
        }

        // 下一页
        html += `
            <li class="page-item ${!pagination.has_next ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="channelsInstance.goToPage(${pagination.page + 1})">
                    <i class="bi bi-chevron-right"></i>
                </a>
            </li>
        `;

        container.innerHTML = html;
    }

    // 跳转页面
    goToPage(page) {
        if (page < 1 || page > this.totalPages) return;
        
        this.currentPage = page;
        this.loadChannels();
    }

    // 搜索频道
    searchChannels() {
        const searchInput = document.getElementById('channel-search');
        this.searchKeyword = searchInput ? searchInput.value.trim() : '';
        
        this.currentPage = 1; // 重置到第一页
        this.loadChannels();
    }

    // 显示频道详情
    async showChannelDetails(channelId) {
        try {
            const [channelData, statsData] = await Promise.all([
                api.getChannel(channelId),
                api.getChannelStatistics(channelId)
            ]);

            this.renderChannelDetailsModal(channelData.data, statsData.data);
            
        } catch (error) {
            console.error('加载频道详情失败:', error);
            showToast('加载频道详情失败', 'error');
        }
    }

    // 渲染频道详情模态框
    renderChannelDetailsModal(channel, stats) {
        // 创建模态框HTML
        const modalHTML = `
            <div class="modal fade" id="channelDetailsModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">频道详情 - ${channel.name}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            ${this.renderChannelDetailsContent(channel, stats)}
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                            <button type="button" class="btn btn-primary" onclick="editChannel(${channel.id})">编辑频道</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 移除已存在的模态框
        const existingModal = document.getElementById('channelDetailsModal');
        if (existingModal) {
            existingModal.remove();
        }

        // 添加新模态框
        document.body.insertAdjacentHTML('beforeend', modalHTML);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('channelDetailsModal'));
        modal.show();
    }

    // 渲染频道详情内容
    renderChannelDetailsContent(channel, stats) {
        const latestData = stats.latest_data;
        
        return `
            <div class="row">
                <div class="col-md-6">
                    <h6>基本信息</h6>
                    <table class="table table-sm">
                        <tr>
                            <td>频道名称</td>
                            <td>${channel.name}</td>
                        </tr>
                        <tr>
                            <td>频道ID</td>
                            <td><code>${channel.channel_id}</code></td>
                        </tr>
                        <tr>
                            <td>分类</td>
                            <td>${channel.category || '未分类'}</td>
                        </tr>
                        <tr>
                            <td>CPM值</td>
                            <td>$${channel.cpm}</td>
                        </tr>
                        <tr>
                            <td>状态</td>
                            <td>
                                <span class="badge ${channel.is_active ? 'bg-success' : 'bg-secondary'}">
                                    ${channel.is_active ? '活跃' : '停用'}
                                </span>
                            </td>
                        </tr>
                        <tr>
                            <td>创建时间</td>
                            <td>${Utils.formatDate(channel.created_at)}</td>
                        </tr>
                    </table>
                </div>
                <div class="col-md-6">
                    <h6>数据统计</h6>
                    <table class="table table-sm">
                        <tr>
                            <td>数据记录数</td>
                            <td>${Utils.formatNumber(stats.total_records)}</td>
                        </tr>
                        <tr>
                            <td>最早数据</td>
                            <td>${Utils.formatDate(stats.earliest_date)}</td>
                        </tr>
                        <tr>
                            <td>最新数据</td>
                            <td>${Utils.formatDate(stats.latest_date)}</td>
                        </tr>
                        <tr>
                            <td>总收入</td>
                            <td>${Utils.formatCurrency(stats.total_revenue)}</td>
                        </tr>
                    </table>
                </div>
            </div>
            
            ${latestData ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>最新数据 (${Utils.formatDate(latestData.date)})</h6>
                        <div class="row">
                            <div class="col-md-3">
                                <div class="text-center">
                                    <div class="h4 text-primary">${Utils.formatNumber(latestData.subscribers)}</div>
                                    <div class="small text-muted">订阅者</div>
                                    <div class="small ${latestData.subscriber_change >= 0 ? 'text-success' : 'text-danger'}">
                                        ${Utils.formatChange(latestData.subscriber_change)}
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="text-center">
                                    <div class="h4 text-info">${Utils.formatNumber(latestData.total_views)}</div>
                                    <div class="small text-muted">总观看量</div>
                                    <div class="small ${latestData.view_change >= 0 ? 'text-success' : 'text-danger'}">
                                        ${Utils.formatChange(latestData.view_change)}
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="text-center">
                                    <div class="h4 text-warning">${Utils.formatNumber(latestData.video_count)}</div>
                                    <div class="small text-muted">视频数量</div>
                                    <div class="small ${latestData.video_change >= 0 ? 'text-success' : 'text-danger'}">
                                        ${Utils.formatChange(latestData.video_change)}
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="text-center">
                                    <div class="h4 text-success">${Utils.formatCurrency(latestData.estimated_revenue)}</div>
                                    <div class="small text-muted">预估收入</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            ` : ''}
        `;
    }

    // 加载频道统计数据（异步加载到卡片中）
    async loadChannelStats(channelId) {
        try {
            const statsData = await api.getChannelStatistics(channelId);
            const container = document.getElementById(`channel-stats-${channelId}`);
            
            if (container && statsData.data.latest_data) {
                const data = statsData.data.latest_data;
                container.innerHTML = `
                    <div class="stat-item">
                        <div class="stat-value">${Utils.formatNumber(data.subscribers)}</div>
                        <div class="stat-label">订阅者</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${Utils.formatNumber(data.total_views)}</div>
                        <div class="stat-label">观看量</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${Utils.formatNumber(data.video_count)}</div>
                        <div class="stat-label">视频数</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${Utils.formatCurrency(data.estimated_revenue)}</div>
                        <div class="stat-label">预估收入</div>
                    </div>
                `;
            }
        } catch (error) {
            console.error(`加载频道${channelId}统计失败:`, error);
            const container = document.getElementById(`channel-stats-${channelId}`);
            if (container) {
                container.innerHTML = '<div class="text-muted">统计数据加载失败</div>';
            }
        }
    }

    // 刷新频道列表
    refresh() {
        this.loadChannels();
    }

    // 显示加载状态
    showLoading(message = '正在加载...') {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            statusText.textContent = message;
            statusBar.classList.remove('d-none', 'alert-danger');
            statusBar.classList.add('alert-info');
        }
    }

    // 隐藏加载状态
    hideLoading() {
        const statusBar = document.getElementById('status-bar');
        if (statusBar) {
            statusBar.classList.add('d-none');
        }
    }

    // 显示错误
    showError(message) {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            statusText.textContent = message;
            statusBar.classList.remove('d-none', 'alert-info');
            statusBar.classList.add('alert-danger');
            
            setTimeout(() => {
                this.hideLoading();
            }, 3000);
        }
    }
}

// 频道管理相关的全局函数
function loadChannels() {
    if (channelsInstance) {
        channelsInstance.refresh();
    }
}

function searchChannels() {
    if (channelsInstance) {
        channelsInstance.searchChannels();
    }
}

function syncChannelData(channelId) {
    if (confirm('确定要同步此频道的数据吗？')) {
        api.syncChannel(channelId)
            .then(response => {
                showToast('频道数据同步成功', 'success');
                if (channelsInstance) {
                    channelsInstance.loadChannelStats(channelId);
                }
            })
            .catch(error => {
                console.error('同步频道数据失败:', error);
                showToast('同步频道数据失败', 'error');
            });
    }
}

function viewChannelStatistics(channelId) {
    if (channelsInstance) {
        channelsInstance.showChannelDetails(channelId);
    }
}

async function editChannel(channelId) {
    try {
        showLoading('正在加载频道信息...');
        
        // 获取频道详细信息
        const response = await api.getChannel(channelId);
        
        if (response.success) {
            const channel = response.data;
            
            // 填充编辑表单
            document.getElementById('edit-channel-id').value = channel.id;
            document.getElementById('edit-channel-name').value = channel.name || '';
            document.getElementById('edit-channel-channel-id').value = channel.channel_id || '';
            document.getElementById('edit-channel-cpm').value = channel.cpm || '1.00';
            document.getElementById('edit-channel-category').value = channel.category || '';
            document.getElementById('edit-channel-description').value = channel.description || '';
            document.getElementById('edit-channel-active').checked = channel.is_active !== false;
            
            // 显示编辑模态框
            const modal = new bootstrap.Modal(document.getElementById('editChannelModal'));
            modal.show();
            
            hideLoading();
        } else {
            hideLoading();
            showToast('获取频道信息失败: ' + response.message, 'error');
        }
        
    } catch (error) {
        console.error('获取频道信息失败:', error);
        hideLoading();
        showToast('获取频道信息失败', 'error');
    }
}

// 更新频道信息
async function updateChannel() {
    try {
        // 获取表单数据
        const channelId = document.getElementById('edit-channel-id').value;
        const name = document.getElementById('edit-channel-name').value.trim();
        const cpm = parseFloat(document.getElementById('edit-channel-cpm').value) || 1.00;
        const category = document.getElementById('edit-channel-category').value.trim();
        const description = document.getElementById('edit-channel-description').value.trim();
        const isActive = document.getElementById('edit-channel-active').checked;
        
        // 验证必填字段
        if (!name) {
            showToast('频道名称不能为空', 'error');
            return;
        }
        
        if (!channelId) {
            showToast('频道ID无效', 'error');
            return;
        }
        
        showLoading('正在更新频道信息...');
        
        // 构建更新数据
        const updateData = {
            name: name,
            cpm: cpm,
            category: category,
            description: description,
            is_active: isActive
        };
        
        // 调用API更新频道
        const response = await api.updateChannel(channelId, updateData);
        
        if (response.success) {
            // 关闭模态框
            const modal = bootstrap.Modal.getInstance(document.getElementById('editChannelModal'));
            modal.hide();
            
            // 刷新频道列表
            if (channelsInstance) {
                channelsInstance.refresh();
            }
            
            hideLoading();
            showToast('频道信息更新成功', 'success');
        } else {
            hideLoading();
            showToast('更新频道信息失败: ' + response.message, 'error');
        }
        
    } catch (error) {
        console.error('更新频道信息失败:', error);
        hideLoading();
        showToast('更新频道信息失败', 'error');
    }
}

function deleteChannel(channelId) {
    if (confirm('确定要删除此频道吗？此操作不可撤销。')) {
        api.deleteChannel(channelId)
            .then(response => {
                showToast('频道删除成功', 'success');
                if (channelsInstance) {
                    channelsInstance.refresh();
                }
            })
            .catch(error => {
                console.error('删除频道失败:', error);
                showToast('删除频道失败', 'error');
            });
    }
}

// 当DOM加载完成时初始化频道管理
let channelsInstance;

document.addEventListener('DOMContentLoaded', function() {
    // 监听标签页切换
    document.addEventListener('shown.bs.tab', function(e) {
        if (e.target.getAttribute('href') === '#channels') {
            if (!channelsInstance) {
                channelsInstance = new Channels();
            } else {
                channelsInstance.refresh();
            }
            
            // 异步加载频道统计数据
            setTimeout(() => {
                if (channelsInstance && channelsInstance.channels) {
                    channelsInstance.channels.forEach(channel => {
                        channelsInstance.loadChannelStats(channel.id);
                    });
                }
            }, 500);
        }
    });
});

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Channels };
}