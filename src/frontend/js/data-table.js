// 数据表格模块
class DataTable {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 10;
        this.startDate = null;
        this.endDate = null;
        this.tableData = null;
        this.selectedChannelIds = []; // 已选择的频道ID列表
        this.allChannels = []; // 所有可用频道
        
        this.init();
    }

    // 初始化
    init() {
        this.bindEvents();
        this.setDefaultDates();
        this.loadTableData();
    }

    // 绑定事件
    bindEvents() {
        const startDateInput = document.getElementById('start-date');
        const endDateInput = document.getElementById('end-date');
        
        if (startDateInput) {
            startDateInput.addEventListener('change', () => {
                this.startDate = startDateInput.value;
            });
        }
        
        if (endDateInput) {
            endDateInput.addEventListener('change', () => {
                this.endDate = endDateInput.value;
            });
        }
    }

    // 设置默认日期（本月1日到今天）
    setDefaultDates() {
        const endDate = new Date();
        const startDate = new Date();
        // 设置为本月1日
        startDate.setDate(1);
        
        this.endDate = Utils.formatDate(endDate);
        this.startDate = Utils.formatDate(startDate);
        
        const startDateInput = document.getElementById('start-date');
        const endDateInput = document.getElementById('end-date');
        
        if (startDateInput) startDateInput.value = this.startDate;
        if (endDateInput) endDateInput.value = this.endDate;
    }

    // 加载表格数据
    async loadTableData() {
        try {
            this.showLoading('正在加载数据表格...');
            
            const params = {
                page: this.currentPage,
                per_page: this.pageSize
            };
            
            if (this.startDate) params.start_date = this.startDate;
            if (this.endDate) params.end_date = this.endDate;
            if (this.selectedChannelIds.length > 0) {
                params.channel_ids = this.selectedChannelIds.join(',');
            }
            
            const response = await api.getTableData(params);
            this.tableData = response.data;
            
            this.renderTable();
            this.renderPagination(this.tableData.pagination);
            
            this.hideLoading();
            
        } catch (error) {
            console.error('加载表格数据失败:', error);
            this.hideLoading();
            this.showError('加载表格数据失败');
        }
    }

    // 渲染表格 - 复用new-main.js的renderTable方法
    renderTable() {
        const table = document.getElementById('data-table');
        if (!table) return;
        const thead = table.querySelector('thead');
        const tbody = table.querySelector('tbody');
        
        if (!this.tableData || !this.tableData.channels || this.tableData.channels.length === 0) {
            thead.innerHTML = '<tr><th class="sticky left-0 bg-gray-50 px-6 py-4 border-b border-gray-200 font-medium text-gray-500 text-left">日期</th></tr>';
            tbody.innerHTML = '<tr><td colspan="100%" class="text-center text-muted py-8">暂无数据</td></tr>';
            return;
        }
        
        // 使用和new-main.js完全相同的渲染逻辑
        this.renderComplexTable(thead, tbody, this.tableData);
    }

    // 复杂表格渲染方法 - 完全复制new-main.js的逻辑
    renderComplexTable(thead, tbody, data) {
        // 生成表头
        let headerHtml = `
            <tr class="bg-gray-50">
                <th class="sticky left-0 bg-gray-50 px-6 py-4 border-b border-gray-200 font-medium text-gray-500 text-left" rowspan="2">日期</th>
        `;
        data.channels.forEach((channel, index) => {
            const channelClass = index % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';
            headerHtml += `
                <th class="px-6 py-4 border-b border-gray-200 font-medium text-gray-700 text-center ${channelClass} channel-divider" colspan="7">
                    ${channel.channel_name} (CPM: ${channel.cpm})
                </th>
            `;
        });
        headerHtml += '</tr><tr class="bg-gray-50">';
        data.channels.forEach((channel, index) => {
            const channelClass = index % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';
            headerHtml += `
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} channel-divider whitespace-nowrap min-w-[100px]">订阅数</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[110px]">总浏览量</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">视频总量</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增订阅</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增浏览</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增视频</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">预估收入</th>
            `;
        });
        headerHtml += '</tr>';
        thead.innerHTML = headerHtml;
        
        // 生成数据行
        let bodyHtml = '';
        data.rows.forEach(row => {
            bodyHtml += `
                <tr>
                    <td class="sticky left-0 bg-white px-6 py-4 border-b border-gray-200 text-gray-900">
                        ${Utils.formatDate(row.date)}
                    </td>
            `;
            row.channels_data.forEach((channelData, channelIndex) => {
                const subsciberChange = channelData.subscriber_change || 0;
                const viewChange = channelData.view_change || 0;
                const videoChange = channelData.video_change || 0;
                const estimatedRevenue = channelData.estimated_revenue || 0;
                
                const channelClass = channelIndex % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';
                bodyHtml += `
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} channel-divider whitespace-nowrap min-w-[100px]">
                        ${Utils.formatNumber(channelData.subscribers)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} whitespace-nowrap min-w-[110px]">
                        ${Utils.formatNumber(channelData.total_views)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${channelData.video_count || 0}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${subsciberChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(subsciberChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${viewChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(viewChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${videoChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(videoChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${estimatedRevenue > 0 ? 'text-blue-600 font-medium' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatCurrency(estimatedRevenue)}
                    </td>
                `;
            });
            bodyHtml += '</tr>';
        });
        tbody.innerHTML = bodyHtml;
    }

    // 渲染表头
    renderTableHeader(thead) {
        const channels = this.tableData.channels;
        
        let headerHTML = '<th class="sticky-left">日期</th>';
        
        channels.forEach(channel => {
            headerHTML += `
                <th class="text-center">
                    <div class="fw-bold">${channel.channel_name}</div>
                    <div class="small text-muted">${channel.category || '未分类'}</div>
                </th>
            `;
        });
        
        thead.innerHTML = headerHTML;
    }

    // 渲染表体
    renderTableBody(tbody) {
        const rows = this.tableData.rows;
        const channels = this.tableData.channels;
        
        const rowsHTML = rows.map(row => {
            let rowHTML = `<td class="sticky-left fw-bold">${Utils.formatDate(row.date)}</td>`;
            
            channels.forEach(channel => {
                const channelData = row.channels_data.find(cd => cd.channel_id === channel.channel_id);
                
                if (channelData) {
                    rowHTML += `
                        <td class="text-center">
                            <div class="small">
                                <div>订阅: ${Utils.formatNumber(channelData.subscribers)}</div>
                                <div>观看: ${Utils.formatNumber(channelData.total_views)}</div>
                                <div>视频: ${Utils.formatNumber(channelData.video_count)}</div>
                                <div class="text-success">收入: ${Utils.formatCurrency(channelData.estimated_revenue)}</div>
                            </div>
                        </td>
                    `;
                } else {
                    rowHTML += '<td class="text-center text-muted">-</td>';
                }
            });
            
            return `<tr>${rowHTML}</tr>`;
        }).join('');
        
        tbody.innerHTML = rowsHTML;
    }

    // 渲染分页
    renderPagination(pagination) {
        const container = document.querySelector('#table-pagination .pagination');
        if (!container) return;

        if (pagination.pages <= 1) {
            container.innerHTML = '';
            return;
        }

        let html = '';

        // 上一页
        html += `
            <li class="page-item ${!pagination.has_prev ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="dataTableInstance.goToPage(${pagination.page - 1})">
                    <i class="bi bi-chevron-left"></i>
                </a>
            </li>
        `;

        // 页码
        const startPage = Math.max(1, pagination.page - 2);
        const endPage = Math.min(pagination.pages, pagination.page + 2);

        for (let i = startPage; i <= endPage; i++) {
            html += `
                <li class="page-item ${i === pagination.page ? 'active' : ''}">
                    <a class="page-link" href="#" onclick="dataTableInstance.goToPage(${i})">${i}</a>
                </li>
            `;
        }

        // 下一页
        html += `
            <li class="page-item ${!pagination.has_next ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="dataTableInstance.goToPage(${pagination.page + 1})">
                    <i class="bi bi-chevron-right"></i>
                </a>
            </li>
        `;

        container.innerHTML = html;
    }

    // 跳转页面
    goToPage(page) {
        this.currentPage = page;
        this.loadTableData();
    }

    // 筛选数据
    filterTableData() {
        this.currentPage = 1;
        this.loadTableData();
    }

    // 导出表格数据
    async exportTableData() {
        try {
            const params = {};
            if (this.startDate) params.start_date = this.startDate;
            if (this.endDate) params.end_date = this.endDate;
            
            await api.exportData(params);
            showToast('数据导出成功', 'success');
            
        } catch (error) {
            console.error('导出数据失败:', error);
            showToast('导出数据失败', 'error');
        }
    }

    // 刷新数据
    refresh() {
        this.loadTableData();
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

    // 加载所有频道列表
    async loadAllChannels() {
        try {
            // 请求所有频道（包括非活跃的）用于筛选
            const response = await api.getChannels({ include_inactive: true, per_page: 1000 });
            this.allChannels = response.data.channels || [];
            // 默认选择所有活跃频道
            if (this.selectedChannelIds.length === 0) {
                this.selectedChannelIds = this.allChannels
                    .filter(channel => channel.is_active)
                    .map(channel => channel.id);
            }
            return this.allChannels;
        } catch (error) {
            console.error('加载频道列表失败:', error);
            return [];
        }
    }

    // 设置选中的频道
    setSelectedChannels(channelIds) {
        this.selectedChannelIds = channelIds;
        this.currentPage = 1; // 重置到第一页
        this.loadTableData();
    }

    // 获取选中的频道
    getSelectedChannels() {
        return this.selectedChannelIds;
    }

    // 获取所有频道
    getAllChannels() {
        return this.allChannels;
    }
}

// 数据表格相关的全局函数
function loadTableData() {
    if (dataTableInstance) {
        dataTableInstance.refresh();
    }
}

function filterTableData() {
    if (dataTableInstance) {
        dataTableInstance.filterTableData();
    }
}

function exportTableData() {
    if (dataTableInstance) {
        dataTableInstance.exportTableData();
    }
}

// 当DOM加载完成时初始化数据表格
let dataTableInstance;

document.addEventListener('DOMContentLoaded', function() {
    // 检查是否在首页且存在数据表格
    const dataTableElement = document.getElementById('data-table');
    if (dataTableElement) {
        dataTableInstance = new DataTable();
    }
    
    // 监听标签页切换
    document.addEventListener('shown.bs.tab', function(e) {
        if (e.target.getAttribute('href') === '#data-table') {
            if (!dataTableInstance) {
                dataTableInstance = new DataTable();
            } else {
                dataTableInstance.refresh();
            }
        }
    });
});

// 频道筛选弹层相关功能
let channelFilterData = {
    allChannels: [],
    filteredChannels: [],
    selectedChannelIds: [],
    searchTerm: '',
    selectedCategory: '',
    selectedStatus: ''
};

// 打开频道筛选弹层
async function openChannelFilterModal() {
    const modal = document.getElementById('channelFilterModal');
    const content = document.getElementById('channelFilterContent');
    
    if (modal && content) {
        // 显示弹层
        modal.classList.remove('hidden');
        setTimeout(() => {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }, 10);
        
        // 加载频道列表
        await loadChannelFilterList();
        
        // 绑定搜索和分类筛选事件
        bindChannelFilterEvents();
    }
}

// 关闭频道筛选弹层
function closeChannelFilterModal() {
    const modal = document.getElementById('channelFilterModal');
    const content = document.getElementById('channelFilterContent');
    
    if (modal && content) {
        content.classList.remove('scale-100', 'opacity-100');
        content.classList.add('scale-95', 'opacity-0');
        
        setTimeout(() => {
            modal.classList.add('hidden');
        }, 300);
    }
}

// 加载频道筛选列表
async function loadChannelFilterList() {
    try {
        const loadingEl = document.getElementById('channelFilterLoading');
        const listEl = document.getElementById('channelFilterList');
        
        if (loadingEl) loadingEl.style.display = 'block';
        
        // 从DataTable实例获取频道列表，如果没有就直接请求API
        let channels = [];
        if (dataTableInstance) {
            channels = await dataTableInstance.loadAllChannels();
            channelFilterData.selectedChannelIds = dataTableInstance.getSelectedChannels();
        } else {
            // 请求所有频道（包括非活跃的）用于筛选
            const response = await api.getChannels({ include_inactive: true, per_page: 1000 });
            channels = response.data.channels || [];
            channelFilterData.selectedChannelIds = channels.map(channel => channel.id);
        }
        
        channelFilterData.allChannels = channels;
        channelFilterData.filteredChannels = channels;
        
        if (loadingEl) loadingEl.style.display = 'none';
        
        // 渲染频道列表
        renderChannelFilterList();
        updateSelectedChannelCount();
        
    } catch (error) {
        console.error('加载频道列表失败:', error);
        showToast('加载频道列表失败', 'error');
    }
}

// 渲染频道筛选列表
function renderChannelFilterList() {
    const listEl = document.getElementById('channelFilterList');
    if (!listEl) return;
    
    const { filteredChannels, selectedChannelIds } = channelFilterData;
    
    if (filteredChannels.length === 0) {
        listEl.innerHTML = `
            <div class="p-8 text-center text-gray-500">
                <i class="fas fa-search text-2xl mb-2"></i>
                <div>没有找到匹配的频道</div>
            </div>
        `;
        return;
    }
    
    const html = filteredChannels.map(channel => {
        const isSelected = selectedChannelIds.includes(channel.id);
        const isActive = channel.is_active;
        return `
            <div class="p-4 hover:bg-gray-50 transition-colors cursor-pointer ${!isActive ? 'opacity-75' : ''}" onclick="toggleChannelSelection(${channel.id})">
                <div class="flex items-center gap-3">
                    <div class="flex-shrink-0">
                        <input type="checkbox" ${isSelected ? 'checked' : ''} 
                               class="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
                               onclick="event.stopPropagation();">
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium ${isActive ? 'text-gray-900' : 'text-gray-500'} truncate">
                            ${channel.name || channel.channel_name}
                            ${!isActive ? '<span class="text-xs text-gray-400 ml-2">(已暂停)</span>' : ''}
                        </div>
                        <div class="text-sm text-gray-500 flex items-center gap-2">
                            <span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${isActive ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}">
                                ${isActive ? '活跃' : '暂停'} • ${channel.category || '未分类'}
                            </span>
                            <span>订阅者: ${Utils.formatNumber(channel.subscriber_count || 0)}</span>
                        </div>
                    </div>
                    <div class="flex-shrink-0 text-sm text-gray-400">
                        ${isSelected ? '<i class="fas fa-check text-purple-600"></i>' : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    listEl.innerHTML = html;
}

// 绑定频道筛选事件
function bindChannelFilterEvents() {
    // 搜索框事件
    const searchInput = document.getElementById('channelFilterSearch');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            channelFilterData.searchTerm = e.target.value.toLowerCase();
            filterChannelList();
        });
    }
    
    // 分类筛选事件
    const categorySelect = document.getElementById('categoryFilter');
    if (categorySelect) {
        categorySelect.addEventListener('change', (e) => {
            channelFilterData.selectedCategory = e.target.value;
            filterChannelList();
        });
    }
    
    // 状态筛选事件
    const statusSelect = document.getElementById('statusFilter');
    if (statusSelect) {
        statusSelect.addEventListener('change', (e) => {
            channelFilterData.selectedStatus = e.target.value;
            filterChannelList();
        });
    }
}

// 筛选频道列表
function filterChannelList() {
    const { allChannels, searchTerm, selectedCategory, selectedStatus } = channelFilterData;
    
    channelFilterData.filteredChannels = allChannels.filter(channel => {
        const name = (channel.name || channel.channel_name || '').toLowerCase();
        const category = channel.category || '';
        const isActive = channel.is_active;
        
        const matchesSearch = !searchTerm || name.includes(searchTerm);
        const matchesCategory = !selectedCategory || category === selectedCategory;
        const matchesStatus = !selectedStatus || 
            (selectedStatus === 'active' && isActive) ||
            (selectedStatus === 'inactive' && !isActive);
        
        return matchesSearch && matchesCategory && matchesStatus;
    });
    
    renderChannelFilterList();
}

// 切换频道选择状态
function toggleChannelSelection(channelId) {
    const { selectedChannelIds } = channelFilterData;
    const index = selectedChannelIds.indexOf(channelId);
    
    if (index > -1) {
        selectedChannelIds.splice(index, 1);
    } else {
        selectedChannelIds.push(channelId);
    }
    
    renderChannelFilterList();
    updateSelectedChannelCount();
}

// 全选频道
function selectAllChannels() {
    const { filteredChannels } = channelFilterData;
    filteredChannels.forEach(channel => {
        if (!channelFilterData.selectedChannelIds.includes(channel.id)) {
            channelFilterData.selectedChannelIds.push(channel.id);
        }
    });
    
    renderChannelFilterList();
    updateSelectedChannelCount();
}

// 清空选择
function clearAllChannels() {
    const { filteredChannels } = channelFilterData;
    filteredChannels.forEach(channel => {
        const index = channelFilterData.selectedChannelIds.indexOf(channel.id);
        if (index > -1) {
            channelFilterData.selectedChannelIds.splice(index, 1);
        }
    });
    
    renderChannelFilterList();
    updateSelectedChannelCount();
}

// 反选频道
function toggleAllChannelSelection() {
    const { filteredChannels, selectedChannelIds } = channelFilterData;
    
    filteredChannels.forEach(channel => {
        const index = selectedChannelIds.indexOf(channel.id);
        if (index > -1) {
            selectedChannelIds.splice(index, 1);
        } else {
            selectedChannelIds.push(channel.id);
        }
    });
    
    renderChannelFilterList();
    updateSelectedChannelCount();
}

// 更新选择数量显示
function updateSelectedChannelCount() {
    const countEl = document.getElementById('selectedChannelCount');
    if (countEl) {
        countEl.textContent = channelFilterData.selectedChannelIds.length;
    }
}

// 应用频道筛选
function applyChannelFilter() {
    const { selectedChannelIds } = channelFilterData;
    
    if (selectedChannelIds.length === 0) {
        showToast('请至少选择一个频道', 'warning');
        return;
    }
    
    // 应用筛选到DataTable
    if (dataTableInstance) {
        dataTableInstance.setSelectedChannels(selectedChannelIds);
        showToast(`已筛选 ${selectedChannelIds.length} 个频道`, 'success');
    } else {
        // 尝试初始化
        const dataTableElement = document.getElementById('data-table');
        if (dataTableElement) {
            dataTableInstance = new DataTable();
            dataTableInstance.setSelectedChannels(selectedChannelIds);
            showToast(`已筛选 ${selectedChannelIds.length} 个频道`, 'success');
        } else {
            showToast('数据表格元素未找到', 'error');
        }
    }
    
    // 关闭弹层
    closeChannelFilterModal();
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { DataTable };
}