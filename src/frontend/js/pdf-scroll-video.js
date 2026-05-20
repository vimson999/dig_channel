class PdfScrollVideoTool {
    constructor() {
        this.file = null;
        this.downloadUrl = null;
        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
    }

    bindElements() {
        this.form = document.getElementById('pdf-video-form');
        this.dropZone = document.getElementById('drop-zone');
        this.fileInput = document.getElementById('file-input');
        this.pickFileBtn = document.getElementById('pick-file-btn');
        this.clearFileBtn = document.getElementById('clear-file-btn');
        this.fileCard = document.getElementById('file-card');
        this.fileName = document.getElementById('file-name');
        this.fileSize = document.getElementById('file-size');
        this.pdfUrl = document.getElementById('pdf-url');
        this.presetSelect = document.getElementById('preset-select');
        this.durationInput = document.getElementById('duration-input');
        this.startHoldInput = document.getElementById('start-hold-input');
        this.endHoldInput = document.getElementById('end-hold-input');
        this.fpsSelect = document.getElementById('fps-select');
        this.crfSelect = document.getElementById('crf-select');
        this.message = document.getElementById('message');
        this.progressText = document.getElementById('progress-text');
        this.progressLabel = document.getElementById('progress-label');
        this.progressBar = document.getElementById('progress-bar');
        this.convertBtn = document.getElementById('convert-btn');
        this.emptyState = document.getElementById('empty-state');
        this.previewWrap = document.getElementById('preview-wrap');
        this.previewVideo = document.getElementById('preview-video');
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
            this.selectFile(event.dataTransfer.files[0]);
        });
    }

    selectFile(file) {
        this.hideMessage();
        if (!file) return;

        if (!file.name.toLowerCase().endsWith('.pdf')) {
            this.showMessage('请选择 PDF 文件', 'error');
            return;
        }

        const maxSize = 80 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showMessage('PDF 超过 80MB，请选择更小的文件', 'error');
            return;
        }

        this.file = file;
        this.pdfUrl.value = '';
        this.fileName.textContent = file.name;
        this.fileSize.textContent = this.formatFileSize(file.size);
        this.fileCard.classList.remove('hidden');
        this.setProgress(0, '准备生成');
    }

    clearFile() {
        this.file = null;
        this.fileInput.value = '';
        this.fileCard.classList.add('hidden');
        this.setProgress(0, '等待 PDF');
        this.hideMessage();
    }

    async convert() {
        if (!this.file && !this.pdfUrl.value.trim()) {
            this.showMessage('请上传 PDF 文件或填写 PDF 链接', 'error');
            return;
        }

        this.revokeDownloadUrl();
        this.hideMessage();
        this.setBusy(true);
        this.setProgress(10, '准备 PDF');
        this.emptyState.classList.add('hidden');
        this.previewWrap.classList.add('hidden');
        this.downloadLink.classList.add('hidden');

        const [width, height] = this.presetSelect.value.split('x');
        const formData = new FormData();
        if (this.file) {
            formData.append('file', this.file);
        } else {
            formData.append('pdf_url', this.pdfUrl.value.trim());
        }
        formData.append('width', width);
        formData.append('height', height);
        formData.append('duration', this.durationInput.value);
        formData.append('start_hold', this.startHoldInput.value);
        formData.append('end_hold', this.endHoldInput.value);
        formData.append('fps', this.fpsSelect.value);
        formData.append('crf', this.crfSelect.value);

        const progressTimer = this.startSoftProgress();

        try {
            const response = await fetch(`${API_BASE_URL}/documents/pdf-scroll-video`, {
                method: 'POST',
                body: formData,
            });

            clearInterval(progressTimer);
            this.setProgress(94, '准备下载');

            if (!response.ok) {
                let message = `生成失败，状态码 ${response.status}`;
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
            const meta = {
                pages: response.headers.get('X-Page-Count'),
                longWidth: response.headers.get('X-Long-Image-Width'),
                longHeight: response.headers.get('X-Long-Image-Height'),
                videoWidth: response.headers.get('X-Video-Width'),
                videoHeight: response.headers.get('X-Video-Height'),
                duration: response.headers.get('X-Video-Duration'),
                size: response.headers.get('X-File-Size'),
            };

            this.previewVideo.src = this.downloadUrl;
            this.downloadLink.href = this.downloadUrl;
            this.downloadLink.download = filename;
            this.downloadLink.classList.remove('hidden');
            this.previewWrap.classList.remove('hidden');
            this.resultMeta.textContent = this.buildMetaText(meta);

            this.setProgress(100, '生成完成');
            this.showMessage('MP4 已生成，可以预览或下载', 'success');
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
        let value = 16;
        return setInterval(() => {
            value = Math.min(88, value + 3);
            const text = value < 42 ? '渲染 PDF' : (value < 70 ? '拼接长图' : '编码 MP4');
            this.setProgress(value, text);
        }, 1000);
    }

    setBusy(isBusy) {
        this.convertBtn.disabled = isBusy;
        this.convertBtn.innerHTML = isBusy
            ? '<i class="fas fa-spinner fa-spin mr-2"></i>生成中'
            : '<i class="fas fa-video mr-2"></i>生成 MP4';
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
        const baseName = this.file ? this.file.name.replace(/\.[^/.]+$/, '') : 'pdf-scroll';
        return `${baseName}.scroll.mp4`;
    }

    buildMetaText(meta) {
        const parts = [];
        if (meta.pages) parts.push(`${meta.pages} 页`);
        if (meta.videoWidth && meta.videoHeight) parts.push(`${meta.videoWidth} x ${meta.videoHeight}`);
        if (meta.duration) parts.push(`${meta.duration}s`);
        if (meta.size) parts.push(this.formatFileSize(Number(meta.size)));
        return parts.length > 0 ? parts.join(' / ') : '视频生成完成';
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
    new PdfScrollVideoTool();
});
