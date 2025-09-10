// 频道管理页面JavaScript逻辑
class ChannelManagement {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 20;
        this.totalPages = 0;
        this.channels = []; // 当前页显示的频道
        this.allChannels = []; // 所有频道数据（用于前端分页）
        this.filteredChannels = [];
        this.selectedChannels = new Set();
        this.sortField = 'name';
        this.sortDirection = 'asc';
        this.categories = []; // 存储分类数据
        
        this.bindEvents();
        this.loadCategories(); // 加载分类数据
    }

    bindEvents() {
        // 搜索框事件
        const searchInput = document.getElementById('search-channels');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                this.debounce(() => this.filterChannels(), 300)();
            });
        }

        // 筛选器事件
        const categoryFilter = document.getElementById('filter-category');
        const statusFilter = document.getElementById('filter-status');
        
        if (categoryFilter) {
            categoryFilter.addEventListener('change', () => this.filterChannels());
        }
        
        if (statusFilter) {
            statusFilter.addEventListener('change', () => this.filterChannels());
        }

        // 全选事件
        const selectAllMain = document.getElementById('select-all-channels');
        const selectAllTable = document.getElementById('table-select-all');
        
        if (selectAllMain) {
            selectAllMain.addEventListener('change', (e) => this.toggleSelectAll(e.target.checked));
        }
        
        if (selectAllTable) {
            selectAllTable.addEventListener('change', (e) => this.toggleSelectAll(e.target.checked));
        }
    }

    // 防抖函数
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // 加载频道数据
    async loadChannels() {
        try {
            this.showLoading();
            
            // 使用后端分页参数
            const response = await api.getChannels({
                page: this.currentPage,
                per_page: this.pageSize,
                search: document.getElementById('search-channels')?.value || '',
                category: document.getElementById('filter-category')?.value || '',
                active_only: document.getElementById('filter-status')?.value === 'active' ? true : 
                            document.getElementById('filter-status')?.value === 'inactive' ? false : undefined
            });

            if (response.success) {
                // 处理后端分页响应格式
                let channels, pagination;
                
                if (response.data.channels && response.data.pagination) {
                    // 标准分页格式：{channels: [], pagination: {}}
                    channels = response.data.channels;
                    pagination = response.data.pagination;
                } else if (Array.isArray(response.data)) {
                    // 简单格式：直接返回数组（无分页）
                    channels = response.data;
                    pagination = {
                        page: 1,
                        total: channels.length,
                        pages: 1,
                        per_page: channels.length
                    };
                } else {
                    // 其他格式
                    channels = response.data || [];
                    pagination = {
                        page: 1,
                        total: channels.length,
                        pages: 1,
                        per_page: channels.length
                    };
                }
                
                // 存储当前页频道数据
                this.channels = channels;
                
                // 更新分页信息
                this.totalPages = pagination.pages || 1;
                this.currentPage = pagination.page || 1;
                
                console.log('频道数据加载:', {
                    currentPageChannels: this.channels.length,
                    total: pagination.total,
                    totalPages: this.totalPages,
                    currentPage: this.currentPage,
                    pageSize: this.pageSize
                });
                
                // 强制调试输出
                console.log('=== 频道管理调试信息 ===');
                console.log('API响应:', response);
                console.log('分页信息:', pagination);
                console.log('频道数据:', channels);
                
                this.updateStatistics();
                this.renderChannelTable();
                this.updatePagination(pagination.total);
                
                // 更新频道总数显示（使用分页信息中的total）
                const totalChannelsEl = document.getElementById('total-channels');
                if (totalChannelsEl) {
                    totalChannelsEl.textContent = pagination.total || 0;
                }
                
                // 异步加载全局统计信息
                this.loadGlobalStatistics();
            } else {
                this.showError('加载频道数据失败: ' + response.message);
            }
        } catch (error) {
            console.error('加载频道数据失败:', error);
            this.showError('加载频道数据失败');
        } finally {
            this.hideLoading();
        }
    }

    // 更新统计信息
    updateStatistics(stats) {
        // 计算统计信息
        const totalChannels = this.channels.length;
        const activeChannels = this.channels.filter(channel => channel.is_active).length;
        
        // 如果API返回了统计信息，使用API数据，否则使用当前页面数据计算
        let totalSubscribers = 0;
        let totalVideos = 0;
        
        if (stats) {
            // 使用API返回的全局统计
            document.getElementById('stat-total-channels').textContent = stats.total_channels || 0;
            document.getElementById('stat-active-channels').textContent = stats.active_channels || 0;
            document.getElementById('stat-total-subscribers').textContent = Utils.formatNumber(stats.total_subscribers || 0);
            document.getElementById('stat-total-videos').textContent = Utils.formatNumber(stats.total_videos || 0);
        } else {
            // 使用当前页面数据计算（仅当前页的统计）
            this.channels.forEach(channel => {
                // 假设频道数据中有最新的统计信息
                if (channel.latest_data) {
                    totalSubscribers += channel.latest_data.subscribers || 0;
                    totalVideos += channel.latest_data.video_count || 0;
                }
            });
            
            document.getElementById('stat-total-channels').textContent = totalChannels;
            document.getElementById('stat-active-channels').textContent = activeChannels;
            document.getElementById('stat-total-subscribers').textContent = Utils.formatNumber(totalSubscribers);
            document.getElementById('stat-total-videos').textContent = Utils.formatNumber(totalVideos);
        }
        
        console.log('统计信息更新:', {
            totalChannels,
            activeChannels,
            totalSubscribers,
            totalVideos,
            useApiStats: !!stats
        });
    }

    // 加载全局统计信息
    async loadGlobalStatistics() {
        try {
            // 获取所有频道的统计信息
            const allChannelsResponse = await api.getChannels({ 
                page: 1, 
                per_page: 1000,  // 获取所有频道
                active_only: false 
            });
            
            if (allChannelsResponse.success) {
                let allChannels = [];
                
                // 处理响应格式
                if (allChannelsResponse.data.channels) {
                    allChannels = allChannelsResponse.data.channels;
                } else if (Array.isArray(allChannelsResponse.data)) {
                    allChannels = allChannelsResponse.data;
                } else {
                    allChannels = allChannelsResponse.data || [];
                }
                
                // 计算全局统计
                const totalChannels = allChannels.length;
                const activeChannels = allChannels.filter(channel => channel.is_active).length;
                
                let totalSubscribers = 0;
                let totalVideos = 0;
                
                // 遍历所有频道计算总计
                allChannels.forEach(channel => {
                    if (channel.latest_data) {
                        totalSubscribers += channel.latest_data.subscribers || 0;
                        totalVideos += channel.latest_data.video_count || 0;
                    }
                });
                
                // 更新统计显示
                document.getElementById('stat-total-channels').textContent = totalChannels;
                document.getElementById('stat-active-channels').textContent = activeChannels;
                document.getElementById('stat-total-subscribers').textContent = Utils.formatNumber(totalSubscribers);
                document.getElementById('stat-total-videos').textContent = Utils.formatNumber(totalVideos);
                
                console.log('全局统计信息更新:', {
                    totalChannels,
                    activeChannels,
                    totalSubscribers,
                    totalVideos
                });
            }
        } catch (error) {
            console.error('加载全局统计失败:', error);
            // 使用当前页面数据作为回退
        }
    }

    // 加载频道分类
    async loadCategories() {
        try {
            const response = await api.getChannelCategories();
            if (response.success && response.data.categories) {
                this.categories = response.data.categories;
                console.log('加载到的分类:', this.categories);
                
                // 更新筛选器下拉框
                this.updateFilterCategoryDropdown();
                
                // 更新编辑模态框的分类下拉框
                this.updateEditCategoryDropdown();
            }
        } catch (error) {
            console.error('加载分类失败:', error);
            // 使用默认分类作为回退
            this.categories = [
                { name: '科技', count: 0 },
                { name: '教育', count: 0 },
                { name: '娱乐', count: 0 },
                { name: '生活', count: 0 },
                { name: '游戏', count: 0 },
                { name: '音乐', count: 0 },
                { name: '其他', count: 0 }
            ];
            this.updateFilterCategoryDropdown();
            this.updateEditCategoryDropdown();
        }
    }

    // 更新筛选器的分类下拉框
    updateFilterCategoryDropdown() {
        const filterCategory = document.getElementById('filter-category');
        if (!filterCategory) return;

        // 保存当前选中的值
        const currentValue = filterCategory.value;

        // 构建选项HTML
        let options = '<option value="">所有分类</option>';
        this.categories.forEach(category => {
            options += `<option value="${category.name}">${category.name} (${category.count})</option>`;
        });

        // 更新下拉框
        filterCategory.innerHTML = options;
        
        // 恢复之前选中的值
        if (currentValue) {
            filterCategory.value = currentValue;
        }
    }

    // 更新编辑模态框的分类下拉框
    updateEditCategoryDropdown() {
        const editCategory = document.getElementById('edit-channel-category');
        if (!editCategory) return;

        // 保存当前选中的值
        const currentValue = editCategory.value;

        // 构建选项HTML
        let options = '<option value="">选择分类</option>';
        this.categories.forEach(category => {
            options += `<option value="${category.name}">${category.name}</option>`;
        });

        // 更新下拉框
        editCategory.innerHTML = options;
        
        // 恢复之前选中的值
        if (currentValue) {
            editCategory.value = currentValue;
        }
    }

    // 渲染频道表格
    renderChannelTable() {
        const tbody = document.getElementById('channels-tbody');
        if (!tbody) {
            console.error('未找到 channels-tbody 元素');
            return;
        }

        if (this.channels.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="px-6 py-12 text-center">
                        <div class="flex flex-col items-center justify-center text-gray-500">
                            <i class="fas fa-tv text-4xl mb-4 opacity-50"></i>
                            <p class="text-lg font-medium mb-2">暂无频道数据</p>
                            <p class="text-sm">点击"添加频道"开始管理您的YouTube频道</p>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        const rows = this.channels.map(channel => this.renderChannelRow(channel)).join('');
        tbody.innerHTML = rows;
        
        // 更新选中状态
        this.updateSelectedChannels();
    }

    // 渲染单个频道行
    renderChannelRow(channel) {
        const isSelected = this.selectedChannels.has(channel.id);
        const statusBadge = channel.is_active
            ? '<span class="px-2 py-1 bg-green-100 text-green-800 text-xs font-medium rounded-full">活跃</span>'
            : '<span class="px-2 py-1 bg-gray-100 text-gray-800 text-xs font-medium rounded-full">停用</span>';

        const latestData = channel.latest_data || {};
        const subscribers = Utils.formatNumber(latestData.subscribers || 0);

        return `
            <tr class="hover:bg-gray-50 transition-colors ${isSelected ? 'bg-blue-50' : ''}">
                <td class="px-6 py-4">
                    <input type="checkbox" 
                           class="channel-checkbox rounded border-gray-300 text-primary-600" 
                           data-channel-id="${channel.id}" 
                           ${isSelected ? 'checked' : ''}
                           onchange="window.channelManagement.toggleChannelSelection(${channel.id})">
                </td>
                <td class="px-6 py-4">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 bg-gradient-to-br from-red-400 to-red-600 rounded-lg flex items-center justify-center">
                            <i class="fab fa-youtube text-white"></i>
                        </div>
                        <div>
                            <div class="font-medium text-gray-900">${channel.name}</div>
                            <div class="text-sm text-gray-500">${channel.channel_id}</div>
                        </div>
                    </div>
                </td>
                <td class="px-6 py-4">
                    <span class="px-2 py-1 bg-blue-100 text-blue-800 text-xs font-medium rounded-full">
                        ${channel.category || '未分类'}
                    </span>
                </td>
                <td class="px-6 py-4 text-gray-900 font-medium">${subscribers}</td>
                <td class="px-6 py-4 text-gray-900">$${channel.cpm}</td>
                <td class="px-6 py-4">${statusBadge}</td>
                <td class="px-6 py-4 text-gray-500 text-sm">${Utils.formatDate(channel.updated_at)}</td>
                <td class="px-6 py-4 text-center">
                    <div class="flex items-center justify-center gap-2">
                        <button onclick="window.channelManagement.editChannel(${channel.id})" 
                                class="p-2 text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded-lg transition-colors" 
                                title="编辑">
                            <i class="fas fa-edit"></i>
                        </button>
                        <button onclick="window.channelManagement.syncChannel(${channel.id})" 
                                class="p-2 text-green-600 hover:text-green-800 hover:bg-green-50 rounded-lg transition-colors" 
                                title="同步">
                            <i class="fas fa-sync-alt"></i>
                        </button>
                        <button onclick="window.channelManagement.toggleChannelStatus(${channel.id})" 
                                class="p-2 text-yellow-600 hover:text-yellow-800 hover:bg-yellow-50 rounded-lg transition-colors" 
                                title="${channel.is_active ? '停用' : '启用'}">
                            <i class="fas fa-toggle-${channel.is_active ? 'on' : 'off'}"></i>
                        </button>
                        <button onclick="window.channelManagement.deleteChannel(${channel.id})" 
                                class="p-2 text-red-600 hover:text-red-800 hover:bg-red-50 rounded-lg transition-colors" 
                                title="删除">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }

    // 筛选频道
    filterChannels() {
        const searchTerm = document.getElementById('search-channels')?.value.toLowerCase() || '';
        const categoryFilter = document.getElementById('filter-category')?.value || '';
        const statusFilter = document.getElementById('filter-status')?.value || '';

        this.currentPage = 1; // 重置页码
        this.loadChannels(); // 重新加载数据（服务端筛选）
    }

    // 清除筛选
    clearFilters() {
        document.getElementById('search-channels').value = '';
        document.getElementById('filter-category').value = '';
        document.getElementById('filter-status').value = '';
        this.filterChannels();
    }

    // 切换频道选择状态
    toggleChannelSelection(channelId) {
        if (this.selectedChannels.has(channelId)) {
            this.selectedChannels.delete(channelId);
        } else {
            this.selectedChannels.add(channelId);
        }
        this.updateBatchActions();
    }

    // 全选/取消全选
    toggleSelectAll(checked) {
        const checkboxes = document.querySelectorAll('.channel-checkbox');
        checkboxes.forEach(checkbox => {
            const channelId = parseInt(checkbox.dataset.channelId);
            checkbox.checked = checked;
            
            if (checked) {
                this.selectedChannels.add(channelId);
            } else {
                this.selectedChannels.delete(channelId);
            }
        });
        
        this.updateBatchActions();
        this.updateSelectedChannels();
    }

    // 更新选中状态显示
    updateSelectedChannels() {
        const selectAllMain = document.getElementById('select-all-channels');
        const selectAllTable = document.getElementById('table-select-all');
        const checkboxes = document.querySelectorAll('.channel-checkbox');
        
        const allChecked = checkboxes.length > 0 && Array.from(checkboxes).every(cb => cb.checked);
        const someChecked = Array.from(checkboxes).some(cb => cb.checked);
        
        if (selectAllMain) {
            selectAllMain.checked = allChecked;
            selectAllMain.indeterminate = someChecked && !allChecked;
        }
        
        if (selectAllTable) {
            selectAllTable.checked = allChecked;
            selectAllTable.indeterminate = someChecked && !allChecked;
        }
    }

    // 更新批量操作按钮状态
    updateBatchActions() {
        const selectedCount = this.selectedChannels.size;
        const batchBtn = document.getElementById('batch-actions-btn');
        const selectedCountSpan = document.getElementById('selected-count');
        
        if (batchBtn) {
            if (selectedCount > 0) {
                batchBtn.disabled = false;
                batchBtn.className = batchBtn.className.replace('bg-gray-100 text-gray-400 cursor-not-allowed', 'bg-primary-500 text-white hover:bg-primary-600 cursor-pointer');
            } else {
                batchBtn.disabled = true;
                batchBtn.className = batchBtn.className.replace('bg-primary-500 text-white hover:bg-primary-600 cursor-pointer', 'bg-gray-100 text-gray-400 cursor-not-allowed');
            }
        }
        
        if (selectedCountSpan) {
            selectedCountSpan.textContent = selectedCount;
        }
    }

    // 表格排序
    sortTable(field) {
        if (this.sortField === field) {
            this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            this.sortField = field;
            this.sortDirection = 'asc';
        }
        
        this.loadChannels();
    }

    // 分页相关方法
    changePage(direction) {
        const newPage = this.currentPage + direction;
        if (newPage >= 1 && newPage <= this.totalPages) {
            this.currentPage = newPage;
            this.loadChannels();
        }
    }

    goToPage(page) {
        if (page >= 1 && page <= this.totalPages) {
            this.currentPage = page;
            this.loadChannels();
        }
    }

    updatePagination(total) {
        // 确保total是有效数值
        total = parseInt(total) || 0;
        
        const start = total > 0 ? (this.currentPage - 1) * this.pageSize + 1 : 0;
        const end = Math.min(this.currentPage * this.pageSize, total);
        
        const pageStartEl = document.getElementById('page-start');
        const pageEndEl = document.getElementById('page-end');
        const totalRecordsEl = document.getElementById('total-count');
        
        if (pageStartEl) pageStartEl.textContent = start;
        if (pageEndEl) pageEndEl.textContent = end;
        if (totalRecordsEl) totalRecordsEl.textContent = total;
        
        // 更新分页按钮
        const prevBtn = document.getElementById('prev-page');
        const nextBtn = document.getElementById('next-page');
        
        if (prevBtn) {
            prevBtn.disabled = this.currentPage <= 1;
            prevBtn.style.display = this.totalPages <= 1 ? 'none' : 'inline-flex';
        }
        if (nextBtn) {
            nextBtn.disabled = this.currentPage >= this.totalPages;
            nextBtn.style.display = this.totalPages <= 1 ? 'none' : 'inline-flex';
        }
        
        console.log('分页更新:', {
            start,
            end,
            total,
            currentPage: this.currentPage,
            totalPages: this.totalPages,
            pageSize: this.pageSize,
            prevBtnDisabled: this.currentPage <= 1,
            nextBtnDisabled: this.currentPage >= this.totalPages,
            shouldHideButtons: this.totalPages <= 1
        });
        
        this.renderPageNumbers();
    }

    renderPageNumbers() {
        const container = document.getElementById('page-numbers');
        if (!container) return;
        
        const current = this.currentPage;
        const total = this.totalPages;
        
        // 如果只有一页或没有数据，不显示页码
        if (total <= 1) {
            container.innerHTML = '';
            return;
        }
        
        let pages = [];
        
        // 计算显示的页码
        if (total <= 7) {
            pages = Array.from({length: total}, (_, i) => i + 1);
        } else {
            if (current <= 4) {
                pages = [1, 2, 3, 4, 5, '...', total];
            } else if (current >= total - 3) {
                pages = [1, '...', total - 4, total - 3, total - 2, total - 1, total];
            } else {
                pages = [1, '...', current - 1, current, current + 1, '...', total];
            }
        }
        
        container.innerHTML = pages.map(page => {
            if (page === '...') {
                return '<span class="px-3 py-2 text-gray-500">...</span>';
            }
            
            const isActive = page === current;
            const className = isActive 
                ? 'px-3 py-2 bg-primary-600 text-white rounded-button text-sm font-medium'
                : 'px-3 py-2 border border-gray-300 rounded-button text-sm text-gray-700 hover:bg-gray-50 cursor-pointer';
                
            return `<button onclick="window.channelManagement.goToPage(${page})" class="${className}">${page}</button>`;
        }).join('');
    }

    // 频道操作方法
    async editChannel(channelId) {
        try {
            const channel = this.channels.find(c => c.id === channelId);
            if (!channel) return;
            
            // 这里应该打开编辑模态框
            this.openEditChannelModal(channel);
        } catch (error) {
            console.error('编辑频道失败:', error);
            this.showError('编辑频道失败');
        }
    }

    async syncChannel(channelId) {
        try {
            this.showLoading('正在同步频道数据...');
            
            const response = await api.syncChannel(channelId);
            if (response.success) {
                this.showSuccess('频道数据同步成功');
                this.loadChannels(); // 重新加载数据
            } else {
                this.showError('同步失败: ' + response.message);
            }
        } catch (error) {
            console.error('同步频道失败:', error);
            this.showError('同步频道失败');
        } finally {
            this.hideLoading();
        }
    }

    async toggleChannelStatus(channelId) {
        try {
            const channel = this.channels.find(c => c.id === channelId);
            if (!channel) return;
            
            const newStatus = !channel.is_active;
            const action = newStatus ? '启用' : '停用';
            
            if (!confirm(`确定要${action}频道"${channel.name}"吗？`)) return;
            
            const response = await api.updateChannel(channelId, { is_active: newStatus });
            if (response.success) {
                this.showSuccess(`频道${action}成功`);
                this.loadChannels();
            } else {
                this.showError(`${action}失败: ` + response.message);
            }
        } catch (error) {
            console.error('切换频道状态失败:', error);
            this.showError('操作失败');
        }
    }

    async deleteChannel(channelId) {
        try {
            const channel = this.channels.find(c => c.id === channelId);
            if (!channel) return;
            
            if (!confirm(`确定要删除频道"${channel.name}"吗？\n\n删除后该频道的所有历史数据都将被清除，此操作不可恢复！`)) return;
            
            const response = await api.deleteChannel(channelId);
            if (response.success) {
                this.showSuccess('频道删除成功');
                this.selectedChannels.delete(channelId); // 从选中列表中移除
                this.loadChannels();
            } else {
                this.showError('删除失败: ' + response.message);
            }
        } catch (error) {
            console.error('删除频道失败:', error);
            this.showError('删除频道失败');
        }
    }

    // 工具方法
    showLoading(message = '正在加载...') {
        // 使用全局的加载状态
        if (window.app && window.app.showLoading) {
            window.app.showLoading(message);
        } else {
            // 回退到页面的加载状态
            const loadingElement = document.getElementById('channels-loading');
            const emptyElement = document.getElementById('channels-empty');
            const tableContainer = document.querySelector('.table-container');
            
            if (loadingElement) {
                loadingElement.classList.remove('hidden');
            }
            if (emptyElement) {
                emptyElement.classList.add('hidden');
            }
            if (tableContainer) {
                tableContainer.style.display = 'none';
            }
        }
    }

    hideLoading() {
        if (window.app && window.app.hideLoading) {
            window.app.hideLoading();
        } else {
            // 回退到页面的加载状态
            const loadingElement = document.getElementById('channels-loading');
            const tableContainer = document.querySelector('.table-container');
            
            if (loadingElement) {
                loadingElement.classList.add('hidden');
            }
            if (tableContainer) {
                tableContainer.style.display = 'block';
            }
        }
    }

    showSuccess(message) {
        if (window.app && window.app.showToast) {
            window.app.showToast(message, 'success');
        } else {
            showToast(message, 'success');
        }
    }

    showError(message) {
        if (window.app && window.app.showToast) {
            window.app.showToast(message, 'error');
        } else {
            showToast(message, 'error');
        }
    }

    // 打开编辑频道模态框
    openEditChannelModal(channel) {
        const modal = document.getElementById('editChannelModal');
        const content = document.getElementById('editModalContent');
        
        // 确保分类下拉框是最新的
        this.updateEditCategoryDropdown();
        
        // 填充表单数据
        document.getElementById('edit-channel-id').value = channel.id;
        document.getElementById('edit-channel-name').value = channel.name;
        document.getElementById('edit-channel-category').value = channel.category || '';
        document.getElementById('edit-channel-cpm').value = channel.cpm;
        document.getElementById('edit-channel-active').checked = channel.is_active;
        
        // 填充统计信息
        const latestData = channel.latest_data || {};
        document.getElementById('edit-stats-subscribers').textContent = Utils.formatNumber(latestData.subscribers || 0);
        document.getElementById('edit-stats-views').textContent = Utils.formatNumber(latestData.total_views || 0);
        document.getElementById('edit-stats-videos').textContent = latestData.video_count || 0;
        document.getElementById('edit-stats-updated').textContent = Utils.formatDate(channel.updated_at);
        
        // 显示模态框
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        
        // 动画效果
        setTimeout(() => {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }, 10);
        
        // 点击模态框外部关闭
        modal.addEventListener('click', (e) => {
            if (e.target.id === 'editChannelModal') {
                this.closeEditChannelModal();
            }
        });
    }
}

// 全局函数，供HTML调用
function toggleBatchActions() {
    const panel = document.getElementById('batch-actions-panel');
    if (panel) {
        panel.classList.toggle('hidden');
    }
}

function clearFilters() {
    if (window.channelManagement) {
        window.channelManagement.clearFilters();
    }
}

function changePage(direction) {
    if (window.channelManagement) {
        window.channelManagement.changePage(direction);
    }
}

function sortTable(field) {
    if (window.channelManagement) {
        window.channelManagement.sortTable(field);
    }
}

// 批量操作函数
async function batchSync() {
    if (window.channelManagement) {
        const selectedIds = Array.from(window.channelManagement.selectedChannels);
        if (selectedIds.length === 0) return;
        
        if (!confirm(`确定要同步选中的 ${selectedIds.length} 个频道吗？`)) return;
        
        try {
            window.channelManagement.showLoading('正在批量同步频道...');
            
            // 这里应该调用批量同步API
            const response = await api.batchSyncChannels(selectedIds);
            if (response.success) {
                window.channelManagement.showSuccess('批量同步完成');
                window.channelManagement.loadChannels();
                window.channelManagement.selectedChannels.clear();
                window.channelManagement.updateBatchActions();
            } else {
                window.channelManagement.showError('批量同步失败: ' + response.message);
            }
        } catch (error) {
            console.error('批量同步失败:', error);
            window.channelManagement.showError('批量同步失败');
        } finally {
            window.channelManagement.hideLoading();
        }
    }
}

async function batchToggleStatus() {
    if (window.channelManagement) {
        const selectedIds = Array.from(window.channelManagement.selectedChannels);
        if (selectedIds.length === 0) return;
        
        if (!confirm(`确定要切换选中的 ${selectedIds.length} 个频道的状态吗？`)) return;
        
        try {
            window.channelManagement.showLoading('正在批量切换状态...');
            
            let successCount = 0;
            let failedCount = 0;
            
            for (const channelId of selectedIds) {
                try {
                    const channel = window.channelManagement.allChannels.find(c => c.id === channelId);
                    if (channel) {
                        const newStatus = !channel.is_active;
                        const response = await api.updateChannel(channelId, { is_active: newStatus });
                        if (response.success) {
                            successCount++;
                        } else {
                            failedCount++;
                        }
                    }
                } catch (error) {
                    failedCount++;
                }
            }
            
            window.channelManagement.showSuccess(`批量状态切换完成: 成功 ${successCount}, 失败 ${failedCount}`);
            window.channelManagement.loadChannels();
            window.channelManagement.selectedChannels.clear();
            window.channelManagement.updateBatchActions();
        } catch (error) {
            console.error('批量状态切换失败:', error);
            window.channelManagement.showError('批量状态切换失败');
        } finally {
            window.channelManagement.hideLoading();
        }
    }
}

async function batchDelete() {
    if (window.channelManagement) {
        const selectedIds = Array.from(window.channelManagement.selectedChannels);
        if (selectedIds.length === 0) return;
        
        if (!confirm(`确定要删除选中的 ${selectedIds.length} 个频道吗？\n\n删除后这些频道的所有历史数据都将被清除，此操作不可恢复！`)) return;
        
        try {
            window.channelManagement.showLoading('正在批量删除频道...');
            
            let successCount = 0;
            let failedCount = 0;
            
            for (const channelId of selectedIds) {
                try {
                    const response = await api.deleteChannel(channelId);
                    if (response.success) {
                        successCount++;
                        window.channelManagement.selectedChannels.delete(channelId);
                    } else {
                        failedCount++;
                    }
                } catch (error) {
                    failedCount++;
                }
            }
            
            window.channelManagement.showSuccess(`批量删除完成: 成功 ${successCount}, 失败 ${failedCount}`);
            window.channelManagement.loadChannels();
            window.channelManagement.selectedChannels.clear();
            window.channelManagement.updateBatchActions();
        } catch (error) {
            console.error('批量删除失败:', error);
            window.channelManagement.showError('批量删除失败');
        } finally {
            window.channelManagement.hideLoading();
        }
    }
}

async function syncAllChannelsFromManagement() {
    if (window.channelManagement) {
        if (!confirm('确定要同步所有频道的数据吗？这将从YouTube获取最新数据。')) return;
        
        try {
            window.channelManagement.showLoading('正在同步所有频道数据...');
            const response = await api.syncAllChannels();
            
            if (response.success) {
                window.channelManagement.showSuccess(
                    `同步完成！成功: ${response.data.success_count}, 失败: ${response.data.failed_count}`
                );
                window.channelManagement.loadChannels();
            } else {
                window.channelManagement.showError('同步失败: ' + response.message);
            }
        } catch (error) {
            console.error('同步失败:', error);
            window.channelManagement.showError('同步失败');
        } finally {
            window.channelManagement.hideLoading();
        }
    }
}

// 编辑频道模态框控制函数
function closeEditChannelModal() {
    const modal = document.getElementById('editChannelModal');
    const content = document.getElementById('editModalContent');
    
    // 动画效果
    content.classList.remove('scale-100', 'opacity-100');
    content.classList.add('scale-95', 'opacity-0');
    
    setTimeout(() => {
        modal.classList.add('hidden');
        document.body.style.overflow = 'auto';
        document.getElementById('editChannelForm').reset();
    }, 300);
}

async function updateChannel() {
    if (!window.channelManagement) return;
    
    const form = document.getElementById('editChannelForm');
    const formData = new FormData(form);
    
    const channelId = document.getElementById('edit-channel-id').value;
    const channelData = {
        name: document.getElementById('edit-channel-name').value.trim(),
        category: document.getElementById('edit-channel-category').value || null,
        cpm: parseFloat(document.getElementById('edit-channel-cpm').value) || 2.5,
        is_active: document.getElementById('edit-channel-active').checked
    };

    if (!channelData.name) {
        window.channelManagement.showError('请输入频道名称');
        return;
    }

    try {
        window.channelManagement.showLoading('正在更新频道...');
        const response = await api.updateChannel(channelId, channelData);
        
        if (response.success) {
            window.channelManagement.showSuccess('频道更新成功');
            closeEditChannelModal();
            window.channelManagement.loadChannels(); // 重新加载数据
        } else {
            window.channelManagement.showError('更新失败: ' + response.message);
        }
        
        window.channelManagement.hideLoading();
    } catch (error) {
        window.channelManagement.hideLoading();
        window.channelManagement.showError('更新失败: ' + error.message);
    }
}

// 当频道管理页面显示时初始化
function initChannelManagement() {
    if (!window.channelManagement) {
        window.channelManagement = new ChannelManagement();
    }
    window.channelManagement.loadChannels();
}

// 缺失的全局函数定义
function openAddChannelModal() {
    // TODO: 实现添加频道模态框
    showToast('添加频道功能正在开发中', 'info');
}

// openChannelFilterModal 函数在 data-table.js 中实现
// 这里移除重复定义避免函数覆盖

function sortChannels(field) {
    if (window.channelManagement) {
        window.channelManagement.sortChannels(field);
    }
}

function previousPage() {
    if (window.channelManagement) {
        window.channelManagement.previousPage();
    }
}

function nextPage() {
    if (window.channelManagement) {
        window.channelManagement.nextPage();
    }
}

// Toast 消息显示函数
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast bg-white border rounded-lg shadow-lg p-4 max-w-sm transition-all duration-300 transform translate-x-full opacity-0`;
    
    const colors = {
        success: 'border-green-200 text-green-800',
        error: 'border-red-200 text-red-800',
        warning: 'border-yellow-200 text-yellow-800',
        info: 'border-blue-200 text-blue-800'
    };
    
    const icons = {
        success: 'fas fa-check-circle',
        error: 'fas fa-times-circle',
        warning: 'fas fa-exclamation-triangle',
        info: 'fas fa-info-circle'
    };
    
    toast.className += ` ${colors[type] || colors.info}`;
    
    toast.innerHTML = `
        <div class="flex items-center">
            <i class="${icons[type] || icons.info} mr-3"></i>
            <span>${message}</span>
            <button onclick="this.parentElement.parentElement.remove()" class="ml-4 text-gray-400 hover:text-gray-600">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `;
    
    container.appendChild(toast);
    
    // 显示动画
    setTimeout(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    }, 100);
    
    // 自动消失
    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}