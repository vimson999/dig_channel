// 文本转语音功能 JavaScript
console.log('🎤 [TTS] TTS模块已加载');

class TextToSpeechManager {
    constructor() {
        this.selectedModel = 'tacotron2';
        this.currentAudioFilename = null;
        this.models = [];
        this.isGenerating = false;
        
        this.init();
    }
    
    async init() {
        console.log('🚀 [TTS] TTS管理器启动中...');
        
        // 检查认证状态
        const isAuthenticated = await this.checkAuth();
        if (!isAuthenticated) {
            console.log('❌ [TTS] 用户未认证，重定向到登录页');
            window.location.href = 'login.html';
            return;
        }
        
        // 初始化事件监听器
        this.bindEvents();
        
        // 检查TTS服务状态
        await this.checkTTSStatus();
        
        // 加载可用模型
        await this.loadModels();
        
        console.log('✅ [TTS] TTS管理器初始化完成');
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
    
    bindEvents() {
        // 文本输入字符计数
        const textInput = document.getElementById('text-input');
        const charCounter = document.getElementById('char-counter');
        
        textInput.addEventListener('input', (e) => {
            const length = e.target.value.length;
            charCounter.textContent = `${length}/1000`;
            
            // 根据字符数设置样式
            charCounter.className = 'char-counter';
            if (length > 800) {
                charCounter.classList.add('warning');
            }
            if (length > 950) {
                charCounter.classList.add('error');
            }
            
            // 更新按钮状态
            this.updateButtonStates();
        });
        
        // 语速滑块
        const speedSlider = document.getElementById('speed-slider');
        const speedValue = document.getElementById('speed-value');
        
        speedSlider.addEventListener('input', (e) => {
            speedValue.textContent = `${e.target.value}x`;
        });
        
        // 音频播放事件
        const audioPlayer = document.getElementById('audio-player');
        const waveAnimation = document.getElementById('wave-animation');
        
        audioPlayer.addEventListener('play', () => {
            waveAnimation.classList.remove('hidden');
        });
        
        audioPlayer.addEventListener('pause', () => {
            waveAnimation.classList.add('hidden');
        });
        
        audioPlayer.addEventListener('ended', () => {
            waveAnimation.classList.add('hidden');
        });
    }
    
    async checkTTSStatus() {
        try {
            showStatusBar('检查TTS服务状态...');
            
            const response = await fetch(`${API_BASE_URL}/tts/status`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            const result = await response.json();
            const statusElement = document.getElementById('tts-status');
            
            if (result.success && result.data.tts_available) {
                statusElement.textContent = '服务正常';
                statusElement.className = 'text-xs px-2 py-1 rounded-full bg-green-100 text-green-700';
                
                if (result.data.default_model_ready) {
                    console.log('✅ [TTS] 默认模型已就绪');
                } else {
                    console.log('⚠️ [TTS] 默认模型未就绪');
                }
            } else {
                statusElement.textContent = '服务不可用';
                statusElement.className = 'text-xs px-2 py-1 rounded-full bg-red-100 text-red-700';
                showToast('TTS服务暂不可用，请联系管理员', 'error');
            }
            
            hideStatusBar();
            
        } catch (error) {
            console.error('检查TTS状态失败:', error);
            const statusElement = document.getElementById('tts-status');
            statusElement.textContent = '检查失败';
            statusElement.className = 'text-xs px-2 py-1 rounded-full bg-red-100 text-red-700';
            hideStatusBar();
        }
    }
    
    async loadModels() {
        try {
            showStatusBar('加载语音模型...');
            
            const response = await fetch(`${API_BASE_URL}/tts/models`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                }
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.models = result.data.models;
                this.selectedModel = result.data.default_model;
                this.renderModelSelection();
                console.log('✅ [TTS] 模型加载成功:', this.models.length, '个模型');
            } else {
                throw new Error(result.message || '加载模型失败');
            }
            
            hideStatusBar();
            
        } catch (error) {
            console.error('加载模型失败:', error);
            showToast('加载语音模型失败: ' + error.message, 'error');
            hideStatusBar();
        }
    }
    
    renderModelSelection() {
        const container = document.getElementById('model-selection');
        
        if (this.models.length === 0) {
            container.innerHTML = `
                <div class="col-span-full text-center py-8 text-gray-500">
                    <i class="fas fa-robot text-2xl mb-2 opacity-50"></i>
                    <div>暂无可用模型</div>
                </div>
            `;
            return;
        }
        
        const modelsHtml = this.models.map(model => {
            const isSelected = model.key === this.selectedModel;
            const selectedClass = isSelected ? 'selected' : '';
            
            // 根据语言设置图标和颜色
            let iconClass = 'fas fa-robot';
            let colorClass = 'text-blue-600';
            
            if (model.language === 'zh-CN') {
                iconClass = 'fas fa-language';
                colorClass = 'text-red-600';
            } else if (model.language === 'en') {
                iconClass = 'fas fa-microphone';
                colorClass = 'text-green-600';
            }
            
            return `
                <div class="model-card bg-white rounded-lg p-4 border ${selectedClass}" 
                     onclick="ttsManager.selectModel('${model.key}')">
                    <div class="flex items-start gap-3">
                        <div class="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center flex-shrink-0">
                            <i class="${iconClass} ${colorClass}"></i>
                        </div>
                        <div class="flex-1 min-w-0">
                            <h4 class="font-semibold text-gray-800 text-sm">${model.name}</h4>
                            <p class="text-xs text-gray-500 mt-1 line-clamp-2">${model.description}</p>
                            <div class="flex items-center gap-2 mt-2">
                                <span class="px-2 py-1 text-xs font-medium rounded-full ${
                                    model.language === 'zh-CN' 
                                        ? 'bg-red-100 text-red-700' 
                                        : 'bg-blue-100 text-blue-700'
                                }">
                                    ${model.language === 'zh-CN' ? '中文' : 'English'}
                                </span>
                                ${isSelected ? '<i class="fas fa-check text-primary-600 text-sm"></i>' : ''}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = modelsHtml;
    }
    
    selectModel(modelKey) {
        this.selectedModel = modelKey;
        this.renderModelSelection();
        console.log('🎯 [TTS] 选择模型:', modelKey);
        
        // 更新按钮状态
        this.updateButtonStates();
    }
    
    updateButtonStates() {
        const textInput = document.getElementById('text-input');
        const generateBtn = document.getElementById('generate-btn');
        const previewBtn = document.getElementById('preview-btn');
        
        const hasText = textInput.value.trim().length > 0;
        const isValidLength = textInput.value.length <= 1000;
        const canGenerate = hasText && isValidLength && !this.isGenerating;
        
        // 更新生成按钮状态
        generateBtn.disabled = !canGenerate;
        if (!canGenerate) {
            generateBtn.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            generateBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
        
        // 更新预览按钮状态
        previewBtn.disabled = !hasText;
        if (!hasText) {
            previewBtn.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            previewBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    }
    
    async generateSpeech() {
        const textInput = document.getElementById('text-input');
        const text = textInput.value.trim();
        
        if (!text) {
            showToast('请输入要转换的文本', 'warning');
            textInput.focus();
            return;
        }
        
        if (text.length > 1000) {
            showToast('文本长度不能超过1000字符', 'error');
            return;
        }
        
        if (this.isGenerating) {
            showToast('正在生成中，请稍候...', 'info');
            return;
        }
        
        try {
            this.isGenerating = true;
            this.updateButtonStates();
            
            // 显示进度区域
            this.showProgress('准备中...', 0);
            
            const speedSlider = document.getElementById('speed-slider');
            const speed = parseFloat(speedSlider.value);
            
            console.log(`🎤 [TTS] 开始生成语音: 模型=${this.selectedModel}, 长度=${text.length}, 语速=${speed}`);
            
            // 模拟进度更新
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 10;
                if (progress < 90) {
                    this.updateProgress(`正在生成语音... (${progress}%)`, progress);
                }
            }, 200);
            
            const response = await fetch(`${API_BASE_URL}/tts/synthesize`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify({
                    text: text,
                    model: this.selectedModel,
                    speed: speed
                })
            });
            
            clearInterval(progressInterval);
            
            const result = await response.json();
            
            if (result.success) {
                this.updateProgress('生成完成!', 100);
                
                setTimeout(() => {
                    this.hideProgress();
                    this.showAudioResult(result.data);
                }, 500);
                
                showToast('语音生成成功!', 'success');
                console.log('✅ [TTS] 语音生成成功:', result.data.audio_filename);
                
            } else {
                throw new Error(result.message || '生成失败');
            }
            
        } catch (error) {
            console.error('生成语音失败:', error);
            this.hideProgress();
            showToast('生成语音失败: ' + error.message, 'error');
            
        } finally {
            this.isGenerating = false;
            this.updateButtonStates();
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
    
    showAudioResult(data) {
        this.currentAudioFilename = data.audio_filename;
        
        const resultSection = document.getElementById('result-section');
        const audioPlayer = document.getElementById('audio-player');
        const audioInfo = document.getElementById('audio-info');
        const generationTime = document.getElementById('generation-time');
        
        // 设置音频源
        const audioUrl = `${API_BASE_URL}/tts/download/${data.audio_filename}`;
        audioPlayer.src = audioUrl;
        
        // 显示文件信息
        const fileSizeMB = (data.file_size / 1024 / 1024).toFixed(2);
        const durationText = data.duration_estimate ? `约${data.duration_estimate.toFixed(1)}秒` : '未知';
        audioInfo.textContent = `文件大小: ${fileSizeMB}MB | 预计时长: ${durationText} | 模型: ${data.model_name}`;
        
        // 显示生成时间
        generationTime.textContent = `刚刚生成`;
        
        // 显示结果区域
        resultSection.classList.remove('hidden');
        
        // 滚动到结果区域
        resultSection.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'nearest' 
        });
    }
    
    async downloadAudio() {
        if (!this.currentAudioFilename) {
            showToast('没有可下载的音频文件', 'warning');
            return;
        }
        
        try {
            showStatusBar('准备下载...');
            
            const downloadUrl = `${API_BASE_URL}/tts/download/${this.currentAudioFilename}`;
            
            // 创建隐藏的下载链接
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = this.currentAudioFilename;
            link.target = '_blank';
            
            // 添加认证头（通过URL参数，因为a标签无法设置headers）
            const token = localStorage.getItem('access_token');
            if (token) {
                link.href += `?token=${token}`;
            }
            
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            showToast('开始下载音频文件', 'success');
            console.log('📥 [TTS] 开始下载:', this.currentAudioFilename);
            
            hideStatusBar();
            
        } catch (error) {
            console.error('下载失败:', error);
            showToast('下载失败: ' + error.message, 'error');
            hideStatusBar();
        }
    }
    
    async previewText() {
        const textInput = document.getElementById('text-input');
        const text = textInput.value.trim();
        
        if (!text) {
            showToast('请输入要预览的文本', 'warning');
            return;
        }
        
        // 显示文本预览模态框
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4';
        modal.innerHTML = `
            <div class="bg-white rounded-2xl shadow-strong max-w-2xl w-full max-h-[80vh] overflow-y-auto">
                <div class="p-6 border-b border-gray-100">
                    <div class="flex items-center justify-between">
                        <h3 class="text-xl font-bold text-gray-800">文本预览</h3>
                        <button onclick="this.parentElement.parentElement.parentElement.remove()" 
                                class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
                            <i class="fas fa-times text-lg"></i>
                        </button>
                    </div>
                </div>
                <div class="p-6">
                    <div class="mb-4">
                        <div class="flex items-center justify-between text-sm text-gray-600 mb-2">
                            <span>字符统计</span>
                            <span>${text.length}/1000 字符</span>
                        </div>
                        <div class="w-full bg-gray-200 rounded-full h-2">
                            <div class="bg-primary-600 h-2 rounded-full" style="width: ${(text.length/1000)*100}%"></div>
                        </div>
                    </div>
                    <div class="bg-gray-50 rounded-lg p-4">
                        <div class="text-gray-800 leading-relaxed whitespace-pre-wrap">${text}</div>
                    </div>
                    <div class="mt-4 text-sm text-gray-500">
                        <i class="fas fa-info-circle mr-1"></i>
                        预计语音时长：约 ${(text.length * 0.1).toFixed(1)} 秒
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
    }
    
    shareAudio() {
        if (!this.currentAudioFilename) {
            showToast('没有可分享的音频文件', 'warning');
            return;
        }
        
        // 复制下载链接到剪贴板
        const shareUrl = `${window.location.origin}${API_BASE_URL}/tts/download/${this.currentAudioFilename}`;
        
        navigator.clipboard.writeText(shareUrl).then(() => {
            showToast('音频链接已复制到剪贴板', 'success');
        }).catch(() => {
            showToast('复制链接失败', 'error');
        });
    }
    
    generateAgain() {
        // 隐藏结果区域
        const resultSection = document.getElementById('result-section');
        resultSection.classList.add('hidden');
        
        // 重置状态
        this.currentAudioFilename = null;
        
        // 聚焦到文本输入框
        const textInput = document.getElementById('text-input');
        textInput.focus();
        textInput.select();
    }
}

// 全局函数
function clearText() {
    const textInput = document.getElementById('text-input');
    textInput.value = '';
    textInput.dispatchEvent(new Event('input'));
    textInput.focus();
}

function checkTTSStatus() {
    if (window.ttsManager) {
        window.ttsManager.checkTTSStatus();
    }
}

function previewText() {
    if (window.ttsManager) {
        window.ttsManager.previewText();
    }
}

function generateSpeech() {
    if (window.ttsManager) {
        window.ttsManager.generateSpeech();
    }
}

function downloadAudio() {
    if (window.ttsManager) {
        window.ttsManager.downloadAudio();
    }
}

function shareAudio() {
    if (window.ttsManager) {
        window.ttsManager.shareAudio();
    }
}

function generateAgain() {
    if (window.ttsManager) {
        window.ttsManager.generateAgain();
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
    window.ttsManager = new TextToSpeechManager();
    console.log('🎉 [TTS] TTS页面已启动');
});

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TextToSpeechManager };
}