// 主JavaScript文件 - 全局功能和初始化
class App {
    constructor() {
        this.currentTab = 'dashboard';
        this.toastContainer = null;
        
        this.init();
    }

    // 初始化应用
    init() {
        this.createToastContainer();
        this.bindGlobalEvents();
        this.initializeBootstrapComponents();
        this.loadInitialData();
    }

    // 创建提示消息容器
    createToastContainer() {
        if (!document.getElementById('toast-container')) {
            const container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container position-fixed top-0 end-0 p-3';
            container.style.zIndex = '9999';
            document.body.appendChild(container);
            this.toastContainer = container;
        }
    }

    // 绑定全局事件
    bindGlobalEvents() {
        // 标签页切换事件
        document.addEventListener('shown.bs.tab', (e) => {
            try {
                const target = e.target.getAttribute('data-bs-target') || e.target.getAttribute('href');
                if (target) {
                    const tabId = target.substring(1);
                    this.currentTab = tabId;
                    this.onTabChanged(tabId);
                }
            } catch (error) {
                console.warn('Tab切换处理失败:', error);
            }
        });

        // 键盘快捷键
        document.addEventListener('keydown', (e) => {
            this.handleKeyboardShortcuts(e);
        });

        // 窗口大小变化
        window.addEventListener('resize', Utils.debounce(() => {
            this.handleWindowResize();
        }, 250));

        // 页面卸载前确认
        window.addEventListener('beforeunload', (e) => {
            // 如果有未保存的更改，显示确认对话框
            // 这里可以根据实际需求添加逻辑
        });
    }

    // 初始化Bootstrap组件
    initializeBootstrapComponents() {
        // 初始化工具提示
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });

        // 初始化弹出框
        const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
        popoverTriggerList.map(function (popoverTriggerEl) {
            return new bootstrap.Popover(popoverTriggerEl);
        });
    }

    // 加载初始数据
    async loadInitialData() {
        try {
            // 检查API连接状态
            await this.checkAPIStatus();
            
            // 初始化当前激活的标签页
            const activeTab = document.querySelector('.nav-link.active');
            if (activeTab) {
                const tabId = activeTab.getAttribute('href').substring(1);
                this.onTabChanged(tabId);
            }
            
        } catch (error) {
            console.error('初始化失败:', error);
            this.showToast('应用初始化失败，请刷新页面重试', 'error');
        }
    }

    // 检查API状态
    async checkAPIStatus() {
        try {
            const baseUrl = CONFIG.API_BASE_URL.replace('/api/v1', '');
            const response = await fetch(baseUrl + '/health');
            if (!response.ok) {
                throw new Error('API服务不可用');
            }
            console.log('API连接正常');
        } catch (error) {
            console.error('API连接失败:', error);
            this.showToast('无法连接到服务器，部分功能可能不可用', 'warning');
        }
    }

    // 标签页切换处理
    onTabChanged(tabId) {
        console.log(`切换到标签页: ${tabId}`);
        
        // 更新页面标题
        const titles = {
            'dashboard': 'YouTube频道数据追踪工具 - 仪表板',
            'rankings': 'YouTube频道数据追踪工具 - 排行榜',
            'channels': 'YouTube频道数据追踪工具 - 频道管理',
            'data-table': 'YouTube频道数据追踪工具 - 数据表格'
        };
        
        document.title = titles[tabId] || 'YouTube频道数据追踪工具';
        
        // 保存当前标签页到本地存储
        Utils.setStorageItem('current_tab', tabId);
    }

    // 处理键盘快捷键
    handleKeyboardShortcuts(e) {
        // Ctrl/Cmd + R: 刷新当前页面数据
        if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
            e.preventDefault();
            this.refreshCurrentTab();
        }
        
        // Ctrl/Cmd + N: 添加新频道
        if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
            e.preventDefault();
            openAddChannelModal();
        }
        
        // Esc: 关闭模态框
        if (e.key === 'Escape') {
            const openModals = document.querySelectorAll('.modal.show');
            openModals.forEach(modal => {
                const modalInstance = bootstrap.Modal.getInstance(modal);
                if (modalInstance) {
                    modalInstance.hide();
                }
            });
        }
    }

    // 处理窗口大小变化
    handleWindowResize() {
        // 重新调整图表大小
        if (typeof Chart !== 'undefined') {
            Chart.helpers.each(Chart.instances, function(instance) {
                instance.resize();
            });
        }
    }

    // 刷新当前标签页数据
    refreshCurrentTab() {
        switch (this.currentTab) {
            case 'dashboard':
                if (dashboardInstance) {
                    dashboardInstance.refresh();
                }
                break;
            case 'rankings':
                if (rankingsInstance) {
                    rankingsInstance.refresh();
                }
                break;
            case 'channels':
                if (channelsInstance) {
                    channelsInstance.refresh();
                }
                break;
            case 'data-table':
                if (dataTableInstance) {
                    dataTableInstance.refresh();
                }
                break;
        }
        
        this.showToast('数据已刷新', 'success');
    }

    // 显示提示消息
    showToast(message, type = 'info', duration = 3000, errorDetails = null) {
        const toastId = 'toast_' + Date.now();
        const toastTypes = {
            'success': 'bg-success',
            'error': 'bg-danger',
            'warning': 'bg-warning',
            'info': 'bg-info'
        };
        
        const toastClass = toastTypes[type] || toastTypes.info;
        const iconMap = {
            'success': 'check-circle',
            'error': 'exclamation-triangle',
            'warning': 'exclamation-triangle',
            'info': 'info-circle'
        };
        
        const icon = iconMap[type] || iconMap.info;
        
        // 构建Toast内容
        let toastBody = `<i class="bi bi-${icon} me-2"></i>${message}`;
        
        // 如果有错误详情，添加展开按钮
        if (errorDetails && (errorDetails.error_id || errorDetails.details)) {
            toastBody += `
                <div class="mt-2">
                    <button class="btn btn-sm btn-outline-light" type="button" 
                            onclick="this.nextElementSibling.classList.toggle('d-none')">
                        <i class="bi bi-info-circle me-1"></i>详情
                    </button>
                    <div class="d-none mt-1 small">
                        ${errorDetails.error_id ? `<div>错误ID: ${errorDetails.error_id}</div>` : ''}
                        ${errorDetails.timestamp ? `<div>时间: ${new Date(errorDetails.timestamp).toLocaleString()}</div>` : ''}
                        ${errorDetails.details ? `<div>详情: ${JSON.stringify(errorDetails.details)}</div>` : ''}
                    </div>
                </div>
            `;
        }
        
        const toastHTML = `
            <div id="${toastId}" class="toast ${toastClass} text-white" role="alert">
                <div class="d-flex">
                    <div class="toast-body">
                        ${toastBody}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;
        
        this.toastContainer.insertAdjacentHTML('beforeend', toastHTML);
        
        const toastElement = document.getElementById(toastId);
        const toast = new bootstrap.Toast(toastElement, {
            autohide: true,
            delay: duration
        });
        
        toast.show();
        
        // 自动清理DOM
        toastElement.addEventListener('hidden.bs.toast', () => {
            toastElement.remove();
        });
    }
}

// 全局函数 - 添加频道模态框
function openAddChannelModal() {
    const modal = new bootstrap.Modal(document.getElementById('addChannelModal'));
    modal.show();
    
    // 清空表单
    document.getElementById('addChannelForm').reset();
    document.getElementById('channel-preview').classList.add('d-none');
}

// 预览频道
async function previewChannel() {
    const urlInput = document.getElementById('channel-url');
    const url = urlInput.value.trim();
    
    if (!url) {
        showToast('请输入频道URL', 'warning');
        return;
    }
    
    if (!Utils.isValidYouTubeURL(url)) {
        showToast('请输入有效的YouTube频道URL', 'warning');
        return;
    }
    
    try {
        showLoading('正在预览频道...');
        
        const response = await api.previewChannel(url);
        const channelInfo = response.data.channel_info;
        const alreadyExists = response.data.already_exists;
        
        // 显示预览信息
        const previewContainer = document.getElementById('channel-preview');
        const previewContent = document.getElementById('preview-content');
        
        previewContent.innerHTML = `
            <div class="row">
                <div class="col-md-4">
                    <img src="${channelInfo.thumbnail_url}" class="img-fluid rounded" alt="频道头像">
                </div>
                <div class="col-md-8">
                    <h6>${channelInfo.title}</h6>
                    <p class="small text-muted">${channelInfo.description.substring(0, 100)}...</p>
                    <div class="row">
                        <div class="col-6">
                            <strong>订阅者:</strong> ${Utils.formatNumber(channelInfo.subscriber_count)}
                        </div>
                        <div class="col-6">
                            <strong>视频数:</strong> ${Utils.formatNumber(channelInfo.video_count)}
                        </div>
                        <div class="col-6">
                            <strong>总观看:</strong> ${Utils.formatNumber(channelInfo.view_count)}
                        </div>
                        <div class="col-6">
                            <strong>国家:</strong> ${channelInfo.country || 'N/A'}
                        </div>
                    </div>
                    ${alreadyExists ? '<div class="alert alert-warning mt-2">此频道已存在于系统中</div>' : ''}
                </div>
            </div>
        `;
        
        previewContainer.classList.remove('d-none');
        
        // 自动填入频道名称
        const nameInput = document.getElementById('channel-name');
        if (!nameInput.value) {
            nameInput.value = channelInfo.title;
        }
        
        hideLoading();
        
    } catch (error) {
        console.error('预览频道失败:', error);
        hideLoading();
        showToast('预览频道失败: ' + error.getUserMessage(), 'error');
    }
}

// 添加频道
async function addChannel() {
    const form = document.getElementById('addChannelForm');
    const formData = new FormData(form);
    
    const channelData = {
        url: document.getElementById('channel-url').value.trim(),
        name: document.getElementById('channel-name').value.trim() || null,
        cpm: parseFloat(document.getElementById('channel-cpm').value) || 1.00,
        category: document.getElementById('channel-category').value.trim() || null
    };
    
    // 验证数据
    if (!channelData.url) {
        showToast('请输入频道URL', 'warning');
        return;
    }
    
    if (!Utils.isValidYouTubeURL(channelData.url)) {
        showToast('请输入有效的YouTube频道URL', 'warning');
        return;
    }
    
    if (channelData.cpm < 0 || channelData.cpm > 100) {
        showToast('CPM值应该在0-100之间', 'warning');
        return;
    }
    
    try {
        showLoading('正在添加频道...');
        
        const response = await api.createChannel(channelData);
        
        showToast('频道添加成功', 'success');
        
        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('addChannelModal'));
        modal.hide();
        
        // 刷新频道列表
        if (channelsInstance) {
            channelsInstance.refresh();
        }
        
        hideLoading();
        
    } catch (error) {
        console.error('添加频道失败:', error);
        hideLoading();
        showToast('添加频道失败: ' + error.getUserMessage(), 'error');
    }
}

// 同步所有频道数据
async function syncAllChannels() {
    if (!confirm('确定要同步所有频道的数据吗？这可能需要一些时间。')) {
        return;
    }
    
    try {
        showLoading('正在同步所有频道数据...');
        
        const response = await api.syncAllChannels();
        const result = response.data;
        
        showToast(
            `同步完成: 成功 ${result.success_count}，失败 ${result.failed_count}`, 
            result.failed_count > 0 ? 'warning' : 'success'
        );
        
        // 刷新当前页面数据
        app.refreshCurrentTab();
        
        hideLoading();
        
    } catch (error) {
        console.error('同步数据失败:', error);
        hideLoading();
        showToast('同步数据失败: ' + error.getUserMessage(), 'error');
    }
}

// 导出数据
async function exportData() {
    try {
        showLoading('正在准备导出数据...');
        
        await api.exportData();
        
        showToast('数据导出成功', 'success');
        hideLoading();
        
    } catch (error) {
        console.error('导出数据失败:', error);
        hideLoading();
        showToast('导出数据失败: ' + error.getUserMessage(), 'error');
    }
}

// 全局工具函数
function showToast(message, type, duration, errorDetails) {
    if (app) {
        app.showToast(message, type, duration, errorDetails);
    }
}

// 全局错误处理函数
function handleAPIError(error, defaultMessage = '操作失败') {
    console.error('API错误:', error);
    
    let message = defaultMessage;
    let errorDetails = null;
    
    if (error instanceof APIError) {
        message = error.getUserMessage();
        errorDetails = {
            error_id: error.getErrorId(),
            error_type: error.name,
            status: error.status,
            timestamp: new Date().toISOString(),
            details: error.details
        };
    } else if (error.message) {
        message = error.message;
    }
    
    // 根据错误类型选择提示类型
    let toastType = 'error';
    if (error instanceof APIError) {
        if (error.isNetworkError()) {
            toastType = 'warning';
        } else if (error.isClientError()) {
            toastType = 'warning';
        }
    }
    
    showToast(message, toastType, 5000, errorDetails);
}

function showLoading(message) {
    const statusBar = document.getElementById('status-bar');
    const statusText = document.getElementById('status-text');
    
    if (statusBar && statusText) {
        statusText.textContent = message || '正在加载...';
        statusBar.classList.remove('d-none', 'alert-danger');
        statusBar.classList.add('alert-info');
    }
}

function hideLoading() {
    const statusBar = document.getElementById('status-bar');
    if (statusBar) {
        statusBar.classList.add('d-none');
    }
}

// 应用实例
let app;

// 当DOM加载完成时初始化应用
document.addEventListener('DOMContentLoaded', function() {
    app = new App();
    console.log('应用初始化完成');
});

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { App };
}