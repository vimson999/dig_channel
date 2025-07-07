// 数据表格模块
class DataTable {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 10;
        this.startDate = null;
        this.endDate = null;
        this.tableData = null;
        
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

    // 设置默认日期（最近30天）
    setDefaultDates() {
        const endDate = new Date();
        const startDate = new Date();
        startDate.setDate(endDate.getDate() - 30);
        
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

    // 渲染表格
    renderTable() {
        const table = document.getElementById('data-table-content');
        if (!table) return;

        const tbody = table.querySelector('tbody');
        const thead = table.querySelector('thead tr');
        
        if (!this.tableData || !this.tableData.channels || this.tableData.channels.length === 0) {
            tbody.innerHTML = '<tr><td colspan="100%" class="text-center text-muted">暂无数据</td></tr>';
            return;
        }

        // 渲染表头
        this.renderTableHeader(thead);
        
        // 渲染表体
        this.renderTableBody(tbody);
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

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { DataTable };
}