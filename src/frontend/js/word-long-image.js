class WordLongImageTool {
    constructor() {
        this.file = null;
        this.downloadUrl = null;
        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
        this.updateQualityVisibility();
    }

    bindElements() {
        this.form = document.getElementById('word-image-form');
        this.dropZone = document.getElementById('drop-zone');
        this.fileInput = document.getElementById('file-input');
        this.pickFileBtn = document.getElementById('pick-file-btn');
        this.clearFileBtn = document.getElementById('clear-file-btn');
        this.fileCard = document.getElementById('file-card');
        this.fileName = document.getElementById('file-name');
        this.fileSize = document.getElementById('file-size');
        this.formatSelect = document.getElementById('format-select');
        this.widthSelect = document.getElementById('width-select');
        this.qualityRow = document.getElementById('quality-row');
        this.qualityRange = document.getElementById('quality-range');
        this.qualityLabel = document.getElementById('quality-label');
        this.message = document.getElementById('message');
        this.progressText = document.getElementById('progress-text');
        this.progressLabel = document.getElementById('progress-label');
        this.progressBar = document.getElementById('progress-bar');
        this.convertBtn = document.getElementById('convert-btn');
        this.emptyState = document.getElementById('empty-state');
        this.previewWrap = document.getElementById('preview-wrap');
        this.previewImage = document.getElementById('preview-image');
        this.downloadLink = document.getElementById('download-link');
        this.resultMeta = document.getElementById('result-meta');
    }

    bindEvents() {
        this.pickFileBtn.addEventListener('click', () => this.fileInput.click());
        this.fileInput.addEventListener('change', (event) => this.selectFile(event.target.files[0]));
        this.clearFileBtn.addEventListener('click', () => this.clearFile());
        this.form.addEventListener('submit', (event) => {
            event.preventDefault();
            this.convert();
        });

        this.formatSelect.addEventListener('change', () => this.updateQualityVisibility());
        this.qualityRange.addEventListener('input', () => {
            this.qualityLabel.textContent = this.qualityRange.value;
        });

        ['dragenter', 'dragover'].forEach((eventName) => {
            this.dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                this.dropZone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach((eventName) => {
            this.dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                this.dropZone.classList.remove('dragover');
            });
        });

        this.dropZone.addEventListener('drop', (event) => {
            const file = event.dataTransfer.files[0];
            this.selectFile(file);
        });
    }

    selectFile(file) {
        this.hideMessage();
        if (!file) return;

        const lowerName = file.name.toLowerCase();
        if (!lowerName.endsWith('.docx') && !lowerName.endsWith('.doc')) {
            this.showMessage('请选择 .docx 或 .doc 文件', 'error');
            return;
        }

        const maxSize = 50 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showMessage('文件超过 50MB，请选择更小的 Word 文档', 'error');
            return;
        }

        this.file = file;
        this.fileName.textContent = file.name;
        this.fileSize.textContent = this.formatFileSize(file.size);
        this.fileCard.classList.remove('hidden');
        this.convertBtn.disabled = false;
        this.setProgress(0, '准备生成');
    }

    clearFile() {
        this.file = null;
        this.fileInput.value = '';
        this.fileCard.classList.add('hidden');
        this.convertBtn.disabled = true;
        this.setProgress(0, '等待文件');
        this.hideMessage();
    }

    updateQualityVisibility() {
        const isJpg = this.formatSelect.value === 'jpg';
        this.qualityRow.classList.toggle('hidden', !isJpg);
    }

    async convert() {
        if (!this.file) {
            this.showMessage('请先选择 Word 文件', 'error');
            return;
        }

        this.revokeDownloadUrl();
        this.hideMessage();
        this.setBusy(true);
        this.setProgress(12, '上传文档');
        this.emptyState.classList.add('hidden');
        this.previewWrap.classList.add('hidden');
        this.downloadLink.classList.add('hidden');

        const formData = new FormData();
        formData.append('file', this.file);
        formData.append('format', this.formatSelect.value);
        formData.append('width', this.widthSelect.value);
        formData.append('quality', this.qualityRange.value);

        const progressTimer = this.startSoftProgress();

        try {
            const response = await fetch(`${API_BASE_URL}/documents/word-long-image`, {
                method: 'POST',
                body: formData,
            });

            clearInterval(progressTimer);
            this.setProgress(92, '准备下载');

            if (!response.ok) {
                let message = `转换失败，状态码 ${response.status}`;
                try {
                    const data = await response.json();
                    message = data.message || message;
                } catch (error) {
                    message = await response.text() || message;
                }
                throw new Error(message);
            }

            const blob = await response.blob();
            this.downloadUrl = URL.createObjectURL(blob);
            const filename = this.getFilenameFromResponse(response);
            const width = response.headers.get('X-Image-Width');
            const height = response.headers.get('X-Image-Height');
            const pages = response.headers.get('X-Page-Count');
            const size = response.headers.get('X-File-Size');

            this.previewImage.src = this.downloadUrl;
            this.downloadLink.href = this.downloadUrl;
            this.downloadLink.download = filename;
            this.downloadLink.classList.remove('hidden');
            this.previewWrap.classList.remove('hidden');
            this.resultMeta.textContent = this.buildMetaText({ width, height, pages, size });

            this.setProgress(100, '生成完成');
            this.showMessage('长图已生成，可以预览或下载', 'success');
        } catch (error) {
            clearInterval(progressTimer);
            this.emptyState.classList.remove('hidden');
            this.setProgress(0, '生成失败');
            this.showMessage(error.message, 'error');
        } finally {
            this.setBusy(false);
        }
    }

    startSoftProgress() {
        let value = 18;
        return setInterval(() => {
            value = Math.min(86, value + 4);
            this.setProgress(value, value < 50 ? '转换 PDF' : '拼接长图');
        }, 900);
    }

    setBusy(isBusy) {
        this.convertBtn.disabled = isBusy || !this.file;
        this.convertBtn.innerHTML = isBusy
            ? '<i class="fas fa-spinner fa-spin mr-2"></i>生成中'
            : '<i class="fas fa-wand-magic-sparkles mr-2"></i>生成长图';
    }

    setProgress(percent, text) {
        this.progressBar.style.width = `${percent}%`;
        this.progressLabel.textContent = `${percent}%`;
        this.progressText.textContent = text;
    }

    showMessage(text, type) {
        this.message.textContent = text;
        this.message.className = 'rounded-lg border px-4 py-3 text-sm';
        if (type === 'success') {
            this.message.classList.add('bg-emerald-50', 'border-emerald-200', 'text-emerald-700');
        } else {
            this.message.classList.add('bg-red-50', 'border-red-200', 'text-red-700');
        }
        this.message.classList.remove('hidden');
    }

    hideMessage() {
        this.message.classList.add('hidden');
    }

    getFilenameFromResponse(response) {
        const disposition = response.headers.get('content-disposition') || '';
        const utf8Match = disposition.match(/filename\\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            return decodeURIComponent(utf8Match[1]);
        }
        const filenameMatch = disposition.match(/filename="?([^"]+)"?/i);
        if (filenameMatch) {
            return filenameMatch[1];
        }
        const baseName = this.file.name.replace(/\.[^/.]+$/, '');
        return `${baseName}.long.${this.formatSelect.value}`;
    }

    buildMetaText({ width, height, pages, size }) {
        const parts = [];
        if (pages) parts.push(`${pages} 页`);
        if (width && height) parts.push(`${width} x ${height}px`);
        if (size) parts.push(this.formatFileSize(Number(size)));
        return parts.length > 0 ? parts.join(' / ') : '长图生成完成';
    }

    formatFileSize(bytes) {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }

    revokeDownloadUrl() {
        if (this.downloadUrl) {
            URL.revokeObjectURL(this.downloadUrl);
            this.downloadUrl = null;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new WordLongImageTool();
});
