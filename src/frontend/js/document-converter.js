/**
 * 文档转换器JavaScript功能
 * 支持拖拽上传、格式转换、进度显示等
 */

class DocumentConverter {
    constructor() {
        this.init();
        this.selectedFile = null;
    }

    init() {
        this.setupEventListeners();
        this.updateFormatOptions();
    }

    setupEventListeners() {
        const uploadArea = document.getElementById('uploadArea');
        const fileInput = document.getElementById('fileInput');
        const convertBtn = document.getElementById('convertBtn');
        const sourceFormat = document.getElementById('sourceFormat');
        const targetFormat = document.getElementById('targetFormat');

        // 文件输入变化
        fileInput.addEventListener('change', (e) => {
            this.handleFileSelect(e.target.files[0]);
        });

        // 拖拽功能
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                this.handleFileSelect(files[0]);
            }
        });

        // 转换按钮
        convertBtn.addEventListener('click', () => {
            this.convertDocument();
        });

        // 格式选择变化
        sourceFormat.addEventListener('change', () => {
            this.updateFormatOptions();
            this.validateFile();
        });

        targetFormat.addEventListener('change', () => {
            this.validateConversion();
        });
    }

    updateFormatOptions() {
        const sourceFormat = document.getElementById('sourceFormat').value;
        const targetFormat = document.getElementById('targetFormat');
        
        // 清空目标格式选项
        targetFormat.innerHTML = '';
        
        // 根据源格式添加可用的目标格式
        if (sourceFormat === 'markdown') {
            targetFormat.innerHTML = '<option value="docx">Word Document (.docx)</option>';
        } else if (sourceFormat === 'docx') {
            targetFormat.innerHTML = '<option value="markdown">Markdown (.md)</option>';
        }
        
        this.validateConversion();
    }

    handleFileSelect(file) {
        if (!file) return;

        this.selectedFile = file;
        this.displayFileInfo(file);
        this.validateFile();
        this.hideError();
    }

    displayFileInfo(file) {
        const fileInfo = document.getElementById('fileInfo');
        const fileName = document.getElementById('fileName');
        const fileSize = document.getElementById('fileSize');
        const fileType = document.getElementById('fileType');

        fileName.textContent = file.name;
        fileSize.textContent = this.formatFileSize(file.size);
        fileType.textContent = file.type || '未知类型';

        fileInfo.classList.add('show');
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    validateFile() {
        if (!this.selectedFile) {
            this.disableConvertButton();
            return false;
        }

        const sourceFormat = document.getElementById('sourceFormat').value;
        const fileName = this.selectedFile.name.toLowerCase();
        
        let isValidFormat = false;
        
        if (sourceFormat === 'markdown') {
            isValidFormat = fileName.endsWith('.md') || fileName.endsWith('.markdown') || fileName.endsWith('.txt');
        } else if (sourceFormat === 'docx') {
            isValidFormat = fileName.endsWith('.docx') || fileName.endsWith('.doc');
        }

        if (!isValidFormat) {
            this.showError(`选择的文件格式与源格式不匹配。${sourceFormat === 'markdown' ? '请选择 .md 或 .txt 文件' : '请选择 .docx 或 .doc 文件'}`);
            this.disableConvertButton();
            return false;
        }

        // 检查文件大小 (10MB限制)
        const maxSize = 10 * 1024 * 1024;
        if (this.selectedFile.size > maxSize) {
            this.showError('文件大小超过10MB限制，请选择更小的文件');
            this.disableConvertButton();
            return false;
        }

        this.enableConvertButton();
        return true;
    }

    validateConversion() {
        const sourceFormat = document.getElementById('sourceFormat').value;
        const targetFormat = document.getElementById('targetFormat').value;
        
        if (sourceFormat === targetFormat) {
            this.showError('源格式和目标格式不能相同');
            this.disableConvertButton();
            return false;
        }
        
        if (this.selectedFile) {
            return this.validateFile();
        }
        
        return false;
    }

    enableConvertButton() {
        const convertBtn = document.getElementById('convertBtn');
        convertBtn.disabled = false;
        convertBtn.classList.remove('loading');
        const btnText = convertBtn.querySelector('.btn-text');
        if (btnText) {
            btnText.innerHTML = '<i class="fas fa-magic" style="margin-right: 8px;"></i>开始转换';
        }
    }

    disableConvertButton() {
        const convertBtn = document.getElementById('convertBtn');
        convertBtn.disabled = true;
        convertBtn.classList.remove('loading');
        const btnText = convertBtn.querySelector('.btn-text');
        if (btnText) {
            btnText.innerHTML = '<i class="fas fa-magic" style="margin-right: 8px;"></i>开始转换';
        }
    }

    showError(message) {
        const errorMessage = document.getElementById('errorMessage');
        errorMessage.textContent = message;
        errorMessage.classList.add('show');
    }

    hideError() {
        const errorMessage = document.getElementById('errorMessage');
        errorMessage.classList.remove('show');
    }

    showSuccessMessage(message) {
        // 创建或获取成功消息元素
        let successMessage = document.getElementById('successMessage');
        if (!successMessage) {
            successMessage = document.createElement('div');
            successMessage.id = 'successMessage';
            successMessage.className = 'success-message';
            document.getElementById('errorMessage').parentNode.insertBefore(successMessage, document.getElementById('errorMessage').nextSibling);
        }
        
        successMessage.innerHTML = `<i class="fas fa-check-circle" style="margin-right: 8px;"></i>${message}`;
        successMessage.classList.add('show');
        
        // 3秒后自动隐藏
        setTimeout(() => {
            successMessage.classList.remove('show');
        }, 3000);
    }

    showProgress(percent = 0) {
        const progressBar = document.getElementById('progressBar');
        const progressFill = document.getElementById('progressFill');
        
        progressBar.classList.add('show');
        progressFill.style.width = percent + '%';
    }

    hideProgress() {
        const progressBar = document.getElementById('progressBar');
        progressBar.classList.remove('show');
    }

    showResult(downloadUrl, filename) {
        const resultSection = document.getElementById('resultSection');
        const downloadLink = document.getElementById('downloadLink');
        
        downloadLink.href = downloadUrl;
        downloadLink.download = filename;
        resultSection.classList.add('show');
        
        // 显示成功消息
        this.showSuccessMessage(`✨ 转换完成！文件 ${filename} 已准备好下载`);
    }

    hideResult() {
        const resultSection = document.getElementById('resultSection');
        resultSection.classList.remove('show');
    }

    async convertDocument() {
        if (!this.validateConversion()) {
            return;
        }

        const convertBtn = document.getElementById('convertBtn');
        const sourceFormat = document.getElementById('sourceFormat').value;
        const targetFormat = document.getElementById('targetFormat').value;

        try {
            // 禁用按钮并显示进度
            convertBtn.disabled = true;
            convertBtn.classList.add('loading');
            this.hideError();
            this.hideResult();
            this.showProgress(10);
            this.showSuccessMessage('正在处理文件...');

            // 创建FormData
            const formData = new FormData();
            formData.append('file', this.selectedFile);
            formData.append('source_format', sourceFormat);
            formData.append('target_format', targetFormat);

            this.showProgress(30);

            // 发送转换请求
            const apiUrl = `${API_BASE_URL}/documents/convert`;
            console.log('🚀 发送文档转换请求:', {
                apiUrl,
                sourceFormat,
                targetFormat,
                fileName: this.selectedFile.name,
                fileSize: this.formatFileSize(this.selectedFile.size)
            });
            
            const response = await fetch(`${API_BASE_URL}/documents/convert`, {
                method: 'POST',
                body: formData
            });
            
            console.log('📡 API响应状态:', response.status, response.statusText);
            console.log('📋 响应头:', Object.fromEntries(response.headers.entries()));

            this.showProgress(70);

            if (!response.ok) {
                console.error('🔴 API响应错误:', response.status, response.statusText);
                
                let errorMessage = '转换失败';
                try {
                    // 尝试解析JSON错误响应
                    const errorData = await response.json();
                    errorMessage = errorData.message || '转换失败';
                    console.error('📄 错误详情:', errorData);
                } catch (jsonError) {
                    // 如果不是JSON，获取文本内容
                    console.error('⚠️  无法解析JSON错误响应，尝试获取文本:', jsonError.message);
                    try {
                        const errorText = await response.text();
                        console.error('📝 错误页面内容前200字符:', errorText.substring(0, 200));
                        
                        // 检查是否是HTML错误页面
                        if (errorText.includes('<!DOCTYPE') || errorText.includes('<html>')) {
                            errorMessage = `服务器返回了HTML错误页面（状态码：${response.status}）`;
                        } else {
                            errorMessage = `服务器错误（状态码：${response.status}）: ${errorText.substring(0, 100)}`;
                        }
                    } catch (textError) {
                        console.error('❌ 无法获取错误响应内容:', textError.message);
                        errorMessage = `服务器错误（状态码：${response.status}）`;
                    }
                }
                throw new Error(errorMessage);
            }

            // 处理下载
            const blob = await response.blob();
            const downloadUrl = window.URL.createObjectURL(blob);
            
            // 从响应头获取文件名
            const contentDisposition = response.headers.get('content-disposition');
            let filename = 'converted_document';
            if (contentDisposition) {
                const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
                if (filenameMatch && filenameMatch[1]) {
                    filename = filenameMatch[1].replace(/['"]/g, '');
                }
            } else {
                // 生成默认文件名
                const baseName = this.selectedFile.name.replace(/\.[^/.]+$/, '');
                const extension = targetFormat === 'docx' ? '.docx' : '.md';
                filename = baseName + '_converted' + extension;
            }

            this.showProgress(100);
            
            // 显示下载链接
            this.showResult(downloadUrl, filename);
            
            // 重置按钮
            convertBtn.disabled = false;
            convertBtn.classList.remove('loading');
            const btnText = convertBtn.querySelector('.btn-text');
            if (btnText) {
                btnText.innerHTML = '<i class="fas fa-magic" style="margin-right: 8px;"></i>开始转换';
            };
            
            setTimeout(() => {
                this.hideProgress();
            }, 500);

        } catch (error) {
            console.error('💥 转换过程中发生异常:', error);
            console.error('📊 异常详情:', {
                name: error.name,
                message: error.message,
                stack: error.stack
            });
            this.showError('转换失败: ' + error.message);
            
            // 重置按钮和进度
            convertBtn.disabled = false;
            convertBtn.classList.remove('loading');
            const btnText = convertBtn.querySelector('.btn-text');
            if (btnText) {
                btnText.innerHTML = '<i class="fas fa-magic" style="margin-right: 8px;"></i>开始转换';
            }
            this.hideProgress();
        }
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    new DocumentConverter();
});