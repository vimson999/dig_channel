// 数据表格模块
const DATA_TABLE_DEBUG = false;
const dataTableLog = (...args) => {
    if (DATA_TABLE_DEBUG) {
        console.log(...args);
    }
};

class DataTable {
    constructor() {
        dataTableLog('📊 [DataTable] 构造函数被调用，初始化数据表格...');
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
        dataTableLog('📊 [DataTable] init方法被调用...');
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
            dataTableLog('📊 [DataTable] API响应:', response);
            this.tableData = response.data;
            dataTableLog('📊 [DataTable] 处理后的数据:', this.tableData);
            
            this.renderTable();
            this.renderPagination(this.tableData.pagination);
            
            this.hideLoading();
            dataTableLog('📊 [DataTable] 数据加载完成，已隐藏加载状态');
            
        } catch (error) {
            console.error('加载表格数据失败:', error);
            this.hideLoading();
            this.showError('加载表格数据失败');
        }
    }

    // 渲染表格 - 复用new-main.js的renderTable方法
    renderTable() {
        dataTableLog('📊 [DataTable] 开始渲染表格...');
        const table = document.getElementById('data-table');
        if (!table) {
            console.error('📊 [DataTable] 未找到data-table元素');
            return;
        }
        const thead = table.querySelector('thead');
        const tbody = table.querySelector('tbody');
        
        // 清空tbody，移除"正在加载数据..."
        dataTableLog('📊 [DataTable] 清空tbody，移除加载状态...');
        tbody.innerHTML = '';
        dataTableLog('📊 [DataTable] tbody已清空');
        
        if (!this.tableData || !this.tableData.channels || this.tableData.channels.length === 0) {
            dataTableLog('📊 [DataTable] 无数据，显示空状态');
            thead.innerHTML = '<tr><th class="sticky left-0 bg-gray-50 px-6 py-4 border-b border-gray-200 font-medium text-gray-500 text-left">日期</th></tr>';
            tbody.innerHTML = '<tr><td colspan="100%" class="text-center text-gray-500 py-8">暂无数据</td></tr>';
            return;
        }
        
        dataTableLog('📊 [DataTable] 有数据，渲染复杂表格，频道数:', this.tableData.channels.length);
        
        // 使用和new-main.js完全相同的渲染逻辑
        this.renderComplexTable(thead, tbody, this.tableData);
        dataTableLog('📊 [DataTable] 复杂表格渲染完成');
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
        dataTableLog('📊 [DataTable] tbody已更新，新内容长度:', bodyHtml.length, '字符');
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
        const tbody = document.querySelector('#data-table tbody');
        if (tbody && !this.tableData) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="100%" class="text-center text-gray-400 py-8">
                        <div class="inline-flex items-center gap-2">
                            <span class="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full"></span>
                            <span>${message}</span>
                        </div>
                    </td>
                </tr>
            `;
        }
    }

    // 隐藏加载状态
    hideLoading() {
        // 表格加载状态会在 renderTable 中被真实数据覆盖。
    }

    // 显示错误
    showError(message) {
        const tbody = document.querySelector('#data-table tbody');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="100%" class="text-center text-red-500 py-8">${message}</td></tr>`;
        }
    }

    // 加载所有频道列表
    async loadAllChannels() {
        try {
            // 请求所有频道（包括非活跃的）用于筛选
            const response = await api.getChannels({ include_inactive: true, per_page: 1000 });
            this.allChannels = response.data.channels || [];
            
            // 移除默认全选逻辑，保持 selectedChannelIds 为空（代表显示全部）
            // 除非已经有选中的频道，否则不进行自动填充
            
            return this.allChannels;
        } catch (error) {
            console.error('加载频道列表失败:', error);
            return [];
        }
    }

    // 设置日期范围（从今天往前推指定天数）
    setRange(days) {
        dataTableLog(`📊 [DataTable] 设置日期范围: ${days} 天`);
        const endDate = new Date();
        const startDate = new Date();
        
        if (days === 7) {
            // 特殊逻辑：最近 7 天默认为本月 1 日到今天，保持与 new-main.js 一致
            startDate.setDate(1);
        } else {
            startDate.setDate(endDate.getDate() - parseInt(days, 10));
        }
        
        this.startDate = Utils.formatDate(startDate);
        this.endDate = Utils.formatDate(endDate);
        this.currentPage = 1; // 重置到第一页
        
        return this.loadTableData();
    }

    // 设置选中的频道
    setSelectedChannels(channelIds) {
        this.selectedChannelIds = channelIds || [];
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

function initializeDataTable() {
    const dataTableElement = document.getElementById('data-table');
    if (dataTableElement) {
        if (!dataTableInstance) {
            dataTableLog('📊 [DataTable] 创建DataTable实例...');
            dataTableInstance = new DataTable();
        } else {
            dataTableInstance.refresh();
        }
    } else {
        dataTableLog('📊 [DataTable] 未找到data-table元素，跳过初始化');
    }
}

document.addEventListener('DOMContentLoaded', function() {
    dataTableLog('📊 [DataTable] DOM加载完成');

    if (window.DEFER_DATA_TABLE_INIT === true) {
        dataTableLog('📊 [DataTable] 已延迟到主应用认证后初始化');
        return;
    }

    initializeDataTable();
    
    // 监听标签页切换
    document.addEventListener('shown.bs.tab', function(e) {
        if (e.target.getAttribute('href') === '#data-table') {
            initializeDataTable();
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

let channelFilterCategories = [];

function buildFallbackChannelCategories() {
    if (!channelFilterData.allChannels || channelFilterData.allChannels.length === 0) {
        return [
            '科技', '生活', '娱乐', '游戏', '教育', '音乐', '美食', '旅行', '其他'
        ].map(name => ({ name, count: 0 }));
    }

    const categoryCounts = channelFilterData.allChannels.reduce((acc, channel) => {
        const rawName = channel && channel.category;
        const name = rawName && rawName.toString().trim();
        if (!name) {
            return acc;
        }
        acc[name] = (acc[name] || 0) + 1;
        return acc;
    }, {});

    const categories = Object.entries(categoryCounts).map(([name, count]) => ({ name, count }));
    return categories.sort((a, b) => {
        if (b.count !== a.count) {
            return b.count - a.count;
        }
        return a.name.localeCompare(b.name, 'zh-Hans-CN');
    });
}

function populateCategoryFilterOptions() {
    const select = document.getElementById('categoryFilter');
    if (!select) return;

    const previousValue = select.value || channelFilterData.selectedCategory || '';

    const categoriesToRender = (channelFilterCategories && channelFilterCategories.length)
        ? channelFilterCategories
        : buildFallbackChannelCategories();

    select.innerHTML = '';

    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = '所有分类';
    select.appendChild(defaultOption);

    const seen = new Set();
    categoriesToRender.forEach(category => {
        const name = (category && (category.name || category.category || category))
            ? category.name || category.category || category
            : '';
        const trimmedName = name && name.toString().trim();
        if (!trimmedName || seen.has(trimmedName)) {
            return;
        }
        seen.add(trimmedName);

        const option = document.createElement('option');
        option.value = trimmedName;
        if (category && typeof category.count === 'number') {
            option.textContent = `${trimmedName} (${category.count})`;
        } else {
            option.textContent = trimmedName;
        }
        select.appendChild(option);
    });

    const optionsArray = Array.from(select.options);
    const hasPrevious = previousValue && optionsArray.some(option => option.value === previousValue);

    if (hasPrevious) {
        select.value = previousValue;
    } else {
        select.value = '';
        if (channelFilterData.selectedCategory) {
            channelFilterData.selectedCategory = '';
        }
    }
}

async function loadChannelFilterCategories() {
    const select = document.getElementById('categoryFilter');
    if (!select) return;

    try {
        const response = await api.getChannelCategories();
        if (response.success && response.data && Array.isArray(response.data.categories) && response.data.categories.length) {
            channelFilterCategories = response.data.categories.filter(category => {
                const name = category && (category.name || category.category);
                return Boolean(name);
            });
        } else {
            console.warn('[ChannelFilter] 未获取到分类数据，使用本地分类统计');
            channelFilterCategories = [];
        }
    } catch (error) {
        console.error('加载频道分类失败:', error);
        channelFilterCategories = [];
    }

    populateCategoryFilterOptions();
    filterChannelList();
    updateSelectedChannelCount();
}

// 打开频道筛选弹层
async function openChannelFilterModal() {
    const modal = document.getElementById('channelFilterModal');
    const content = document.getElementById('channelFilterContent');
    
    if (modal && content) {
        // 显式绑定全局函数，确保不会被 new-main.js 的绑定覆盖
        window.selectAllChannels = selectAllChannels;
        window.clearAllChannels = clearAllChannels;
        window.toggleAllChannelSelection = toggleAllChannelSelection;
        window.toggleChannelSelection = toggleChannelSelection;
        window.applyChannelFilter = applyChannelFilter;
        window.closeChannelFilterModal = closeChannelFilterModal;

        // 显示弹层
        modal.classList.remove('hidden');
        setTimeout(() => {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }, 10);
        
        // 加载频道列表
        await loadChannelFilterList();

        // 同步加载频道分类
        await loadChannelFilterCategories();
        
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
            // 复制一份，避免直接操作 DataTable 的私有状态
            channelFilterData.selectedChannelIds = [...(dataTableInstance.getSelectedChannels() || [])];
        } else {
            // 请求所有频道（包括非活跃的）用于筛选
            const response = await api.getChannels({ include_inactive: true, per_page: 1000 });
            channels = response.data.channels || [];
            // 初始加载时不默认选择任何频道，除非通过 DataTable 获取
            if (!channelFilterData.selectedChannelIds || channelFilterData.selectedChannelIds.length === 0) {
                channelFilterData.selectedChannelIds = [];
            }
        }
        
        channelFilterData.allChannels = channels;
        channelFilterData.filteredChannels = channels;

        populateCategoryFilterOptions();

        if (loadingEl) loadingEl.style.display = 'none';

        filterChannelList();
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
                               class="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500 pointer-events-none"
                               readonly>
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
        // 克隆节点以移除旧的事件监听器（防止与 new-main.js 冲突）
        const newSearchInput = searchInput.cloneNode(true);
        searchInput.parentNode.replaceChild(newSearchInput, searchInput);
        newSearchInput.value = channelFilterData.searchTerm || '';
        newSearchInput.addEventListener('input', (e) => {
            channelFilterData.searchTerm = e.target.value.toLowerCase();
            filterChannelList();
        });
    }
    
    // 分类筛选事件
    const categorySelect = document.getElementById('categoryFilter');
    if (categorySelect) {
        const newCategorySelect = categorySelect.cloneNode(true);
        categorySelect.parentNode.replaceChild(newCategorySelect, categorySelect);
        newCategorySelect.value = channelFilterData.selectedCategory || '';
        newCategorySelect.addEventListener('change', (e) => {
            channelFilterData.selectedCategory = e.target.value;
            filterChannelList();
        });
    }
    
    // 状态筛选事件
    const statusSelect = document.getElementById('statusFilter');
    if (statusSelect) {
        const newStatusSelect = statusSelect.cloneNode(true);
        statusSelect.parentNode.replaceChild(newStatusSelect, statusSelect);
        newStatusSelect.value = channelFilterData.selectedStatus || 'active';
        newStatusSelect.addEventListener('change', (e) => {
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
    // 改为清空所有选择，而不仅仅是过滤后的
    channelFilterData.selectedChannelIds = [];
    
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
    
    // 应用筛选到DataTable
    if (dataTableInstance) {
        dataTableInstance.setSelectedChannels(selectedChannelIds);
        
        if (selectedChannelIds.length === 0) {
            showToast('已重置为显示全部频道', 'success');
        } else {
            showToast(`已筛选 ${selectedChannelIds.length} 个频道`, 'success');
        }
    } else {
        // 尝试初始化
        const dataTableElement = document.getElementById('data-table');
        if (dataTableElement) {
            dataTableInstance = new DataTable();
            dataTableInstance.setSelectedChannels(selectedChannelIds);
            
            if (selectedChannelIds.length === 0) {
                showToast('已重置为显示全部频道', 'success');
            } else {
                showToast(`已筛选 ${selectedChannelIds.length} 个频道`, 'success');
            }
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
