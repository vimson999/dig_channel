// 文本转语音功能 JavaScript
console.log('🎤 [TTS] TTS模块已加载');

class TextToSpeechManager {
    constructor() {
        this.selectedModel = 'auto';  // 改为智能选择
        this.currentAudioFilename = null;
        this.models = [];
        this.speakers = [];
        this.selectedSpeaker = null;
        this.selectedEmotion = '中性';
        this.referenceAudioPath = null;
        this.isDialogueMode = false;
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
        
        
        // 模型选择器变化
        const modelSelector = document.getElementById('model-selector');
        modelSelector.addEventListener('change', (e) => {
            this.selectModel(e.target.value);
        });
        
        // 说话人选择器变化
        const speakerSelector = document.getElementById('speaker-selector');
        speakerSelector.addEventListener('change', (e) => {
            this.selectedSpeaker = e.target.value;
        });
        
        // 情感选择器变化
        const emotionSelector = document.getElementById('emotion-selector');
        emotionSelector.addEventListener('change', (e) => {
            this.selectedEmotion = e.target.value;
        });
        
        // 多人对话模式切换
        const dialogueMode = document.getElementById('dialogue-mode');
        dialogueMode.addEventListener('change', (e) => {
            this.isDialogueMode = e.target.checked;
            this.toggleDialogueMode();
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
        
        // 添加播放速度控制
        this.setupPlaybackSpeedControls();
    }
    
    setupPlaybackSpeedControls() {
        // 在播放器旁边添加倍速控制
        const audioPlayer = document.getElementById('audio-player');
        if (!audioPlayer) return;
        
        // 创建速度控制容器
        const speedControlsId = 'playback-speed-controls';
        let speedControls = document.getElementById(speedControlsId);
        
        if (!speedControls) {
            speedControls = document.createElement('div');
            speedControls.id = speedControlsId;
            speedControls.className = 'flex items-center gap-2 mt-2';
            speedControls.innerHTML = `
                <span class="text-sm text-gray-600">播放速度:</span>
                <div class="flex gap-1">
                    <button class="speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors" data-speed="0.5">0.5x</button>
                    <button class="speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors" data-speed="0.75">0.75x</button>
                    <button class="speed-btn px-2 py-1 text-xs bg-primary-600 text-white rounded" data-speed="1">1x</button>
                    <button class="speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors" data-speed="1.25">1.25x</button>
                    <button class="speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors" data-speed="1.5">1.5x</button>
                    <button class="speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors" data-speed="2">2x</button>
                </div>
            `;
            
            // 插入到音频播放器后面
            audioPlayer.parentNode.insertBefore(speedControls, audioPlayer.nextSibling);
        }
        
        // 添加点击事件监听器
        speedControls.addEventListener('click', (e) => {
            if (e.target.classList.contains('speed-btn')) {
                const speed = parseFloat(e.target.dataset.speed);
                this.setPlaybackSpeed(speed);
                
                // 更新按钮状态
                speedControls.querySelectorAll('.speed-btn').forEach(btn => {
                    btn.className = 'speed-btn px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors';
                });
                e.target.className = 'speed-btn px-2 py-1 text-xs bg-primary-600 text-white rounded';
            }
        });
    }
    
    setPlaybackSpeed(speed) {
        const audioPlayer = document.getElementById('audio-player');
        if (audioPlayer) {
            audioPlayer.playbackRate = speed;
            console.log(`🎵 [TTS] 设置播放速度: ${speed}x`);
        }
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
        const modelSelector = document.getElementById('model-selector');
        const modelStatus = document.getElementById('model-status');
        
        if (this.models.length === 0) {
            modelSelector.innerHTML = '<option value="">暂无可用模型</option>';
            modelStatus.className = 'w-2 h-2 rounded-full bg-red-400';
            return;
        }
        
        // 清空并添加模型选项
        modelSelector.innerHTML = '';
        
        // 定义分类
        const groups = {
            'multi_speaker': { label: '🎭 多人会话模型', options: [] },
            'chinese': { label: '🇨🇳 中文模型', options: [] },
            'english': { label: '🇺🇸 英文模型', options: [] },
            'other': { label: '🌐 其他模型', options: [] }
        };

        // 对模型进行分类
        this.models.forEach(model => {
            let groupKey = 'other';
            
            // 优先判断多人会话
            if (model.key === 'chattts_v1' || model.key === 'dialogue_mode') {
                groupKey = 'multi_speaker';
            } else if (model.language === 'zh-CN' || model.key === 'chinese') {
                groupKey = 'chinese';
            } else if (model.language === 'en' || model.key === 'tacotron2') {
                groupKey = 'english';
            }
            
            groups[groupKey].options.push(model);
        });

        // 渲染分组
        // 顺序：多人 -> 中文 -> 英文 -> 其他
        const renderOrder = ['multi_speaker', 'chinese', 'english', 'other'];
        
        renderOrder.forEach(key => {
            const group = groups[key];
            if (group.options.length > 0) {
                const optgroup = document.createElement('optgroup');
                optgroup.label = group.label;
                
                group.options.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model.key;
                    
                    // 格式化显示名称: [语言] [模式] 名称
                    let langTag = '[未知]';
                    let modeTag = '[单人]';
                    
                    // 确定语言标签
                    if (model.language === 'zh-CN' || model.key === 'chinese') langTag = '[中文]';
                    else if (model.language === 'en' || model.key === 'tacotron2') langTag = '[英文]';
                    else if (model.language === 'multilingual' || model.key === 'your_tts') langTag = '[多语]';
                    
                    // 确定模式标签
                    if (model.key === 'chattts_v1' || model.key === 'dialogue_mode') {
                        modeTag = '[多人]';
                        // ChatTTS 主要支持中文和英文，但在本场景下主要作为中文多人模型使用
                        if (model.key === 'chattts_v1') langTag = '[中/英]'; 
                    }
                    
                    option.textContent = `${langTag} ${modeTag} ${model.name}`;
                    option.selected = model.key === this.selectedModel;
                    optgroup.appendChild(option);
                });
                
                modelSelector.appendChild(optgroup);
            }
        });
        
        // 更新状态指示器
        modelStatus.className = 'w-2 h-2 rounded-full bg-green-400';
        modelStatus.nextElementSibling.textContent = '模型已加载';
        
        // 初始化高级选项
        this.updateAdvancedOptions();
    }
    
    selectModel(modelKey) {
        this.selectedModel = modelKey;
        console.log('🎯 [TTS] 选择模型:', modelKey);
        
        // 更新高级选项
        this.updateAdvancedOptions();
        
        // 自动填充示例测试文本
        this.fillExampleText(modelKey);
        
        // 更新按钮状态
        this.updateButtonStates();
    }
    
    fillExampleText(modelKey) {
        const textInput = document.getElementById('text-input');
        // 仅当文本框为空时才填充
        if (textInput.value.trim().length > 0) return;
        
        let exampleText = "";
        
        if (modelKey === 'chattts_v1') {
            exampleText = "[主持人] 大家好，欢迎来到今天的科技访谈节目。今天我们请到了一位特别嘉宾。\n[嘉宾] 谢谢主持人，很高兴能和大家分享我对人工智能的一些看法。\n[主持人] 听说最近在语音合成领域有了新的突破？\n[嘉宾] 是的，现在的技术已经能让机器说话非常自然了，就像真人一样。";
        } else if (modelKey === 'dialogue_mode') {
            exampleText = "[小明] 你好！今天天气真不错。\n[小红] 是啊，很久没有见到这么好的太阳了。\n[小明] 我们去公园走走吧？\n[小红] 好主意，走吧！";
        } else if (['chinese'].includes(modelKey) || modelKey.includes('zh')) {
            exampleText = "这是一个中文语音合成测试。通过深度学习技术，我们能够生成自然流畅的中文语音。您可以尝试输入更长的文本来测试效果。";
        } else if (modelKey === 'tacotron2' || modelKey.includes('en')) {
            exampleText = "Hello! This is a test for English speech synthesis. The system can generate natural-sounding speech from text. Feel free to try it out with your own words.";
        } else if (modelKey === 'your_tts') {
            exampleText = "这是一个多语言测试。\nThis is a multilingual test.\n您可以混合输入中英文。";
        }
        
        if (exampleText) {
            textInput.value = exampleText;
            // 触发 input 事件以更新计数器
            textInput.dispatchEvent(new Event('input'));
            
            // 如果是对话模式，自动勾选对话选项（如果需要）
            if (modelKey === 'chattts_v1' || modelKey === 'dialogue_mode') {
                const dialogueModeCheckbox = document.getElementById('dialogue-mode');
                if (dialogueModeCheckbox && !dialogueModeCheckbox.checked) {
                    dialogueModeCheckbox.checked = true;
                    // 手动触发 change 事件
                    dialogueModeCheckbox.dispatchEvent(new Event('change'));
                }
            }
        }
    }
    
    async updateAdvancedOptions() {
        const selectedModel = this.models.find(m => m.key === this.selectedModel);
        if (!selectedModel) return;
        
        const speakerSection = document.getElementById('speaker-section');
        const emotionSection = document.getElementById('emotion-section');
        const cloneSection = document.getElementById('clone-section');
        const dialogueSection = document.getElementById('dialogue-section');
        
        // 隐藏所有高级选项
        speakerSection.classList.add('hidden');
        emotionSection.classList.add('hidden');
        cloneSection.classList.add('hidden');
        dialogueSection.classList.add('hidden');
        
        // 根据模型类型显示相应选项
        if (selectedModel.type === 'multi_speaker') {
            // 显示说话人选择和对话模式
            speakerSection.classList.remove('hidden');
            if (selectedModel.supports_dialogue) {
                dialogueSection.classList.remove('hidden');
            }
            await this.loadSpeakers(this.selectedModel);
        } else if (selectedModel.type === 'voice_cloning') {
            // 显示声音克隆上传和对话模式
            cloneSection.classList.remove('hidden');
            if (selectedModel.supports_dialogue) {
                dialogueSection.classList.remove('hidden');
            }
        } else {
            // 单说话人模型
            if (selectedModel.supports_emotions) {
                // 显示情感选择
                emotionSection.classList.remove('hidden');
                this.renderEmotions(selectedModel.emotion_options);
            }
        }
    }
    
    renderEmotions(emotions) {
        const emotionSelector = document.getElementById('emotion-selector');
        
        // 清空选项
        emotionSelector.innerHTML = '';
        
        if (!emotions || emotions.length === 0) {
            emotions = ['中性', '高兴', '悲伤', '愤怒', '惊讶'];
        }
        
        emotions.forEach(emotion => {
            const option = document.createElement('option');
            option.value = emotion;
            option.textContent = emotion;
            if (emotion === this.selectedEmotion) {
                option.selected = true;
            }
            emotionSelector.appendChild(option);
        });
    }
    
    toggleDialogueMode() {
        const normalTip = document.getElementById('normal-tip');
        const dialogueTip = document.getElementById('dialogue-tip');
        const dialogueHelp = document.getElementById('dialogue-help');
        
        if (this.isDialogueMode) {
            normalTip.classList.add('hidden');
            dialogueTip.classList.remove('hidden');
            dialogueHelp.classList.remove('hidden');
        } else {
            normalTip.classList.remove('hidden');
            dialogueTip.classList.add('hidden');
            dialogueHelp.classList.add('hidden');
        }
    }
    
    async loadSpeakers(modelKey) {
        try {
            const response = await fetch(`${API_BASE_URL}/tts/speakers/${modelKey}`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                }
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.speakers = result.data.speakers || [];
                this.renderSpeakers();
                console.log('✅ [TTS] 加载说话人列表:', this.speakers.length, '个说话人');
            } else {
                console.warn('⚠️ [TTS] 加载说话人失败:', result.message);
                this.speakers = [];
                this.renderSpeakers();
            }
        } catch (error) {
            console.error('加载说话人列表失败:', error);
            this.speakers = [];
            this.renderSpeakers();
        }
    }
    
    renderSpeakers() {
        const speakerSelector = document.getElementById('speaker-selector');
        
        // 清空选项
        speakerSelector.innerHTML = '<option value="">选择说话人...</option>';
        
        if (this.speakers.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '暂无可用说话人';
            option.disabled = true;
            speakerSelector.appendChild(option);
            return;
        }
        
        // 添加说话人选项
        this.speakers.forEach((speaker, index) => {
            const option = document.createElement('option');
            option.value = speaker;
            option.textContent = `说话人 ${index + 1}: ${speaker}`;
            speakerSelector.appendChild(option);
        });
        
        // 默认选择第一个说话人
        if (this.speakers.length > 0) {
            this.selectedSpeaker = this.speakers[0];
            speakerSelector.value = this.selectedSpeaker;
        }
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
            
            console.log(`🎤 [TTS] 开始生成语音: 模型=${this.selectedModel}, 长度=${text.length}`);
            
            // 模拟进度更新
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 10;
                if (progress < 90) {
                    this.updateProgress(`正在生成语音... (${progress}%)`, progress);
                }
            }, 200);
            
            // 前端文本预处理 - 额外保护措施
            let processedText = text.trim();
            // 移除可能导致问题的特殊字符
            processedText = processedText.replace(/[\u200b-\u200d\ufeff]/g, '');
            // 规范化空白字符  
            processedText = processedText.replace(/\s+/g, ' ');
            
            console.log('🔍 [前端] 原始文本:', text);
            console.log('🔍 [前端] 处理后文本:', processedText);

        // 准备请求数据
            const requestData = {
                text: processedText,
                model: this.selectedModel,
                is_dialogue: this.isDialogueMode
            };
            
            // 添加说话人参数（多说话人模型）
            if (this.selectedSpeaker) {
                requestData.speaker = this.selectedSpeaker;
            }
            
            // 添加情感参数（支持情感的模型）
            if (this.selectedEmotion) {
                requestData.emotion = this.selectedEmotion;
            }
            
            // 添加参考音频参数（声音克隆模型）
            if (this.referenceAudioPath) {
                requestData.reference_audio_path = this.referenceAudioPath;
            }
            
            const response = await fetch(`${API_BASE_URL}/tts/synthesize`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify(requestData)
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
                if (result.data.dialogue_segments) {
                    console.log('✅ [TTS] 多人对话生成成功:', result.data.audio_filename, `(${result.data.dialogue_segments}段对话)`);
                } else {
                    console.log('✅ [TTS] 语音生成成功:', result.data.audio_filename);
                }
                
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
        
        // 设置音频源（使用流式播放端点）
        const streamUrl = `${API_BASE_URL}/tts/stream/${data.audio_filename}`;
        audioPlayer.src = streamUrl;
        
        // 添加错误处理
        audioPlayer.addEventListener('error', (e) => {
            console.error('音频播放错误:', e);
            console.error('音频URL:', streamUrl);
            console.error('错误详情:', e.target.error);
            
            // 尝试使用下载端点作为备用
            const fallbackUrl = `${API_BASE_URL}/tts/download/${data.audio_filename}`;
            console.log('🔄 [TTS] 尝试备用播放URL:', fallbackUrl);
            
            if (audioPlayer.src !== fallbackUrl) {
                audioPlayer.src = fallbackUrl;
                showToast('正在尝试备用播放方式...', 'info');
            } else {
                showToast('音频播放失败，请检查文件是否存在', 'error');
            }
        }, { once: true });
        
        // 添加加载成功事件
        audioPlayer.addEventListener('loadeddata', () => {
            console.log('✅ [TTS] 音频加载成功:', streamUrl);
        }, { once: true });
        
        // 添加网络状态监听
        audioPlayer.addEventListener('loadstart', () => {
            console.log('🔄 [TTS] 开始加载音频:', streamUrl);
        }, { once: true });
        
        // 显示文件信息
        const fileSizeMB = (data.file_size / 1024 / 1024).toFixed(2);
        const durationText = data.duration_estimate ? `约${data.duration_estimate.toFixed(1)}秒` : '未知';
        let infoText = `文件大小: ${fileSizeMB}MB | 预计时长: ${durationText} | 模型: ${data.model_name}`;
        
        
        // 只在智能选择模式时显示语言检测信息
        if (data.auto_selected && data.detected_language) {
            const langText = data.detected_language === 'zh-CN' ? '中文' : '英文';
            infoText += ` | 智能检测: ${langText}`;
        }
        
        if (data.dialogue_segments) {
            infoText += ` | 对话段数: ${data.dialogue_segments}`;
        }
        
        if (data.speaker_mapping) {
            const speakerCount = Object.keys(data.speaker_mapping).length;
            infoText += ` | 说话人: ${speakerCount}人`;
        }
        
        audioInfo.textContent = infoText;
        
        // 显示生成时间
        generationTime.textContent = `刚刚生成`;
        
        // 显示结果区域
        resultSection.classList.remove('hidden');
        
        // 确保播放速度控制已初始化
        setTimeout(() => {
            this.setupPlaybackSpeedControls();
        }, 100);
        
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
            
            // 直接创建下载链接，不需要认证（后端已移除认证要求）
            const downloadUrl = `${API_BASE_URL}/tts/download/${this.currentAudioFilename}`;
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = this.currentAudioFilename;
            link.target = '_blank'; // 在新窗口打开，避免页面跳转
            
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
        
        // 计算准确的时长预估
        const chineseChars = (text.match(/[\u4e00-\u9fff]/g) || []).length;
        const otherChars = text.length - chineseChars;
        const estimatedDuration = chineseChars * 0.25 + otherChars * 0.05;
        
        modal.innerHTML = `
            <div class="bg-white rounded-2xl shadow-strong max-w-2xl w-full max-h-[80vh] overflow-y-auto">
                <div class="p-6 border-b border-gray-100">
                    <div class="flex items-center justify-between">
                        <h3 class="text-xl font-bold text-gray-800">文本预览</h3>
                        <button id="modal-close-btn" class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
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
                        预计语音时长：约 ${Math.max(estimatedDuration, 0.5).toFixed(1)} 秒
                        ${chineseChars > 0 ? ` | 中文: ${chineseChars}字` : ''}
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        
        // 添加关闭事件监听器
        const closeBtn = modal.querySelector('#modal-close-btn');
        const closeModal = () => {
            modal.remove();
        };
        
        closeBtn.addEventListener('click', closeModal);
        
        // 点击背景关闭
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                closeModal();
            }
        });
        
        // ESC键关闭
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') {
                closeModal();
                document.removeEventListener('keydown', handleKeyDown);
            }
        };
        document.addEventListener('keydown', handleKeyDown);
    }
    
    shareAudio() {
        if (!this.currentAudioFilename) {
            showToast('没有可分享的音频文件', 'warning');
            return;
        }
        
        // 复制下载链接到剪贴板
        const shareUrl = `${window.location.origin}${API_BASE_URL}/tts/download/${this.currentAudioFilename}`;
        
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(shareUrl).then(() => {
                showToast('音频链接已复制到剪贴板', 'success');
            }).catch(() => {
                // 备用方案
                this.fallbackCopyToClipboard(shareUrl);
            });
        } else {
            // 备用方案
            this.fallbackCopyToClipboard(shareUrl);
        }
    }
    
    fallbackCopyToClipboard(text) {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        try {
            document.execCommand('copy');
            showToast('音频链接已复制到剪贴板', 'success');
        } catch (err) {
            showToast('复制链接失败，请手动复制: ' + text, 'error');
        } finally {
            document.body.removeChild(textArea);
        }
    }
    
    async uploadReferenceAudio() {
        const fileInput = document.getElementById('reference-audio');
        const uploadBtn = document.getElementById('upload-btn');
        const uploadStatus = document.getElementById('upload-status');
        
        if (!fileInput.files[0]) {
            showToast('请选择音频文件', 'warning');
            return;
        }
        
        const file = fileInput.files[0];
        const allowedTypes = ['audio/wav', 'audio/mpeg', 'audio/flac', 'audio/m4a'];
        
        if (!allowedTypes.includes(file.type)) {
            showToast('不支持的文件格式，请使用 WAV, MP3, FLAC 或 M4A 格式', 'error');
            return;
        }
        
        try {
            uploadBtn.disabled = true;
            uploadBtn.textContent = '上传中...';
            uploadStatus.textContent = '正在上传参考音频...';
            uploadStatus.className = 'text-xs text-blue-500 mt-1';
            
            const formData = new FormData();
            formData.append('audio', file);
            
            const response = await fetch(`${API_BASE_URL}/tts/upload-reference`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: formData
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.referenceAudioPath = result.data.reference_path;
                uploadStatus.textContent = `上传成功: ${result.data.original_filename} (${(result.data.file_size / 1024).toFixed(1)}KB)`;
                uploadStatus.className = 'text-xs text-green-600 mt-1';
                showToast('参考音频上传成功', 'success');
                console.log('✅ [TTS] 参考音频上传成功:', result.data.reference_filename);
            } else {
                throw new Error(result.message || '上传失败');
            }
            
        } catch (error) {
            console.error('上传参考音频失败:', error);
            uploadStatus.textContent = '上传失败: ' + error.message;
            uploadStatus.className = 'text-xs text-red-600 mt-1';
            showToast('上传失败: ' + error.message, 'error');
        } finally {
            uploadBtn.disabled = false;
            uploadBtn.textContent = '上传';
        }
    }
    
    generateAgain() {
        // 隐藏结果区域
        const resultSection = document.getElementById('result-section');
        resultSection.classList.add('hidden');
        
        // 重置状态
        this.currentAudioFilename = null;
        this.isDialogueMode = false;
        const dialogueMode = document.getElementById('dialogue-mode');
        if (dialogueMode) {
            dialogueMode.checked = false;
            this.toggleDialogueMode();
        }
        
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

function uploadReferenceAudio() {
    if (window.ttsManager) {
        window.ttsManager.uploadReferenceAudio();
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