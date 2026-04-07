/**
 * Publisher Controller - 页面入口协调器
 */

let currentTaskId = null;
let pollInterval = null;
let historyInterval = null;
let startTime = null;

const state = {
    images: { 
        bg: (() => { const img = new Image(); img.crossOrigin = "anonymous"; return img; })(),      
        custom: (() => { const img = new Image(); img.crossOrigin = "anonymous"; return img; })(),  
        videoBody: (() => { const img = new Image(); img.crossOrigin = "anonymous"; return img; })(),
        v_screenshot: (() => { const img = new Image(); img.crossOrigin = "anonymous"; return img; })() 
    },
    transforms: {
        cover: { x: 0, y: 0, scale: 1 },
        video: { x: 0, y: 0, scale: 1 }
    },
    audio_segments: [], // 支持多段音频
    bgData: []
};


// --- DOM 元素引用 ---
const elements = {
    coverCanvas: document.getElementById('previewCanvas'),
    videoCanvas: document.getElementById('videoPreviewCanvas'),
    verticalCanvas: document.getElementById('verticalCanvas'),
    publishForm: document.getElementById('publishForm'),
    submitBtn: document.getElementById('submitBtn'),
    historyList: document.getElementById('historyList'),
    statusCard: document.getElementById('statusCard'),
    stepDisplay: document.getElementById('stepDisplay'),
    progressBar: document.getElementById('progressBar'),
    timerText: document.getElementById('timerText')
};

const coverCtx = elements.coverCanvas ? elements.coverCanvas.getContext('2d') : null;
const videoCtx = elements.videoCanvas ? elements.videoCanvas.getContext('2d') : null;
const verticalCtx = elements.verticalCanvas ? elements.verticalCanvas.getContext('2d') : null;

// --- 初始化入口 ---

document.addEventListener('DOMContentLoaded', async () => {
    initUI();
    await loadInitialData();
    bindEvents();
    initCanvasInteractions();
});

function initUI() {
    // 动态设置当前日期落款
    const dateInput = document.getElementById('thumb_date');
    const vDateInput = document.getElementById('v_thumb_date');
    const now = new Date();
    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const defaultDate = `${monthNames[now.getMonth()]} ${now.getFullYear()} | 胶囊播客`;
    
    if (dateInput) dateInput.value = defaultDate;
    if (vDateInput) vDateInput.value = defaultDate;

    const select = document.getElementById('bili_tid');
    if (select) {
        const BILI_TIDS = [
            { label: "知识/科技", items: [{ value: "207", label: "财经" }, { value: "36", label: "知识" }] },
            { label: "热门", items: [{ value: "171", label: "电子竞技" }] }
        ];
        BILI_TIDS.forEach(group => {
            const optgroup = document.createElement('optgroup'); optgroup.label = group.label;
            group.items.forEach(item => {
                const opt = document.createElement('option'); opt.value = item.value; opt.textContent = `${item.label} (${item.value})`; optgroup.appendChild(opt);
            });
            select.appendChild(optgroup);
        });
        select.value = "207";
    }
}

async function loadInitialData() {
    try {
        const status = await PublisherService.checkBiliStatus();
        const el = document.getElementById('bili-status-badge');
        if (el) {
            if (status.success && status.data.valid) {
                const user = status.data.data;
                el.className = "flex items-center gap-2 text-xs bg-emerald-50 px-3 py-1 rounded-full text-emerald-600 border border-emerald-100";
                el.innerHTML = `<img src="${user.face}" class="w-4 h-4 rounded-full"> <span class="font-bold">${user.name}</span>`;
                loadSeasons();
            } else {
                el.className = "flex items-center gap-2 text-xs bg-red-50 px-3 py-1 rounded-full text-red-600 border border-red-100 cursor-pointer";
                el.innerHTML = `<i class="fa-solid fa-circle-xmark"></i> <span>未连接 (配置)</span>`;
                el.onclick = () => document.getElementById('bili-modal')?.classList.remove('hidden');
            }
        }

        const presets = await PublisherService.fetchPresets();
        state.bgData = presets.backgrounds;
        const orgS = document.getElementById('select-org');
        if (orgS) {
            orgS.innerHTML = '';
            presets.organizations.forEach(o => { const opt = document.createElement('option'); opt.value = o.name; opt.textContent = o.name; orgS.appendChild(opt); });
            orgS.value = "Goldman Sachs · 高盛研报";
        }
        const bgS = document.getElementById('select-bg');
        if (bgS) {
            bgS.innerHTML = '';
            presets.backgrounds.forEach((b, i) => { const opt = document.createElement('option'); opt.value = i; opt.textContent = b.name; bgS.appendChild(opt); });
        }

        const currentMode = document.getElementById('thumb_bg_mode')?.value || 'preset';
        if (currentMode === 'preset' && state.bgData[0]) {
            state.images.bg.src = 'assets/cover/' + state.bgData[0].file;
        } else {
            // 截图模式下，确保预览刷新以显示占位背景
            refreshPreviews();
        }
        state.images.bg.onload = refreshPreviews;

        loadHistory();
    } catch (e) { console.error('Init Error:', e); }
}

async function loadSeasons() {
    const select = document.getElementById('bili_season_id');
    if (!select) return;
    try {
        const json = await PublisherService.fetchBiliSeasons();
        if (json.success && json.data.length > 0) {
            select.innerHTML = '<option value="">-- 请选择合集 --</option>';
            json.data.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.id; opt.textContent = s.title;
                select.appendChild(opt);
            });
        }
    } catch (e) { console.error('Failed to load seasons:', e); }
}

function initCanvasInteractions() {
    const interactionConfigs = [
        { canvas: elements.coverCanvas, key: 'cover' },
        { canvas: elements.videoCanvas, key: 'video' }
    ];

    interactionConfigs.forEach(item => {
        if (!item.canvas) return;
        
        let isDragging = false;
        let lastX, lastY;

        item.canvas.style.cursor = 'move';

        item.canvas.addEventListener('mousedown', e => {
            isDragging = true;
            lastX = e.clientX;
            lastY = e.clientY;
            e.preventDefault();
        });

        // Use window for mousemove/mouseup to handle drag out of canvas
        window.addEventListener('mousemove', e => {
            if (!isDragging) return;
            const rect = item.canvas.getBoundingClientRect();
            // Note: 1920/1080 is the internal resolution of the canvases
            const scaleX = 1920 / rect.width;
            const scaleY = 1080 / rect.height;
            
            const deltaX = (e.clientX - lastX) * scaleX;
            const deltaY = (e.clientY - lastY) * scaleY;
            
            state.transforms[item.key].x += deltaX;
            state.transforms[item.key].y += deltaY;
            
            lastX = e.clientX;
            lastY = e.clientY;
            refreshPreviews();
        });

        window.addEventListener('mouseup', () => {
            isDragging = false;
        });

        item.canvas.addEventListener('wheel', e => {
            // Only zoom if the mouse is over the canvas
            const rect = item.canvas.getBoundingClientRect();
            if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) return;

            e.preventDefault();
            const delta = -e.deltaY;
            const factor = 1.1; // Slightly faster zoom
            const oldScale = state.transforms[item.key].scale;
            const newScale = delta > 0 ? oldScale * factor : oldScale / factor;
            state.transforms[item.key].scale = Math.max(0.1, Math.min(10, newScale));
            refreshPreviews();
        }, { passive: false });
        
        // 双击重置
        item.canvas.addEventListener('dblclick', () => {
            state.transforms[item.key] = { x: 0, y: 0, scale: 1 };
            refreshPreviews();
        });
    });
}

function bindEvents() {
    const coverInputs = ['thumb_main', 'thumb_sub', 'thumb_date', 'overlay_opacity', 'thumb_bg_mode'];
    const vCoverInputs = ['v_thumb_main', 'v_thumb_sub', 'v_thumb_date', 'v_thumb_enabled'];
    const ctaInputs = ['cta_text', 'cta_position', 'cta_style'];
    
    coverInputs.forEach(id => document.getElementById(id)?.addEventListener('input', refreshPreviews));
    vCoverInputs.forEach(id => document.getElementById(id)?.addEventListener('input', refreshPreviews));
    ctaInputs.forEach(id => document.getElementById(id)?.addEventListener('input', refreshPreviews));
    
    document.getElementById('v_video_2_enabled')?.addEventListener('change', (e) => {
        const area = document.getElementById('v_video_2_config_area');
        if (e.target.checked) area.classList.remove('opacity-50', 'pointer-events-none');
        else area.classList.add('opacity-50', 'pointer-events-none');
    });

    document.getElementById('thumb_bg_mode')?.addEventListener('change', () => {
        const mode = document.getElementById('thumb_bg_mode').value;
        document.getElementById('preset_bg_area')?.classList.toggle('hidden', mode !== 'preset');
        document.getElementById('screenshot_bg_area')?.classList.toggle('hidden', mode === 'preset');
        refreshPreviews();
    });

    document.getElementById('select-org')?.addEventListener('change', (e) => {
        const valTag = document.getElementById('val-tag');
        if (valTag) valTag.value = e.target.value;
        refreshPreviews();
    });

    document.getElementById('cta_enabled')?.addEventListener('change', (e) => {
        const panel = document.getElementById('cta_config_panel');
        if (e.target.checked) panel.classList.remove('opacity-50', 'pointer-events-none');
        else panel.classList.add('opacity-50', 'pointer-events-none');
        refreshPreviews();
    });

    document.getElementById('v_thumb_enabled')?.addEventListener('change', (e) => {
        refreshPreviews();
    });

    document.getElementById('landscape_enabled')?.addEventListener('change', (e) => {
        const area = document.getElementById('landscape_config_area');
        if (e.target.checked) area.classList.remove('opacity-50', 'pointer-events-none');
        else area.classList.add('opacity-50', 'pointer-events-none');
    });

    document.getElementById('v_video_enabled')?.addEventListener('change', (e) => {
        const area = document.getElementById('v_video_config_area');
        if (e.target.checked) area.classList.remove('opacity-50', 'pointer-events-none');
        else area.classList.add('opacity-50', 'pointer-events-none');
    });

    document.getElementById('bili_enabled')?.addEventListener('change', (e) => {
        const panel = document.getElementById('bili_config_area');
        if (e.target.checked) panel.classList.remove('opacity-50', 'pointer-events-none');
        else panel.classList.add('opacity-50', 'pointer-events-none');
    });

    document.getElementById('select-bg')?.addEventListener('change', (e) => {
        const i = e.target.value;
        if (state.bgData[i]) state.images.bg.src = 'assets/cover/' + state.bgData[i].file;
    });

    elements.submitBtn?.addEventListener('click', handleTaskSubmit);
}

// --- 核心业务逻辑 ---

async function handleUpload(input, type) {
    const files = input.files;
    if (!files || files.length === 0) return;
    
    const area = document.getElementById(`area-${type}`), 
          spinner = document.getElementById(`spin-${type}`), 
          hiddenInput = document.getElementById(`val-${type}`);
          
    if (spinner) spinner.classList.remove('hidden');
    
    try {
        // 如果是音频，循环处理所有文件
        if (type === 'audio') {
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const data = await PublisherService.uploadFile(file);
                if (data.success) {
                    const segment = {
                        name: file.name,
                        path: data.path,
                        subtitle: "" // 由后端自动识别或用户手动输入
                    };
                    state.audio_segments.push(segment);
                }
            }
            
            renderAudioList();
            // 存储 JSON 数组字符串，后端将负责解析
            if (hiddenInput) hiddenInput.value = JSON.stringify(state.audio_segments.map(s => s.path));
            
            if (area) area.classList.add('uploaded');
            const label = document.getElementById(`label-audio`);
            if (label) {
                label.innerText = `已添加 ${state.audio_segments.length} 个片段`;
                label.classList.add('text-emerald-600');
            }
            const icon = document.getElementById(`icon-audio`);
            if (icon) icon.className = "fas fa-check-circle text-3xl text-emerald-500 mb-3";
            
        } else {
            // 非音频文件，保持单文件处理逻辑
            const file = files[0];
            const data = await PublisherService.uploadFile(file);
            if (data.success) {
                if (area) area.classList.add('uploaded');
                if (hiddenInput) hiddenInput.value = data.path;
                
                const icon = document.getElementById(`icon-${type}`);
                const label = document.getElementById(`label-${type}`);
                if (icon) icon.className = "fas fa-check-circle text-3xl text-emerald-500 mb-3";
                if (label) {
                    const typeMap = { 
                        'video': '片头视频', 'image': '正文图片', 'thumb-bg': '封面底图', 
                        'v-screenshot': '竖版截图', 'v-video-image': '竖版视频背景' 
                    };
                    label.innerText = `${typeMap[type] || '素材'} 已就绪`;
                    label.classList.add('text-emerald-600');
                }

                const viewUrl = `${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(data.path)}&_t=${Date.now()}`;
                
                if (type === 'image') {
                    document.getElementById('btn-sync-to-cover')?.classList.remove('hidden');
                    state.images.videoBody.onload = () => { console.log('✅ 正文图已加载'); refreshPreviews(); };
                    state.images.videoBody.onerror = () => console.error('❌ 正文图加载失败:', viewUrl);
                    state.images.videoBody.src = viewUrl;
                } else if (type === 'thumb-bg') {
                    const bgMode = document.getElementById('thumb_bg_mode');
                    if (bgMode) {
                        bgMode.value = 'screenshot';
                        document.getElementById('preset_bg_area')?.classList.add('hidden');
                        document.getElementById('screenshot_bg_area')?.classList.remove('hidden');
                    }
                    state.images.custom.onload = () => { console.log('✅ 封面图已加载'); refreshPreviews(); };
                    state.images.custom.src = viewUrl;
                } else if (type === 'v-screenshot') {
                    state.images.v_screenshot.onload = () => { console.log('✅ 竖版截图已加载'); refreshPreviews(); };
                    state.images.v_screenshot.src = viewUrl;
                } else if (type === 'v-video-image') {
                    const vImg = document.getElementById('v-video-preview-img');
                    if (vImg) {
                        vImg.onload = () => { vImg.classList.remove('hidden'); console.log('✅ 竖屏背景已就绪'); };
                        vImg.src = viewUrl;
                    }
                } else if (type === 'v-video-2-image') {
                    const vImg2 = document.getElementById('v-video-2-preview-img');
                    if (vImg2) {
                        vImg2.onload = () => { vImg2.classList.remove('hidden'); console.log('✅ 备选竖屏背景已就绪'); };
                        vImg2.src = viewUrl;
                    }
                }
            }
        }
        
        // 统一重绘
        refreshPreviews();
        
    } catch (e) { 
        console.error('Upload Error:', e); 
        alert('文件上传失败，请重试');
    } finally { 
        if (spinner) spinner.classList.add('hidden'); 
    }
}

function renderAudioList() {
    const wrapper = document.getElementById('audio-list-wrapper');
    const container = document.getElementById('audio-list');
    const badge = document.getElementById('audio-count-badge');
    
    if (!container || !wrapper) return;
    
    if (state.audio_segments.length === 0) {
        wrapper.classList.add('hidden');
        return;
    }
    
    wrapper.classList.remove('hidden');
    if (badge) badge.innerText = `${state.audio_segments.length} Clips`;
    
    container.innerHTML = state.audio_segments.map((s, i) => `
        <div class="flex flex-col bg-slate-50 border border-slate-100 rounded-xl hover:bg-white transition-all group p-3">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-3 min-w-0">
                    <span class="w-5 h-5 bg-slate-200 text-slate-500 rounded-full flex items-center justify-center text-[9px] font-black">${i + 1}</span>
                    <div class="truncate">
                        <p class="text-[11px] font-bold text-slate-700 truncate">${s.name}</p>
                    </div>
                </div>
                <button type="button" onclick="removeAudioSegment(${i})" class="text-slate-300 hover:text-red-500 p-1 transition-colors">
                    <i class="fas fa-trash-alt text-[11px]"></i>
                </button>
            </div>
            <input type="text" 
                class="audio-subtitle-input w-full bg-white/50 border border-slate-200 rounded-lg px-3 py-1.5 text-[11px] font-medium focus:ring-1 focus:ring-blue-500 outline-none transition-all" 
                placeholder="(可选) 手动覆盖字幕文字，留空则由系统自动识别" 
                oninput="state.audio_segments[${i}].subtitle = this.value"
                value="${s.subtitle || ''}">
        </div>
    `).join('');
}

window.removeAudioSegment = (index) => {
    state.audio_segments.splice(index, 1);
    renderAudioList();
    const hiddenInput = document.getElementById(`val-audio`);
    if (hiddenInput) hiddenInput.value = state.audio_segments.length > 0 ? JSON.stringify(state.audio_segments.map(s => s.path)) : "";
    
    const label = document.getElementById(`label-audio`);
    if (label) {
        if (state.audio_segments.length > 0) {
            label.innerText = `已添加 ${state.audio_segments.length} 个片段`;
        } else {
            label.innerText = "1. 核心音频素材 (时长基准)";
            label.classList.remove('text-emerald-600');
            const icon = document.getElementById(`icon-audio`);
            if (icon) icon.className = "fas fa-microphone-lines text-3xl text-slate-300 mb-3";
            document.getElementById('area-audio')?.classList.remove('uploaded');
        }
    }
};

async function handleTaskSubmit() {
    const fd = new FormData(elements.publishForm);
    if (!fd.get('audio_path')) { alert('请上传音频素材'); return; }

    const vSubtitles = state.audio_segments.map(s => s.subtitle || "");

    const payload = {
        video: { 
            enabled: document.getElementById('landscape_enabled')?.checked ?? true,
            sub_burn_enabled: document.getElementById('sub_burn_enabled')?.checked ?? false,
            asr_model: fd.get('asr_model') || 'base',
            video_path: fd.get('video_path'), 
            audio_path: fd.get('audio_path'),
            image_path: fd.get('image_path'),
            audio_start: fd.get('audio_start'), audio_end_offset: fd.get('audio_end_offset'),
            transform: state.transforms.video,
            cta: document.getElementById('cta_enabled')?.checked ? { 
                text: fd.get('cta_text') || "", 
                position: fd.get('cta_position') || "bottom_bar", 
                style: fd.get('cta_style') || "premium_dark" 
            } : null
        },
        vertical_video: {
            enabled: document.getElementById('v_video_enabled')?.checked ?? true,
            asr_model: fd.get('asr_model') || 'base',
            image_path: fd.get('v_video_image_path'),
            subtitles: vSubtitles
        },
        vertical_video_2: {
            enabled: document.getElementById('v_video_2_enabled')?.checked ?? false,
            image_path: fd.get('v_video_2_image_path')
        },
        bilibili: {
            enabled: document.getElementById('bili_enabled')?.checked ?? true,
            title: fd.get('bili_title') || (document.getElementById('thumb_main')?.value || "未命名视频").split('\n')[0],
            desc: fd.get('bili_desc') || "由一键发布台自动合成。",
            tid: parseInt(fd.get('bili_tid') || 17), 
            tags: (fd.get('bili_tags') || "财经, 投资").split(',').map(t => t.trim()).filter(t => t),
            copyright: parseInt(fd.get('bili_copyright') || 1),
            no_reprint: document.querySelector('input[name="bili_no_reprint"]')?.checked ? 1 : 0
        }
    };
    
    if (document.getElementById('thumb_enabled')?.checked) {
        payload.thumbnail = {
            main_title: document.getElementById('thumb_main').value,
            sub_title: document.getElementById('thumb_sub').value,
            tag_text: document.getElementById('val-tag').value,
            date_text: document.getElementById('thumb_date').value,
            bg_index: document.getElementById('select-bg').value,
            layout: 'left', 
            custom_bg_path: document.getElementById('val-thumb-bg')?.value, 
            overlay_opacity: document.getElementById('overlay_opacity')?.value || 0.5,
            transform: state.transforms.cover
        };
    }

    if (document.getElementById('v_thumb_enabled')?.checked) {
        payload.vertical_thumbnail = {
            main_title: document.getElementById('v_thumb_main').value,
            sub_title: document.getElementById('v_thumb_sub').value,
            date_text: document.getElementById('v_thumb_date').value,
            screenshot_path: document.getElementById('val-v-screenshot')?.value
        };
    }

    try {
        elements.submitBtn.disabled = true; elements.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 提交中...';
        const res = await PublisherService.submitTask(payload);
        if (res.success) {
            currentTaskId = res.task_id;
            startPolling(res.task_id);
            resetFormUI(); // 提交成功后复位
            setTimeout(loadHistory, 1000); // 1秒后刷新历史，确保后端已入库
        }
    } catch (e) { alert('提交失败'); } finally { 
        elements.submitBtn.disabled = false; 
        elements.submitBtn.innerHTML = '<i class="fas fa-rocket"></i> 提交新任务';
    }
}

function resetFormUI() {
    // 1. 重置 HTML 表单
    elements.publishForm.reset();
    
    // 2. 清空状态和列表
    state.audio_segments = [];
    renderAudioList();
    
    // 3. 清空隐藏路径
    ['video', 'audio', 'image', 'thumb-bg', 'v-video-image', 'v-video-2-image'].forEach(type => {
        const input = document.getElementById(`val-${type}`);
        if (input) input.value = "";
        
        const area = document.getElementById(`area-${type}`);
        if (area) area.classList.remove('uploaded');
        
        // 恢复图标和文字
        const icon = document.getElementById(`icon-${type}`);
        const label = document.getElementById(`label-${type}`);
        if (icon) {
            const iconMap = { 
                'video': 'fa-film', 'audio': 'fa-microphone', 'image': 'fa-image', 
                'thumb-bg': 'fa-upload', 'v-video-image': 'fa-image', 'v-video-2-image': 'fa-image' 
            };
            icon.className = `fas ${iconMap[type]} text-3xl text-slate-300 mb-3`;
        }
        if (label) {
            const labelMap = { 
                'video': 'Step 1: 片头', 'audio': 'Step 2: 音频', 'image': 'Step 3: 正文图', 
                'thumb-bg': '选择图片...', 'v-video-image': '上传自定义背景...', 'v-video-2-image': '上传备选背景图...' 
            };
            label.innerText = labelMap[type];
            label.classList.remove('text-emerald-600');
        }
    });

    // 3. 隐藏联动预览
    document.getElementById('btn-sync-to-cover')?.classList.add('hidden');
    document.getElementById('v-video-preview-img')?.classList.add('hidden');
    document.getElementById('v-video-2-preview-img')?.classList.add('hidden');
    
    // 4. 重置状态对象
    state.images.videoBody.src = "";
    state.images.custom.src = "";
    
    // 5. 触发一次重绘
    refreshPreviews();
}

// --- UI 更新函数 ---

function refreshPreviews() {
    if (typeof CanvasRenderer === 'undefined') return;

    const placeholder = document.getElementById('cta-placeholder');
    if (state.images.videoBody.src && state.images.videoBody.complete && state.images.videoBody.naturalWidth !== 0) {
        placeholder?.classList.add('opacity-0', 'pointer-events-none');
    } else {
        placeholder?.classList.remove('opacity-0', 'pointer-events-none');
    }

    const coverConfig = {
        mode: document.getElementById('thumb_bg_mode')?.value || 'screenshot',
        tagText: document.getElementById('val-tag')?.value || "",
        mainTitle: document.getElementById('thumb_main')?.value || "",
        subTitle: document.getElementById('thumb_sub')?.value || "",
        dateText: document.getElementById('thumb_date')?.value || "",
        overlayOpacity: document.getElementById('overlay_opacity')?.value || 0.5,
        transform: state.transforms.cover
    };
    const videoConfig = {
        enabled: document.getElementById('cta_enabled')?.checked || false,
        text: document.getElementById('cta_text')?.value || "",
        position: document.getElementById('cta_position')?.value || "bottom_bar",
        style: document.getElementById('cta_style')?.value || "premium_dark",
        transform: state.transforms.video
    };
    const verticalConfig = {
        mainTitle: document.getElementById('v_thumb_main')?.value || "",
        subTitle: document.getElementById('v_thumb_sub')?.value || "",
        dateText: document.getElementById('v_thumb_date')?.value || ""
    };
    CanvasRenderer.drawCover(coverCtx, elements.coverCanvas, state.images, coverConfig);
    CanvasRenderer.drawVideoPreview(videoCtx, elements.videoCanvas, state.images.videoBody, videoConfig);
    CanvasRenderer.drawVerticalCover(verticalCtx, elements.verticalCanvas, state.images, verticalConfig);
}

async function loadHistory() {
    try {
        const res = await PublisherService.fetchTasks();
        if (res.success && res.data.length > 0) {
            let hasProcessing = false;
            elements.historyList.innerHTML = res.data.map(task => {
                // 修复时区偏移：确保末尾有 Z
                const dateStr = task.created_at.endsWith('Z') ? task.created_at : task.created_at + 'Z';
                const date = new Date(dateStr).toLocaleString('zh-CN', { 
                    hour12: false, 
                    month: 'numeric', 
                    day: 'numeric', 
                    hour: '2-digit', 
                    minute: '2-digit',
                    second: '2-digit'
                });
                let statusColor = 'text-slate-500', icon = 'fa-circle-dot';
                if (task.status === 'completed' || task.status === 'success') { statusColor = 'text-emerald-500'; icon = 'fa-check-circle'; }
                if (task.status === 'failed') { statusColor = 'text-red-500'; icon = 'fa-times-circle'; }
                if (task.status === 'processing' || task.status === 'pending') { 
                    statusColor = 'text-blue-500'; icon = 'fa-spinner fa-spin'; 
                    hasProcessing = true;
                }
                const showYTBtn = task.status === 'completed' && (!task.result || !task.result.youtube_result);
                return `
                <div class="history-item flex justify-between items-center group">
                    <div class="min-w-0 pr-2 flex-1" onclick="trackTask('${task.id}')">
                        <div class="font-bold text-slate-700 truncate" title="${task.title}">${task.title}</div>
                        <div class="text-[10px] text-slate-400 flex gap-2"><span class="font-mono">${date}</span> <span class="uppercase font-black text-[9px] opacity-50">${task.status}</span></div>
                    </div>
                    <div class="flex items-center gap-2">
                        <div class="${statusColor} text-sm"><i class="fas ${icon}"></i></div>
                        ${showYTBtn ? `<button onclick="postToYouTube('${task.id}', event)" class="text-slate-400 hover:text-red-600 p-1"><i class="fab fa-youtube"></i></button>` : ''}
                        <button onclick="retryTask('${task.id}', event)" class="text-slate-400 hover:text-blue-600 p-1"><i class="fas fa-rotate-right"></i></button>
                    </div>
                </div>`;
            }).join('');

            // 如果有正在处理的任务，开启短频率自动刷新列表
            if (hasProcessing) {
                if (!historyInterval) historyInterval = setInterval(loadHistory, 5000);
            } else if (historyInterval) {
                clearInterval(historyInterval);
                historyInterval = null;
            }
        }
    } catch (e) { console.error(e); }
}

function startPolling(taskId) {
    elements.statusCard?.classList.remove('hidden');
    startTime = Date.now();
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        const res = await PublisherService.getTaskStatus(taskId);
        if (res.success) updateProgressUI(res.data);
    }, 2000);
}

function updateProgressUI(task) {
    if (elements.progressBar) elements.progressBar.style.width = `${task.progress}%`;
    if (elements.stepDisplay) {
        const map = { 
            'synthesis': '🎬 横屏视频合成', 
            'v_synthesis': '📱 竖屏视频合成', 
            'thumbnail': '🎨 封面生成', 
            'upload': '🚀 上传B站', 
            'upload_youtube': '📺 上传YouTube',
            'done': '✅ 完成' 
        };
        elements.stepDisplay.innerText = map[task.current_step] || task.current_step;
    }
    if (task.status === 'completed' || task.status === 'failed') {
        clearInterval(pollInterval);
        loadHistory(); // 任务结束后最后刷新一次历史
    }
}

function trackTask(taskId) { currentTaskId = taskId; startPolling(taskId); }

async function retryTask(taskId, event) {
    if(event) event.stopPropagation();
    if(!confirm('确定重试此任务吗？')) return;
    const res = await PublisherService.retryTask(taskId);
    if(res.success) { currentTaskId = res.task_id; startPolling(res.task_id); loadHistory(); }
}

async function postToYouTube(taskId, event) {
    if(event) event.stopPropagation();
    if(!confirm('确定补发到 YouTube 吗？')) return;
    const res = await PublisherService.postToYouTube(taskId);
    if(res.success) { currentTaskId = taskId; startPolling(taskId); loadHistory(); }
}

// --- 暴露接口 ---
window.handleUpload = handleUpload;
window.syncToCover = (e) => {
    const imgPath = document.getElementById('val-image').value;
    if (!imgPath) return;
    document.getElementById('val-thumb-bg').value = imgPath;
    
    const bgMode = document.getElementById('thumb_bg_mode');
    if (bgMode) {
        bgMode.value = 'screenshot';
        document.getElementById('preset_bg_area')?.classList.add('hidden');
        document.getElementById('screenshot_bg_area')?.classList.remove('hidden');
    }
    
    state.images.custom.src = PublisherService.API_BASE + '/resources/view?path=' + encodeURIComponent(imgPath) + '&_t=' + Date.now();
    refreshPreviews();
};
window.trackTask = trackTask;
window.retryTask = retryTask;
window.postToYouTube = postToYouTube;
window.checkCtaPosition = (el) => {
    const warning = document.getElementById('cta_warning');
    if (el.value === 'top_right') warning?.classList.remove('hidden');
    else warning?.classList.add('hidden');
    refreshPreviews();
};
window.updateCounter = (id, max) => {
    const el = document.getElementById(id);
    const counter = document.getElementById(id + '_counter');
    if (el && counter) counter.innerText = `${el.value.length}/${max}`;
};
window.drawCover = refreshPreviews;
window.drawVerticalCover = refreshPreviews;
window.syncTitleFromBili = () => {
    const bt = document.getElementById('bili_title')?.value;
    if (bt && document.getElementById('thumb_main')) {
        document.getElementById('thumb_main').value = bt.replace(/\|/g, '\n');
        refreshPreviews();
    }
};
window.syncVTitle = () => {
    const mt = document.getElementById('thumb_main')?.value;
    const st = document.getElementById('thumb_sub')?.value;
    if (mt) document.getElementById('v_thumb_main').value = mt;
    if (st) document.getElementById('v_thumb_sub').value = st;
    refreshPreviews();
};
window.syncVThumbToVideo = (e) => {
    if (e) e.stopPropagation();
    const canvas = document.getElementById('verticalCanvas');
    if (!canvas) return;
    
    const dataUrl = canvas.toDataURL('image/jpeg', 0.95);
    
    // 模拟文件上传过程
    const blob = dataURLToBlob(dataUrl);
    const file = new File([blob], "auto_v_bg.jpg", { type: "image/jpeg" });
    
    // 使用现有的 handleUpload 逻辑
    const mockInput = { files: [file] };
    handleUpload(mockInput, 'v-video-image');
};

// 辅助函数：DataURL 转 Blob
function dataURLToBlob(dataurl) {
    var arr = dataurl.split(','), mime = arr[0].match(/:(.*?);/)[1],
        bstr = atob(arr[1]), n = bstr.length, u8arr = new Uint8Array(n);
    while(n--){ u8arr[n] = bstr.charCodeAt(n); }
    return new Blob([u8arr], {type:mime});
}

window.toggleSafeZone = () => {
    const overlay = document.getElementById('safeZoneOverlay');
    if (overlay) overlay.classList.toggle('hidden');
};

window.changeZoom = (key, factor) => {
    if (!state.transforms[key]) return;
    const oldScale = state.transforms[key].scale;
    state.transforms[key].scale = Math.max(0.1, Math.min(10, oldScale * factor));
    refreshPreviews();
};

window.resetTransform = (key) => {
    if (!state.transforms[key]) return;
    state.transforms[key] = { x: 0, y: 0, scale: 1 };
    refreshPreviews();
};

window.downloadVerticalPreview = () => downloadCanvasAsImage('verticalCanvas', 'Vertical_Cover');
window.downloadLandscapePreview = () => downloadCanvasAsImage('previewCanvas', 'Landscape_Cover');
window.downloadVideoPreview = () => downloadCanvasAsImage('videoPreviewCanvas', 'Landscape_CTA');

/**
 * 通用下载 Canvas 为图片
 * @param {string} canvasId 
 * @param {string} filenamePrefix 
 */
function downloadCanvasAsImage(canvasId, filenamePrefix) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    // 强制一次完整渲染
    if (typeof refreshPreviews === 'function') refreshPreviews();
    
    // 导出为 JPEG (质量 0.95)
    const dataUrl = canvas.toDataURL('image/jpeg', 0.95);
    const link = document.createElement('a');
    
    // 生成带时间戳的文件名
    const now = new Date();
    const ts = `${now.getMonth()+1}${now.getDate()}_${now.getHours()}${now.getMinutes()}`;
    link.download = `${filenamePrefix}_${ts}.jpg`;
    link.href = dataUrl;
    
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
window.closeBiliModal = () => document.getElementById('bili-modal').classList.add('hidden');
window.toggleBgMode = () => { /* 内部联动处理 */ };
