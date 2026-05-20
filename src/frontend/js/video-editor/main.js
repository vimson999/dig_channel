/**
 * Pro Video Editor - 核心逻辑 (修复连续播放与删除功能)
 */

// 缩放对数映射：slider(0-100) ↔ zoom(0.1-10000px/s)
const ZOOM_MIN = 0.1, ZOOM_MAX = 10000, ZOOM_DEFAULT = 50;
const DEFAULT_SUBTITLE_Y_BY_RATIO = {
    '16:9': 0.90,
    '9:16': 0.72
};
function sliderToZoom(v) { return ZOOM_MIN * Math.pow(ZOOM_MAX / ZOOM_MIN, v / 100); }
function zoomToSlider(z) { return Math.round(Math.log(z / ZOOM_MIN) / Math.log(ZOOM_MAX / ZOOM_MIN) * 100); }
function updateZoomLabel(zoom) {
    const el = document.getElementById('zoom-label');
    if (el) el.textContent = (zoom / ZOOM_DEFAULT).toFixed(1) + '×';
}
function applyZoom(sliderVal) {
    sliderVal = Math.max(0, Math.min(100, sliderVal));
    const el = document.getElementById('timeline-zoom-slider');
    if (el) el.value = sliderVal;
    state.zoom = sliderToZoom(sliderVal);
    updateZoomLabel(state.zoom);
    renderTimeline();
    seekTo(state.current_time);
}

const state = {
    audio_segments: [],
    image_path: null,
    video_path: null,
    background_type: null,
    previewImage: (() => { const img = new Image(); img.crossOrigin = "anonymous"; return img; })(),
    previewVideo: (() => { const video = document.createElement('video'); video.crossOrigin = "anonymous"; video.muted = true; video.playsInline = true; video.preload = "metadata"; return video; })(),
    current_time: 0,
    total_duration: 0,
    zoom: ZOOM_DEFAULT,
    active_index: -1,
    is_playing: false,
    play_raf: null,
    current_audio_instance: null,
    aspect_ratio: '16:9',
    tasks: [],
    subtitle_y_pct: DEFAULT_SUBTITLE_Y_BY_RATIO['16:9']
};

const elements = {
    audioTrack: document.getElementById('audio-track'),
    timelineRuler: document.getElementById('timeline-ruler'),
    playhead: document.getElementById('playhead'),
    currentTimeDisplay: document.getElementById('current-time'),
    totalTimeDisplay: document.getElementById('total-time'),
    previewCanvas: document.getElementById('preview-canvas'),
    submitBtn: document.getElementById('submit-task-btn'),
    timelineScroll: document.getElementById('timeline-scroll'),
    taskList: document.getElementById('task-list'),
    videoTitle: document.getElementById('video-title'),
    clipSettings: document.getElementById('clip-settings'),
    clipStartInput: document.getElementById('clip-start'),
    clipEndInput: document.getElementById('clip-end'),
    activeClipName: document.getElementById('active-clip-name'),
    clipDurationVal: document.getElementById('clip-duration-val'),
    clipTotalVal: document.getElementById('clip-total-val')
};

// --- 初始化 ---
document.addEventListener('DOMContentLoaded', () => {
    updateZoomLabel(state.zoom);
    initEventListeners();
    initSubtitleDrag();
    renderTimeline();
    refreshTaskList();
    setInterval(refreshTaskList, 4000);
});

function initSubtitleDrag() {
    const overlay = document.getElementById('subtitle-drag-overlay');
    const handle = document.getElementById('subtitle-handle');
    if (!overlay || !handle) return;

    let dragging = false;
    setSubtitlePosition(state.subtitle_y_pct);

    handle.addEventListener('mousedown', (e) => {
        dragging = true;
        e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const rect = overlay.getBoundingClientRect();
        const pct = (e.clientY - rect.top) / rect.height;
        setSubtitlePosition(pct);
        console.log(`📍 [Subtitle Drag] y_pct: ${state.subtitle_y_pct.toFixed(4)}`);
    });

    window.addEventListener('mouseup', () => { dragging = false; });

    // 双击重置为当前画幅默认位置
    handle.addEventListener('dblclick', () => {
        setSubtitlePosition(getDefaultSubtitleY(state.aspect_ratio));
    });
}

function getDefaultSubtitleY(ratio) {
    return DEFAULT_SUBTITLE_Y_BY_RATIO[ratio] ?? 0.90;
}

function setSubtitlePosition(pct) {
    const handle = document.getElementById('subtitle-handle');
    const hint = document.getElementById('subtitle-pos-hint');
    const nextPct = Math.max(0.03, Math.min(0.97, Number(pct)));
    state.subtitle_y_pct = nextPct;
    if (handle) handle.style.top = `${nextPct * 100}%`;
    if (hint) hint.textContent = `${Math.round(nextPct * 100)}%`;
}

function initEventListeners() {
    elements.timelineScroll.addEventListener('click', (e) => {
        if (e.target.closest('.audio-clip')) return;
        const rect = elements.timelineScroll.getBoundingClientRect();
        const x = e.clientX - rect.left + elements.timelineScroll.scrollLeft;
        seekTo(x / state.zoom);
    });

    document.getElementById('audio-upload').addEventListener('change', async (e) => {
        const files = e.target.files;
        for (let file of files) {
            const data = await uploadFile(file);
            if (data.success) addAudioSegment(file.name, data.path);
        }
        // 用第一个文件名自动填充标题（仅当标题为空时）
        if (files.length > 0 && !elements.videoTitle.value.trim()) {
            elements.videoTitle.value = files[0].name.replace(/\.[^.]+$/, '');
        }
    });

    document.getElementById('image-upload').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        const data = await uploadFile(file);
        if (data.success) {
            state.image_path = data.path;
            state.video_path = null;
            state.background_type = 'image';
            const viewUrl = `${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(data.path)}&_t=${Date.now()}`;
            state.previewImage.onload = () => updatePreview();
            state.previewImage.onerror = () => console.error('❌ 预览图加载失败:', viewUrl);
            state.previewImage.src = viewUrl;
            updateMediaStatus(file.name, '图片背景');
        }
    });

    document.getElementById('video-upload')?.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const data = await uploadFile(file);
        if (data.success) {
            state.video_path = data.path;
            state.image_path = null;
            state.background_type = 'video';
            const viewUrl = `${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(data.path)}&_t=${Date.now()}`;
            state.previewVideo.onloadeddata = () => {
                try {
                    state.previewVideo.currentTime = Math.min(0.2, state.previewVideo.duration || 0);
                } catch (err) {
                    updatePreview();
                }
            };
            state.previewVideo.onseeked = () => updatePreview();
            state.previewVideo.onerror = () => console.error('❌ 预览视频加载失败:', viewUrl);
            state.previewVideo.src = viewUrl;
            updateMediaStatus(file.name, '视频背景');
        }
    });

    document.getElementById('play-btn').addEventListener('click', togglePlay);
    document.getElementById('split-btn').addEventListener('click', splitActiveSegment);
    document.getElementById('trim-start-btn').addEventListener('click', trimActiveStart);
    document.getElementById('trim-end-btn').addEventListener('click', trimActiveEnd);

    elements.submitBtn.addEventListener('click', handleSubmit);
    document.getElementById('cta-enabled').addEventListener('change', updatePreview);
    document.getElementById('cta-text').addEventListener('input', updatePreview);
    document.getElementById('cta-position')?.addEventListener('change', updatePreview);
    document.getElementById('cta-style')?.addEventListener('change', updatePreview);

    document.getElementById('timeline-zoom-slider')?.addEventListener('input', (e) => {
        applyZoom(parseInt(e.target.value));
    });
    document.getElementById('zoom-in-btn')?.addEventListener('click', () => {
        applyZoom(parseInt(document.getElementById('timeline-zoom-slider').value) + 10);
    });
    document.getElementById('zoom-out-btn')?.addEventListener('click', () => {
        applyZoom(parseInt(document.getElementById('timeline-zoom-slider').value) - 10);
    });

    elements.taskList?.addEventListener('click', (e) => {
        const applyBtn = e.target.closest('[data-apply-task-id]');
        if (!applyBtn) return;
        e.preventDefault();
        applyTaskConfig(applyBtn.dataset.applyTaskId);
    });

    // 片段设置面板事件
    elements.clipStartInput?.addEventListener('change', (e) => {
        if (state.active_index === -1) return;
        const seg = state.audio_segments[state.active_index];
        const val = parseFormattedTime(e.target.value);
        seg.start = Math.max(0, Math.min(val, seg.duration - seg.end_offset - 0.1));
        updateClipSettingsPanel();
        renderTimeline();
        updateTotalDuration();
    });

    elements.clipEndInput?.addEventListener('change', (e) => {
        if (state.active_index === -1) return;
        const seg = state.audio_segments[state.active_index];
        const val = parseFormattedTime(e.target.value);
        seg.end_offset = Math.max(0, Math.min(seg.duration - val, seg.duration - seg.start - 0.1));
        updateClipSettingsPanel();
        renderTimeline();
        updateTotalDuration();
    });
}

function updateClipSettingsPanel() {
    if (state.active_index === -1) {
        elements.clipSettings?.classList.add('hidden');
        return;
    }

    const seg = state.audio_segments[state.active_index];
    elements.clipSettings?.classList.remove('hidden');
    elements.activeClipName.textContent = seg.name;
    elements.clipStartInput.value = secondsToFormattedTime(seg.start);
    elements.clipEndInput.value = secondsToFormattedTime(seg.duration - seg.end_offset);
    elements.clipDurationVal.textContent = ((seg.duration - seg.start - seg.end_offset) / seg.speed).toFixed(2);
    elements.clipTotalVal.textContent = secondsToFormattedTime(seg.duration);
}

// --- 素材与轨道逻辑 ---

async function uploadFile(file) {
    try { return await PublisherService.uploadFile(file); }
    catch (err) { return { success: false }; }
}

function updateMediaStatus(fileName, label) {
    const mediaList = document.getElementById('media-list');
    if (!mediaList) return;
    mediaList.innerHTML = `
        <div class="rounded-lg border border-zinc-700 bg-zinc-900/70 p-3">
            <div class="text-[8px] font-black uppercase tracking-widest text-zinc-500">${label}</div>
            <div class="mt-1 truncate text-[10px] font-bold text-zinc-200">${fileName}</div>
        </div>
    `;
}

function addAudioSegment(name, path, options = {}) {
    const viewUrl = `${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(path)}`;
    const audio = new Audio(viewUrl);
    audio.onloadedmetadata = () => {
        const segment = {
            id: Date.now() + Math.random(),
            name: name,
            path: path,
            duration: audio.duration,
            start: Number(options.start || 0),
            end_offset: Number(options.end_offset || 0),
            speed: Number(options.speed || 1.0),
            subtitle: options.subtitle || "",
            audio_instance: audio
        };
        state.audio_segments.push(segment);
        renderTimeline();
        updateTotalDuration();
    };
    audio.onerror = () => showToast(`音频无法加载：${name}`, 'error');
}

function renderTimeline() {
    elements.audioTrack.innerHTML = '';
    let currentX = 0;

    state.audio_segments.forEach((seg, index) => {
        const actualDuration = (seg.duration - seg.start - seg.end_offset) / seg.speed;
        const width = actualDuration * state.zoom;

        const clip = document.createElement('div');
        clip.className = `audio-clip absolute h-20 bg-emerald-600/20 border-x-2 border-emerald-500/50 rounded-md flex flex-col justify-center px-3 cursor-pointer transition-all hover:bg-emerald-500/30 ${state.active_index === index ? 'clip-active bg-emerald-500/40' : ''}`;
        clip.style.left = `${currentX}px`;
        clip.style.width = `${width}px`;
        
        clip.innerHTML = `
            <div class="flex justify-between items-start mb-1">
                <span class="text-[10px] font-black truncate select-none pointer-events-none pr-4">${seg.name}</span>
                <button onclick="removeAudioSegment(${index}); event.stopPropagation();" class="text-zinc-500 hover:text-red-500 transition-colors">
                    <i class="fas fa-times text-[10px]"></i>
                </button>
            </div>
            <div class="flex items-center justify-between mt-1">
                <span class="text-[7px] opacity-50 font-mono pointer-events-none">${actualDuration.toFixed(1)}s</span>
                <select class="bg-zinc-800 text-[8px] rounded border border-zinc-700 px-1 outline-none" 
                    onmousedown="event.stopPropagation()" 
                    onchange="handleSpeedChange(${index}, this, event)">
                    <option value="0.8" ${seg.speed == 0.8 ? 'selected' : ''}>0.8x</option>
                    <option value="1.0" ${seg.speed == 1.0 ? 'selected' : ''}>1.0x</option>
                    <option value="1.1" ${seg.speed == 1.1 ? 'selected' : ''}>1.1x</option>
                    <option value="1.2" ${seg.speed == 1.2 ? 'selected' : ''}>1.2x</option>
                    <option value="1.5" ${seg.speed == 1.5 ? 'selected' : ''}>1.5x</option>
                    <option value="2.0" ${seg.speed == 2.0 ? 'selected' : ''}>2.0x</option>
                </select>
            </div>
        `;

        clip.onclick = (e) => {
            if (e.target.tagName === 'SELECT' || e.target.closest('button')) return;
            e.stopPropagation();
            state.active_index = index;
            renderTimeline();
            updateClipSettingsPanel();
        };

        elements.audioTrack.appendChild(clip);
        currentX += width;
    });

    const timelineContainerWidth = elements.timelineScroll.clientWidth;
    const finalWidth = Math.max(timelineContainerWidth, currentX);
    elements.timelineRuler.style.width = `${finalWidth}px`;
    renderRuler(finalWidth);
}

window.removeAudioSegment = (index) => {
    console.log(`🗑️ [Timeline] 删除片段 #${index}`);
    stopAllAudio();
    state.audio_segments.splice(index, 1);
    state.active_index = -1;
    renderTimeline();
    updateTotalDuration();
    updateClipSettingsPanel();
};

window.handleSpeedChange = (index, select, event) => {
    event.stopPropagation();
    state.audio_segments[index].speed = parseFloat(select.value);
    renderTimeline();
    updateTotalDuration();
};

function renderRuler(width) {
    elements.timelineRuler.innerHTML = '';
    
    // 根据缩放级别动态计算刻度间距 (保证刻度文字不重叠)
    let interval = 5; 
    if (state.zoom < 2) interval = 30;
    if (state.zoom < 0.5) interval = 300; // 5分钟
    if (state.zoom < 0.15) interval = 600; // 10分钟
    if (state.zoom > 100) interval = 1; 
    if (state.zoom > 500) interval = 0.5;

    const seconds = Math.ceil(width / state.zoom);
    for (let i = 0; i <= seconds; i += interval) {
        const mark = document.createElement('div');
        mark.className = 'absolute border-l border-zinc-700 h-2 flex items-end pl-1';
        mark.style.left = `${i * state.zoom}px`;
        mark.innerText = formatTime(i);
        elements.timelineRuler.appendChild(mark);
    }
}

// --- 播放控制核心 ---

function seekTo(time) {
    stopAllAudio();
    state.current_time = Math.max(0, Math.min(time, state.total_duration));
    elements.playhead.style.left = `${state.current_time * state.zoom}px`;
    elements.currentTimeDisplay.innerText = formatTime(state.current_time);
    if (state.is_playing) startAudioAtCurrentTime();
}

function togglePlay() {
    if (state.is_playing) {
        state.is_playing = false;
        stopAllAudio();
        cancelAnimationFrame(state.play_raf);
        document.getElementById('play-btn').innerHTML = '<i class="fas fa-play text-xs"></i>';
    } else {
        if (state.current_time >= state.total_duration - 0.1) state.current_time = 0;
        state.is_playing = true;
        document.getElementById('play-btn').innerHTML = '<i class="fas fa-pause text-xs"></i>';
        startAudioAtCurrentTime();
        updatePlaybackLoop();
    }
}

function updatePlaybackLoop() {
    if (!state.is_playing) return;
    syncAudioAndTimeline();
    state.play_raf = requestAnimationFrame(updatePlaybackLoop);
}

function syncAudioAndTimeline() {
    const seg = getSegmentAt(state.current_time);
    
    if (seg && state.current_audio_instance) {
        const localElapsed = (state.current_audio_instance.currentTime - seg.start) / seg.speed;
        const timelineStart = getStartTimeOfSegment(seg);
        state.current_time = timelineStart + localElapsed;
        
        if (state.current_audio_instance.currentTime >= (seg.duration - seg.end_offset - 0.05)) {
            console.log("⏭️ [Player] 片段接力...");
            stopAllAudio();
            state.current_time += 0.06; 
            startAudioAtCurrentTime();
        }
    } else {
        state.current_time += 1/60; 
        startAudioAtCurrentTime();
    }

    elements.playhead.style.left = `${state.current_time * state.zoom}px`;
    elements.currentTimeDisplay.innerText = formatTime(state.current_time);

    if (state.current_time >= state.total_duration) {
        console.log("🏁 [Player] 播放结束");
        togglePlay();
    }
}

function startAudioAtCurrentTime() {
    const seg = getSegmentAt(state.current_time);
    if (seg && seg.audio_instance) {
        const timelineStart = getStartTimeOfSegment(seg);
        const localTimelineTime = state.current_time - timelineStart;
        const localAudioTime = seg.start + (localTimelineTime * seg.speed);
        
        if (state.current_audio_instance !== seg.audio_instance || seg.audio_instance.paused) {
            state.current_audio_instance = seg.audio_instance;
            state.current_audio_instance.playbackRate = seg.speed;
            state.current_audio_instance.currentTime = localAudioTime;
            state.current_audio_instance.play().catch(e => {
                console.warn("Autoplay blocked", e);
            });
        }
    } else {
        state.current_audio_instance = null;
    }
}

function stopAllAudio() {
    state.audio_segments.forEach(s => {
        if(s.audio_instance) s.audio_instance.pause();
    });
    state.current_audio_instance = null;
}

function getSegmentAt(time) {
    let elapsed = 0;
    for (const seg of state.audio_segments) {
        const dur = (seg.duration - seg.start - seg.end_offset) / seg.speed;
        if (time >= elapsed && time < (elapsed + dur)) return seg;
        elapsed += dur;
    }
    return null;
}

function getStartTimeOfSegment(targetSeg) {
    let elapsed = 0;
    for (const seg of state.audio_segments) {
        if (seg.id === targetSeg.id) return elapsed;
        elapsed += (seg.duration - seg.start - seg.end_offset) / seg.speed;
    }
    return 0;
}

// --- 剪辑操作 ---

function splitActiveSegment() {
    if (state.active_index === -1) return;
    const seg = state.audio_segments[state.active_index];
    const timelineStart = getStartTimeOfSegment(seg);
    const localTime = (state.current_time - timelineStart) * seg.speed;
    const absoluteCutPoint = seg.start + localTime;

    if (localTime <= 0.2 || localTime >= (seg.duration - seg.start - seg.end_offset) - 0.2) return;

    stopAllAudio();
    const nextSeg = JSON.parse(JSON.stringify(seg));
    nextSeg.id = Date.now() + Math.random();
    nextSeg.audio_instance = new Audio(seg.audio_instance.src);
    
    seg.end_offset = seg.duration - absoluteCutPoint;
    nextSeg.start = absoluteCutPoint;
    
    state.audio_segments.splice(state.active_index + 1, 0, nextSeg);
    renderTimeline();
    updateTotalDuration();
    updateClipSettingsPanel();
}

function trimActiveStart() {
    if (state.active_index === -1) return;
    const seg = state.audio_segments[state.active_index];
    const timelineStart = getStartTimeOfSegment(seg);
    const localTime = (state.current_time - timelineStart) * seg.speed;
    seg.start += localTime;
    renderTimeline();
    updateTotalDuration();
    updateClipSettingsPanel();
}

function trimActiveEnd() {
    if (state.active_index === -1) return;
    const seg = state.audio_segments[state.active_index];
    const timelineStart = getStartTimeOfSegment(seg);
    const localTime = (state.current_time - timelineStart) * seg.speed;
    seg.end_offset = seg.duration - (seg.start + localTime);
    renderTimeline();
    updateTotalDuration();
    updateClipSettingsPanel();
}

// --- 任务管理 ---

let previousTaskStatuses = {}; 

async function refreshTaskList() {
    try {
        const res = await PublisherService.fetchTasks();
        const tasks = Array.isArray(res) ? res : (res.data || res.tasks || []);
        const newTasks = tasks.slice(0, 10);
        
        let playNotification = false;
        let completedTaskTitle = "";

        newTasks.forEach(task => {
            const taskId = task.id || task._id;
            const oldStatus = previousTaskStatuses[taskId];
            const newStatus = task.status;
            
            if (oldStatus && oldStatus !== 'completed' && newStatus === 'completed') {
                playNotification = true;
                completedTaskTitle = task.title || "未命名任务";
                console.log(`%c🔔 [Task Completion] 任务已完成: ${completedTaskTitle}`, 'color: #10b981; font-weight: bold; font-size: 12px;');
            }
            previousTaskStatuses[taskId] = newStatus;
        });

        if (playNotification) {
            playCompletionSound();
        }

        state.tasks = newTasks;
        renderTaskList();
    } catch (err) { console.warn("Task fetch error", err); }
}

function playCompletionSound() {
    // 浏览器 Web Audio API 播放一个简单的合成音，完全不依赖外部资源
    try {
        const context = new (window.AudioContext || window.webkitAudioContext)();
        const osc = context.createOscillator();
        const gain = context.createGain();
        osc.connect(gain);
        gain.connect(context.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, context.currentTime); // A5 note
        osc.frequency.exponentialRampToValueAtTime(440, context.currentTime + 0.5);
        gain.gain.setValueAtTime(0.1, context.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, context.currentTime + 0.5);
        osc.start();
        osc.stop(context.currentTime + 0.5);
        console.log("🔊 浏览器合成提示音播放成功");
    } catch(e) {
        console.warn("🔊 提示音播放失败:", e);
    }
    showToast("任务合成已完成！", "success");
}

function showToast(message, type = "info") {
    const toast = document.createElement('div');
    toast.className = `fixed bottom-24 left-1/2 -translate-x-1/2 z-[9999] px-6 py-3 rounded-full shadow-2xl text-white text-[11px] font-black uppercase tracking-widest transition-all duration-500 transform translate-y-10 opacity-0`;
    
    if (type === 'success') toast.classList.add('bg-emerald-600');
    else if (type === 'error') toast.classList.add('bg-red-600');
    else toast.classList.add('bg-zinc-800');
    
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.remove('translate-y-10', 'opacity-0');
    }, 10);
    
    setTimeout(() => {
        toast.classList.add('translate-y-10', 'opacity-0');
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

function renderTaskList() {
    if (!elements.taskList) return;
    if (state.tasks.length === 0) {
        elements.taskList.innerHTML = `<div class="text-center p-10 opacity-20 text-[9px] uppercase font-black">No Tasks</div>`;
        return;
    }
    elements.taskList.innerHTML = state.tasks.map(task => {
        const isCompleted = task.status === 'completed';
        const hasReusableConfig = !!task.params?.video || Array.isArray(task.params?.audio_segments);
        return `
            <div class="task-card bg-zinc-900 border border-zinc-800 p-3 rounded-lg ${task.status === 'failed' ? 'border-l-red-500' : (isCompleted ? 'border-l-emerald-500' : 'border-l-blue-500')}">
                <div class="flex justify-between items-start">
                    <div class="truncate pr-2">
                        <p class="text-[10px] font-black text-zinc-200 truncate uppercase">${task.title || 'Untitled'}</p>
                        <p class="text-[8px] text-zinc-500 font-mono mt-0.5">${task.status} | ${task.progress}%</p>
                    </div>
                </div>
                <div class="h-1 bg-zinc-800 rounded-full mt-2 overflow-hidden">
                    <div class="h-full bg-emerald-500 transition-all" style="width: ${task.progress}%"></div>
                </div>
                ${hasReusableConfig ? `<button type="button" data-apply-task-id="${task.id || task._id}" class="mt-2 mr-2 rounded bg-zinc-800 px-2 py-1 text-[8px] font-black uppercase text-zinc-300 hover:bg-zinc-700">套用配置</button>` : ''}
                ${isCompleted ? `<a href="${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(task.result.video_path)}" target="_blank" class="block mt-2 text-[8px] font-black text-emerald-500 uppercase hover:underline">Download Video</a>` : ''}
            </div>
        `;
    }).join('');
}

function applyTaskConfig(taskId) {
    const task = state.tasks.find(item => String(item.id || item._id) === String(taskId));
    const params = task?.params;
    if (!task || !params) {
        showToast('没有找到可套用的历史配置', 'error');
        return;
    }

    const videoParams = params.video || {};
    const exportParams = params.export_params || {};
    const cta = videoParams.cta || null;
    const title = params.bilibili?.title || params.thumbnail?.main_title || task.title || '';
    const ratio = params.aspect_ratio || state.aspect_ratio || '16:9';

    document.getElementById('project-ratio').value = ratio;
    changeProjectRatio(ratio);

    if (params.asr_model) document.getElementById('asr-model').value = params.asr_model;
    if (exportParams.resolution) document.getElementById('export-res').value = exportParams.resolution;
    if (exportParams.bitrate) document.getElementById('export-bitrate').value = exportParams.bitrate;
    if (exportParams.fps) document.getElementById('export-fps').value = String(exportParams.fps);
    if (videoParams.subtitle_size) document.getElementById('subtitle-size').value = videoParams.subtitle_size;
    if (videoParams.subtitle_position?.y_pct !== undefined) {
        setSubtitlePosition(videoParams.subtitle_position.y_pct);
    }
    if (title) elements.videoTitle.value = title;

    const ctaEnabled = document.getElementById('cta-enabled');
    ctaEnabled.checked = !!cta;
    if (cta) {
        if (cta.text !== undefined) document.getElementById('cta-text').value = cta.text;
        if (cta.position) document.getElementById('cta-position').value = cta.position;
        if (cta.style) document.getElementById('cta-style').value = cta.style;
    }

    loadBackgroundFromParams(videoParams);
    loadAudioSegmentsFromParams(params.audio_segments || []);
    updatePreview();
    showToast('已套用历史任务配置，可继续调整后重新提交', 'success');
}

function loadBackgroundFromParams(videoParams) {
    const backgroundPath = videoParams.image_path || videoParams.video_path;
    if (!backgroundPath) {
        state.image_path = null;
        state.video_path = null;
        state.background_type = null;
        updateMediaStatus('历史配置未包含背景素材', '缺少背景');
        return;
    }

    const mediaType = videoParams.media_type || (isVideoPath(backgroundPath) ? 'video' : 'image');
    const viewUrl = `${PublisherService.API_BASE}/resources/view?path=${encodeURIComponent(backgroundPath)}&_t=${Date.now()}`;

    if (mediaType === 'video') {
        state.video_path = backgroundPath;
        state.image_path = null;
        state.background_type = 'video';
        state.previewVideo.onloadeddata = () => {
            try {
                state.previewVideo.currentTime = Math.min(0.2, state.previewVideo.duration || 0);
            } catch (err) {
                updatePreview();
            }
        };
        state.previewVideo.onseeked = () => updatePreview();
        state.previewVideo.onerror = () => showToast('历史视频背景无法加载，可能文件已被移动', 'error');
        state.previewVideo.src = viewUrl;
        updateMediaStatus(getFileName(backgroundPath), '视频背景');
    } else {
        state.image_path = backgroundPath;
        state.video_path = null;
        state.background_type = 'image';
        state.previewImage.onload = () => updatePreview();
        state.previewImage.onerror = () => showToast('历史图片背景无法加载，可能文件已被移动', 'error');
        state.previewImage.src = viewUrl;
        updateMediaStatus(getFileName(backgroundPath), '图片背景');
    }
}

function loadAudioSegmentsFromParams(audioSegments) {
    state.audio_segments.forEach(seg => {
        try { seg.audio_instance?.pause(); } catch (err) {}
    });
    state.audio_segments = [];
    state.active_index = -1;
    updateClipSettingsPanel();
    renderTimeline();
    updateTotalDuration();

    if (!Array.isArray(audioSegments) || audioSegments.length === 0) {
        showToast('历史配置未包含音频轨道', 'error');
        return;
    }

    audioSegments.forEach((seg) => {
        if (!seg?.path) return;
        addAudioSegment(getFileName(seg.path), seg.path, {
            start: seg.start,
            end_offset: seg.end_offset,
            speed: seg.speed,
            subtitle: seg.subtitle
        });
    });
}

function isVideoPath(path) {
    return /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(String(path || '').split('?')[0]);
}

function getFileName(path) {
    const cleanPath = String(path || '').split('?')[0];
    return decodeURIComponent(cleanPath.split('/').pop() || cleanPath || '未命名素材');
}

// --- 其他工具 ---

function formatTime(seconds) {
    return secondsToFormattedTime(seconds, false);
}

function secondsToFormattedTime(seconds, showMs = true) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const ms = (seconds % 1).toFixed(1).substring(2);

    let res = "";
    if (h > 0) res += h.toString().padStart(2, '0') + ":";
    res += m.toString().padStart(2, '0') + ":" + s.toString().padStart(2, '0');
    if (showMs) res += "." + ms;
    return res;
}

function parseFormattedTime(str) {
    if (!str) return 0;
    const parts = str.split(':').map(parseFloat);
    let seconds = 0;
    if (parts.length === 3) {
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2];
    } else if (parts.length === 2) {
        seconds = parts[0] * 60 + parts[1];
    } else if (parts.length === 1) {
        seconds = parts[0];
    }
    return isNaN(seconds) ? 0 : seconds;
}

function updateTotalDuration() {
    state.total_duration = state.audio_segments.reduce((acc, seg) => {
        return acc + (seg.duration - seg.start - seg.end_offset) / seg.speed;
    }, 0);
    elements.totalTimeDisplay.innerText = formatTime(state.total_duration);
}

window.changeProjectRatio = (value) => {
    state.aspect_ratio = value;
    const canvas = elements.previewCanvas;
    if (value === '16:9') { canvas.width = 1920; canvas.height = 1080; } 
    else { canvas.width = 1080; canvas.height = 1920; }
    setSubtitlePosition(getDefaultSubtitleY(value));
    updatePreview();
};

window.toggleRightPanel = () => {
    const panel = document.getElementById('right-panel');
    if (panel.classList.contains('w-80')) {
        panel.classList.replace('w-80', 'w-0');
        panel.classList.remove('border-l');
    } else {
        panel.classList.replace('w-0', 'w-80');
        panel.classList.add('border-l');
    }
};

async function handleSubmit() {
    if (state.audio_segments.length === 0) return alert("请添加音频");
    const backgroundPath = state.video_path || state.image_path;
    if (!backgroundPath) return alert("请添加背景图片或背景视频");
    elements.submitBtn.disabled = true;
    elements.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 提交中...';
    const finalTitle = elements.videoTitle.value.trim() || state.audio_segments[0].name.split('.')[0] + " (Pro Edit)";
    const payload = {
        audio_segments: state.audio_segments.map(s => ({ path: s.path, speed: s.speed, start: s.start, end_offset: s.end_offset, subtitle: s.subtitle })),
        aspect_ratio: state.aspect_ratio,
        asr_model: document.getElementById('asr-model').value,
        export_params: {
            resolution: document.getElementById('export-res').value,
            bitrate: document.getElementById('export-bitrate').value,
            fps: parseInt(document.getElementById('export-fps').value)
        },
        bilibili: { title: finalTitle, desc: "高级剪辑版生成" },
        thumbnail: { main_title: finalTitle },
        video: {
            enabled: true,
            image_path: backgroundPath,
            media_type: state.background_type || 'image',
            sub_burn_enabled: true,
            cta: document.getElementById('cta-enabled').checked ? {
                text: document.getElementById('cta-text').value,
                position: document.getElementById('cta-position').value,
                style: document.getElementById('cta-style').value
            } : null,
            subtitle_position: { y_pct: state.subtitle_y_pct },
            subtitle_size: document.getElementById('subtitle-size').value
        }
    };
    console.log("🚀 [Task Submit] Payload:", payload);
    try {
        const res = await PublisherService.submitTask(payload);
        if (res.success) { refreshTaskList(); elements.videoTitle.value = ""; }
        else alert("错误: " + res.error);
    } catch (err) { alert("提交失败"); }
    finally { elements.submitBtn.disabled = false; elements.submitBtn.innerText = "提交合成任务"; }
}

function updatePreview() {
    const canvas = elements.previewCanvas;
    const ctx = canvas.getContext('2d');
    const overlay = document.getElementById('preview-overlay');
    const img = state.previewImage;
    const video = state.previewVideo;

    const hasImage = state.background_type === 'image' && img.src && img.complete && img.naturalWidth > 0;
    const hasVideo = state.background_type === 'video' && video.src && video.readyState >= 2 && video.videoWidth > 0;

    if (!hasImage && !hasVideo) {
        overlay?.classList.remove('opacity-0', 'pointer-events-none');
        return;
    }

    overlay?.classList.add('opacity-0', 'pointer-events-none');

    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (hasVideo) {
        drawImageCover(ctx, video, 0, 0, w, h);
    } else {
        drawImageCover(ctx, img, 0, 0, w, h);
    }

    if (document.getElementById('cta-enabled').checked) {
        const text = document.getElementById('cta-text').value || "";
        const position = document.getElementById('cta-position').value;
        const style = document.getElementById('cta-style').value;

        const styles = {
            police_yellow: { bg: 'rgba(255, 204, 0, 0.95)', text: '#000000', action: '#000000', border: 'rgba(0,0,0,0.2)' },
            premium_dark: { bg: 'rgba(25, 28, 36, 0.85)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.15)' },
            nomura_red: { bg: 'rgba(128, 20, 30, 0.88)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.2)' },
            institutional_blue: { bg: 'rgba(30, 64, 175, 0.85)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.2)' },
            aurora_cyan: { bg: 'rgba(6, 182, 212, 0.9)', text: '#ffffff', action: '#ffffff', border: 'rgba(255,255,255,0.2)' }
        };

        const s = styles[style] || styles.police_yellow;
        const lines = text.split('\n').filter(l => l.trim());

        ctx.save();
        if (position === 'bottom_bar') {
            renderFullBar(ctx, w, h, lines, position, s);
        } else {
            renderFloatingCard(ctx, w, h, lines, position, s);
        }
        ctx.restore();
    }
}

function drawImageCover(ctx, img, x, y, w, h) {
    const sourceW = img.naturalWidth || img.videoWidth;
    const sourceH = img.naturalHeight || img.videoHeight;
    if (!sourceW || !sourceH) return;
    const r = sourceW / sourceH, cr = w / h;
    let rw, rh, rx, ry;
    if (r > cr) {
        rh = h; rw = h * r;
        rx = x + (w - rw) / 2; ry = y;
    } else {
        rw = w; rh = w / r;
        rx = x; ry = y;
    }
    ctx.drawImage(img, rx, ry, rw, rh);
}

const FONT_STACK = '"PingFang SC", "PingFang TC", "Microsoft YaHei", sans-serif';

function renderFullBar(ctx, w, h, lines, position, s) {
    const scaleFactor = h / 1080;
    const barHeight = Math.max(10, 140 * scaleFactor); 
    const barY = h - barHeight;
    
    ctx.fillStyle = s.bg; ctx.fillRect(0, barY, w, barHeight);
    ctx.strokeStyle = s.border; ctx.lineWidth = Math.max(1, 1 * scaleFactor); ctx.beginPath();
    ctx.moveTo(0, barY); ctx.lineTo(w, barY);
    ctx.stroke();

    const lineSpacing = 45 * scaleFactor;
    const totalTextH = lines.length * lineSpacing;
    let currY = barY + (barHeight - totalTextH) / 2;

    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    lines.forEach((line, i) => {
        let fs = 32 * scaleFactor;
        ctx.font = `500 ${fs}px ${FONT_STACK}`; 
        ctx.fillStyle = (i === lines.length - 1 && lines.length > 1) ? s.action : s.text;
        let txt = line;
        if (i === lines.length - 1 && lines.length > 1 && !txt.startsWith('↓')) txt = "↓ " + txt;
        ctx.fillText(txt, w / 2, currY); 
        currY += lineSpacing;
    });
}

function renderFloatingCard(ctx, w, h, lines, position, s) {
    const scaleFactor = h / 1080;
    const boxW = Math.max(10, 600 * (w / 1920));
    const boxH = Math.max(10, 280 * scaleFactor);
    
    let bx = w - boxW - (120 * (w / 1920));
    let by = (position === 'right_center') ? (h - boxH) / 2 : (h - boxH - (220 * scaleFactor));

    ctx.shadowColor = 'rgba(0,0,0,0.3)'; ctx.shadowBlur = 40; ctx.shadowOffsetY = 15;
    ctx.fillStyle = s.bg; ctx.beginPath(); ctx.roundRect(bx, by, boxW, boxH, 20 * scaleFactor); ctx.fill();
    ctx.shadowColor = 'transparent';
    
    // 文字渲染
    const lineSpacing = 45 * scaleFactor;
    const totalTextH = lines.length * lineSpacing;
    let currY = by + (boxH - totalTextH) / 2;
    
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    lines.forEach((line, i) => {
        let fs = (i === 0) ? 38 * scaleFactor : 38 * scaleFactor;
        ctx.font = `500 ${fs}px ${FONT_STACK}`; 
        ctx.fillStyle = (i === lines.length - 1 && lines.length > 1) ? s.action : s.text;
        let txt = line;
        if (i === lines.length - 1 && lines.length > 1 && !txt.startsWith('↓')) txt = "↓ " + txt;
        ctx.fillText(txt, bx + boxW / 2, currY); 
        currY += lineSpacing;
    });
}
