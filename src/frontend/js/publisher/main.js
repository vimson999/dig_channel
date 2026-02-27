/**
 * Publisher Controller - 页面入口协调器
 */

let currentTaskId = null;
let pollInterval = null;
let historyInterval = null;
let startTime = null;

const state = {
    images: { 
        bg: new Image(),      
        custom: new Image(),  
        videoBody: new Image() 
    },
    bgData: []
};

// --- DOM 元素引用 ---
const elements = {
    coverCanvas: document.getElementById('previewCanvas'),
    videoCanvas: document.getElementById('videoPreviewCanvas'),
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

// --- 初始化入口 ---

document.addEventListener('DOMContentLoaded', async () => {
    initUI();
    await loadInitialData();
    bindEvents();
});

function initUI() {
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

        if (state.bgData[0]) state.images.bg.src = 'assets/cover/' + state.bgData[0].file;
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

function bindEvents() {
    const coverInputs = ['thumb_main', 'thumb_sub', 'thumb_date', 'overlay_opacity', 'thumb_bg_mode'];
    const ctaInputs = ['cta_text', 'cta_position', 'cta_style'];
    
    coverInputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', refreshPreviews);
    });
    
    ctaInputs.forEach(id => document.getElementById(id)?.addEventListener('input', refreshPreviews));
    
    document.getElementById('thumb_bg_mode')?.addEventListener('change', () => {
        const mode = document.getElementById('thumb_bg_mode').value;
        document.getElementById('preset_bg_area')?.classList.toggle('hidden', mode !== 'preset');
        document.getElementById('screenshot_bg_area')?.classList.toggle('hidden', mode !== 'screenshot');
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
    const file = input.files[0]; if (!file) return;
    const area = document.getElementById(`area-${type}`), spinner = document.getElementById(`spin-${type}`), hiddenInput = document.getElementById(`val-${type}`);
    if (spinner) spinner.classList.remove('hidden');
    
    try {
        const data = await PublisherService.uploadFile(file);
        if (data.success) {
            if (area) area.classList.add('uploaded');
            if (hiddenInput) hiddenInput.value = data.path;
            
            const icon = document.getElementById(`icon-${type}`);
            const label = document.getElementById(`label-${type}`);
            if (icon) icon.className = "fas fa-check-circle text-3xl text-emerald-500 mb-3";
            if (label) {
                const typeMap = { 'video': '片头视频', 'audio': '音频解说', 'image': '正文图片', 'thumb-bg': '封面底图' };
                label.innerText = `${typeMap[type] || '素材'} 已就绪`;
                label.classList.add('text-emerald-600');
            }

            const viewUrl = PublisherService.API_BASE + '/resources/view?path=' + encodeURIComponent(data.path);
            if (type === 'image') {
                document.getElementById('btn-sync-to-cover')?.classList.remove('hidden');
                state.images.videoBody.crossOrigin = "anonymous";
                state.images.videoBody.onload = refreshPreviews;
                state.images.videoBody.src = viewUrl;
            } else if (type === 'thumb-bg') {
                state.images.custom.crossOrigin = "anonymous";
                state.images.custom.onload = refreshPreviews;
                state.images.custom.src = viewUrl;
            }
        }
    } catch (e) { console.error(e); } finally { if (spinner) spinner.classList.add('hidden'); }
}

async function handleTaskSubmit() {
    const fd = new FormData(elements.publishForm);
    if (!fd.get('audio_path') || !fd.get('image_path')) { alert('请上传必需素材 (音频 + 图片)'); return; }

    const payload = {
        video: { 
            video_path: fd.get('video_path'), audio_path: fd.get('audio_path'), image_path: fd.get('image_path'),
            audio_start: fd.get('audio_start'), audio_end_offset: fd.get('audio_end_offset'),
            cta: document.getElementById('cta_enabled')?.checked ? { 
                text: fd.get('cta_text') || "", 
                position: fd.get('cta_position') || "bottom_bar", 
                style: fd.get('cta_style') || "premium_dark" 
            } : null
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
            overlay_opacity: document.getElementById('overlay_opacity')?.value || 0.5
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
    
    // 2. 清空隐藏路径
    ['video', 'audio', 'image', 'thumb-bg'].forEach(type => {
        const input = document.getElementById(`val-${type}`);
        if (input) input.value = "";
        
        const area = document.getElementById(`area-${type}`);
        if (area) area.classList.remove('uploaded');
        
        // 恢复图标和文字
        const icon = document.getElementById(`icon-${type}`);
        const label = document.getElementById(`label-${type}`);
        if (icon) {
            const iconMap = { 'video': 'fa-film', 'audio': 'fa-microphone', 'image': 'fa-image', 'thumb-bg': 'fa-upload' };
            icon.className = `fas ${iconMap[type]} text-3xl text-slate-300 mb-3`;
        }
        if (label) {
            const labelMap = { 'video': 'Step 1: 片头', 'audio': 'Step 2: 音频', 'image': 'Step 3: 正文图', 'thumb-bg': '选择图片...' };
            label.innerText = labelMap[type];
            label.classList.remove('text-emerald-600');
        }
    });

    // 3. 隐藏联动按钮
    document.getElementById('btn-sync-to-cover')?.classList.add('hidden');
    
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
        mode: document.getElementById('thumb_bg_mode')?.value || 'preset',
        tagText: document.getElementById('val-tag')?.value || "",
        mainTitle: document.getElementById('thumb_main')?.value || "",
        subTitle: document.getElementById('thumb_sub')?.value || "",
        dateText: document.getElementById('thumb_date')?.value || "",
        overlayOpacity: document.getElementById('overlay_opacity')?.value || 0.5
    };
    const videoConfig = {
        enabled: document.getElementById('cta_enabled')?.checked || false,
        text: document.getElementById('cta_text')?.value || "",
        position: document.getElementById('cta_position')?.value || "bottom_bar",
        style: document.getElementById('cta_style')?.value || "premium_dark"
    };
    CanvasRenderer.drawCover(coverCtx, elements.coverCanvas, state.images, coverConfig);
    CanvasRenderer.drawVideoPreview(videoCtx, elements.videoCanvas, state.images.videoBody, videoConfig);
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
        const map = { 'synthesis': '🎬 视频合成', 'thumbnail': '🎨 封面生成', 'upload': '🚀 上传B站', 'done': '✅ 完成' };
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
    document.getElementById('thumb_bg_mode').value = 'screenshot';
    state.images.custom.src = PublisherService.API_BASE + '/resources/view?path=' + encodeURIComponent(imgPath);
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
window.syncTitleFromBili = () => {
    const bt = document.getElementById('bili_title')?.value;
    if (bt && document.getElementById('thumb_main')) {
        document.getElementById('thumb_main').value = bt.replace(/\|/g, '\n');
        refreshPreviews();
    }
};
window.closeBiliModal = () => document.getElementById('bili-modal').classList.add('hidden');
window.toggleBgMode = () => { /* 内部联动处理 */ };
