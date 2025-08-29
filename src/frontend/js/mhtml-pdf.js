// MHTML转PDF功能 JavaScript
console.log('📄 [MHTML-PDF] MHTML转PDF模块已加载');

class MHTMLToPDFManager {
    constructor() {
        this.selectedFiles = [];
        this.isConverting = false;
        this.conversionResults = [];
        
        this.init();
    }
    
    async init() {
        console.log('🚀 [MHTML-PDF] 管理器启动中...');
        
        // 检查认证状态
        const isAuthenticated = await this.checkAuth();
        if (!isAuthenticated) {
            console.log('❌ [MHTML-PDF] 用户未认证，重定向到登录页');
            window.location.href = 'login.html';
            return;
        }
        
        // 检查服务状态
        await this.checkServiceStatus();
        
        console.log('✅ [MHTML-PDF] 管理器初始化完成');
    }
    
    async checkAuth() {
        try {
            const token = localStorage.getItem('access_token');
            if (!token) {
                return false;
            }
            
            const response = await api.verifyToken();
            return response && response.success;
        } catch (error) {
            console.error('认证检查失败:', error);
            return false;
        }
    }
    
    async checkServiceStatus() {
        try {
            showStatusBar('检查服务状态...');
            
            const response = await fetch(`${API_BASE_URL}/mhtml-pdf/status`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            const result = await response.json();
            const statusElement = document.getElementById('service-status');
            
            if (result.success && result.data.chrome_available) {
                statusElement.textContent = '服务正常';
                statusElement.className = 'text-xs px-2 py-1 rounded-full bg-green-100 text-green-700';
                
                console.log('✅ [MHTML-PDF] Chrome可用:', result.data.chrome_path);
            } else {
                statusElement.textContent = '服务不可用';
                statusElement.className = 'text-xs px-2 py-1 rounded-full bg-red-100 text-red-700';
                showToast('MHTML转PDF服务暂不可用: ' + (result.data.error_message || '未知错误'), 'error');
            }
            
            hideStatusBar();
            
        } catch (error) {
            console.error('检查服务状态失败:', error);
            const statusElement = document.getElementById('service-status');
            statusElement.textContent = '检查失败';
            statusElement.className = 'text-xs px-2 py-1 rounded-full bg-red-100 text-red-700';
            hideStatusBar();
        }
    }
    
    addFiles(files) {
        for (let file of files) {
            // 检查文件类型
            if (!this.isValidFile(file)) {
                showToast(`不支持的文件格式: ${file.name}`, 'warning');
                continue;
            }
            
            // 检查文件大小 (50MB)
            if (file.size > 50 * 1024 * 1024) {
                showToast(`文件过大: ${file.name} (最大50MB)`, 'warning');
                continue;
            }
            
            // 检查重复
            if (this.selectedFiles.find(f => f.name === file.name && f.size === file.size)) {
                showToast(`文件已存在: ${file.name}`, 'info');
                continue;
            }
            
            // 检查数量限制
            if (this.selectedFiles.length >= 10) {
                showToast('最多只能选择10个文件', 'warning');
                break;
            }
            
            this.selectedFiles.push(file);
        }
        
        this.renderFileList();
        this.updateUI();
    }
    
    isValidFile(file) {
        const validExtensions = ['.mhtml', '.mht'];
        return validExtensions.some(ext => file.name.toLowerCase().endsWith(ext));
    }
    
    removeFile(index) {
        this.selectedFiles.splice(index, 1);
        this.renderFileList();
        this.updateUI();
    }
    
    clearFiles() {
        this.selectedFiles = [];
        this.conversionResults = [];
        this.renderFileList();
        this.updateUI();
        this.hideResults();
    }
    
    renderFileList() {
        const fileList = document.getElementById('file-list');
        
        if (this.selectedFiles.length === 0) {
            fileList.classList.add('hidden');
            return;
        }
        
        fileList.classList.remove('hidden');
        
        const filesHtml = this.selectedFiles.map((file, index) => {
            const fileSize = this.formatFileSize(file.size);
            const fileType = file.name.toLowerCase().endsWith('.mhtml') ? 'MHTML' : 'MHT';
            
            return `
                <div class="file-item rounded-lg p-4">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-file-alt text-orange-600"></i>
                            </div>
                            <div>
                                <h5 class="font-medium text-gray-800 text-sm">${file.name}</h5>
                                <div class="flex items-center gap-2 text-xs text-gray-500">
                                    <span class="px-2 py-1 bg-gray-100 rounded">${fileType}</span>
                                    <span>${fileSize}</span>
                                </div>
                            </div>
                        </div>
                        <button onclick="mhtmlManager.removeFile(${index})" 
                                class="p-1 text-gray-400 hover:text-red-500 transition-colors" 
                                title="移除文件">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
        
        const fileListContainer = fileList.querySelector('div:last-child') || (() => {
            const div = document.createElement('div');
            div.className = 'space-y-3';
            fileList.appendChild(div);
            return div;
        })();
        
        fileListContainer.innerHTML = filesHtml;
        
        // 添加清空按钮
        if (this.selectedFiles.length > 1) {
            fileListContainer.innerHTML += `
                <div class="pt-3 border-t border-gray-200">
                    <button onclick="mhtmlManager.clearFiles()" 
                            class="text-sm text-gray-500 hover:text-gray-700 transition-colors">
                        <i class="fas fa-trash mr-1"></i>清空所有文件
                    </button>
                </div>
            `;
        }
    }
    
    updateUI() {
        const hasFiles = this.selectedFiles.length > 0;
        
        // 显示/隐藏相关区域
        const optionsElement = document.getElementById('conversion-options');
        const actionsElement = document.getElementById('convert-actions');
        
        if (hasFiles) {
            optionsElement.classList.remove('hidden');
            actionsElement.classList.remove('hidden');
        } else {
            optionsElement.classList.add('hidden');
            actionsElement.classList.add('hidden');
        }
        
        // 更新转换按钮状态
        const convertBtn = document.getElementById('convert-btn');
        convertBtn.disabled = this.isConverting || !hasFiles;
        
        if (convertBtn.disabled) {
            convertBtn.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            convertBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    }
    
    async startConversion() {
        if (this.selectedFiles.length === 0 || this.isConverting) {
            return;
        }
        
        this.isConverting = true;
        this.updateUI();
        this.showProgress('准备转换...', 0);
        
        try {
            console.log(`📄 [MHTML-PDF] 开始转换 ${this.selectedFiles.length} 个文件`);
            
            // 获取转换选项
            const format = document.getElementById('format-select').value;
            const margin = document.getElementById('margin-select').value;
            
            // 单文件或批量转换
            if (this.selectedFiles.length === 1) {
                await this.convertSingleFile(this.selectedFiles[0], format, margin);
            } else {
                await this.convertMultipleFiles(format, margin);
            }
            
            this.showResults();
            showToast('转换完成!', 'success');
            
        } catch (error) {
            console.error('转换失败:', error);
            showToast('转换失败: ' + error.message, 'error');
        } finally {
            this.isConverting = false;
            this.updateUI();
            this.hideProgress();
        }
    }
    
    async convertSingleFile(file, format, margin) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('format', format);
        if (margin) {
            formData.append('margin_top', margin);
            formData.append('margin_right', margin);
            formData.append('margin_bottom', margin);
            formData.append('margin_left', margin);
        }
        
        this.updateProgress('转换中...', 50);
        
        const response = await fetch(`${API_BASE_URL}/mhtml-pdf/convert`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('access_token')}`
            },
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            this.updateProgress('转换完成!', 100);
            this.conversionResults = [{
                original_name: file.name,
                pdf_filename: result.data.pdf_filename,
                file_size: result.data.file_size,
                success: true
            }];
        } else {
            throw new Error(result.message || '转换失败');
        }
    }
    
    async convertMultipleFiles(format, margin) {
        const formData = new FormData();
        
        this.selectedFiles.forEach(file => {
            formData.append('files', file);
        });
        
        formData.append('format', format);
        if (margin) {
            formData.append('margin_top', margin);
            formData.append('margin_right', margin);
            formData.append('margin_bottom', margin);
            formData.append('margin_left', margin);
        }
        
        this.updateProgress('批量转换中...', 50);
        
        const response = await fetch(`${API_BASE_URL}/mhtml-pdf/batch-convert`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('access_token')}`
            },
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            this.updateProgress('批量转换完成!', 100);
            this.conversionResults = [
                ...result.data.converted_files.map(f => ({ ...f, success: true })),
                ...result.data.failed_files.map(f => ({ ...f, success: false }))
            ];
        } else {
            throw new Error(result.message || '批量转换失败');
        }
    }
    
    showProgress(text, percent) {
        const progressSection = document.getElementById('progress-section');
        const progressText = document.getElementById('progress-text');
        const progressFill = document.getElementById('progress-fill');
        
        progressSection.classList.remove('hidden');
        progressText.textContent = text;
        progressFill.style.width = `${percent}%`;
    }
    
    updateProgress(text, percent) {
        const progressText = document.getElementById('progress-text');
        const progressFill = document.getElementById('progress-fill');
        
        progressText.textContent = text;
        progressFill.style.width = `${percent}%`;
    }
    
    hideProgress() {
        const progressSection = document.getElementById('progress-section');
        progressSection.classList.add('hidden');
    }
    
    showResults() {
        const resultsSection = document.getElementById('results-section');
        const resultsList = document.getElementById('results-list');
        
        if (this.conversionResults.length === 0) {
            return;
        }
        
        const resultsHtml = this.conversionResults.map((result, index) => {
            if (result.success) {
                return `
                    <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-3">
                                <div class="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                                    <i class="fas fa-file-pdf text-green-600"></i>
                                </div>
                                <div>
                                    <h5 class="font-medium text-gray-800 text-sm">${result.original_name}</h5>
                                    <div class="text-xs text-gray-500">
                                        PDF大小: ${this.formatFileSize(result.file_size)}
                                    </div>
                                </div>
                            </div>
                            <button onclick="mhtmlManager.downloadPDF('${result.pdf_filename}')" 
                                    class="flex items-center gap-2 px-3 py-2 bg-green-500 text-white rounded-button hover:bg-green-600 transition-colors text-sm">
                                <i class="fas fa-download"></i>
                                <span>下载</span>
                            </button>
                        </div>
                    </div>
                `;
            } else {
                return `
                    <div class="bg-red-50 border border-red-200 rounded-lg p-4">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-exclamation-triangle text-red-600"></i>
                            </div>
                            <div>
                                <h5 class="font-medium text-gray-800 text-sm">${result.original_name}</h5>
                                <div class="text-xs text-red-600">
                                    转换失败: ${result.error || '未知错误'}
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }
        }).join('');
        
        resultsList.innerHTML = resultsHtml;
        resultsSection.classList.remove('hidden');
        
        // 滚动到结果区域
        resultsSection.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'nearest' 
        });
    }
    
    hideResults() {
        const resultsSection = document.getElementById('results-section');
        resultsSection.classList.add('hidden');
    }
    
    async downloadPDF(filename) {
        try {
            showStatusBar('准备下载...');
            
            const downloadUrl = `${API_BASE_URL}/mhtml-pdf/download/${filename}`;
            
            // 创建隐藏的下载链接
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = filename;
            link.target = '_blank';
            
            // 添加认证头（通过fetch下载）
            const token = localStorage.getItem('access_token');
            const response = await fetch(downloadUrl, {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            
            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                link.href = url;
                
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                window.URL.revokeObjectURL(url);
                
                showToast('开始下载PDF文件', 'success');
                console.log('📥 [MHTML-PDF] 开始下载:', filename);
            } else {
                throw new Error('下载请求失败');
            }
            
            hideStatusBar();
            
        } catch (error) {
            console.error('下载失败:', error);
            showToast('下载失败: ' + error.message, 'error');
            hideStatusBar();
        }
    }
    
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
}

// 拖拽上传处理函数
function dropHandler(ev) {
    ev.preventDefault();
    ev.currentTarget.classList.remove('drag-over');
    
    const files = Array.from(ev.dataTransfer.files);
    if (files.length > 0) {
        mhtmlManager.addFiles(files);
    }
}

function dragOverHandler(ev) {
    ev.preventDefault();
}

function dragEnterHandler(ev) {
    ev.preventDefault();
    ev.currentTarget.classList.add('drag-over');
}

function dragLeaveHandler(ev) {
    ev.preventDefault();
    ev.currentTarget.classList.remove('drag-over');
}

// 文件选择处理函数
function handleFileSelect(event) {
    const files = Array.from(event.target.files);
    if (files.length > 0) {
        mhtmlManager.addFiles(files);
    }
    
    // 重置input以允许选择相同文件
    event.target.value = '';
}

// 全局函数
function checkServiceStatus() {
    if (window.mhtmlManager) {
        window.mhtmlManager.checkServiceStatus();
    }
}

function startConversion() {
    if (window.mhtmlManager) {
        window.mhtmlManager.startConversion();
    }
}

// 通用状态和提示函数
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

    const toastId = 'toast-' + Date.now();
    const typeClasses = {
        'success': 'bg-green-500',
        'error': 'bg-red-500',
        'warning': 'bg-yellow-500',
        'info': 'bg-blue-500'
    };

    const bgClass = typeClasses[type] || typeClasses.info;

    const toast = document.createElement('div');
    toast.id = toastId;
    toast.className = `${bgClass} text-white px-4 py-3 rounded-lg shadow-lg transform transition-transform duration-300 translate-x-full`;
    toast.innerHTML = `
        <div class="flex items-center justify-between">
            <span>${message}</span>
            <button onclick="this.parentElement.parentElement.remove()" class="ml-3 text-white hover:text-gray-200">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `;

    container.appendChild(toast);

    // 动画显示
    setTimeout(() => {
        toast.classList.remove('translate-x-full');
    }, 10);

    // 自动隐藏
    setTimeout(() => {
        toast.classList.add('translate-x-full');
        setTimeout(() => {
            if (toast.parentElement) {
                toast.remove();
            }
        }, 300);
    }, duration);
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    window.mhtmlManager = new MHTMLToPDFManager();
    console.log('🎉 [MHTML-PDF] MHTML转PDF页面已启动');
});

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { MHTMLToPDFManager };
}