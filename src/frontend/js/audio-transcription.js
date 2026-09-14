/* 音频转文字：有界单飞轮询、上传真实进度与按需结果读取。 */
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const base = String(window.API_BASE_URL || '/api/v1').replace(/\/api\/v1\/?$/, '') + '/api/audio-transcriptions';
    const labels = {queued: '排队中', validating: '检查音频', transcribing: '转写中', writing_outputs: '生成结果', completed: '已完成', failed: '失败', enqueue_failed: '入队失败'};
    const state = {file: null, id: new URL(location.href).searchParams.get('job'), job: null, page: 1, timer: null, polling: false, uploading: false, kind: 'text', cache: {}, limit: 2 * 1024 ** 3, generation: 0};
    const notice = message => { el('notice').textContent = message; };
    async function api(path, options = {}) {
        const response = await fetch(base + path, {credentials: 'include', ...options});
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.success === false) throw new Error(body.message || `请求失败 (${response.status})`);
        return body.data;
    }
    function choose(files) {
        state.file = null;
        el('file-info').textContent = '尚未选择音频';
        if (files.length !== 1) { notice('请每次选择一个音频文件'); return; }
        const file = files[0];
        if (!/\.(mp3|m4a|wav|aac|flac|ogg|opus)$/i.test(file.name) || file.size <= 0 || file.size > state.limit) {
            notice('请选择支持的非空音频，且大小不超过上传上限'); return;
        }
        state.file = file;
        el('file-info').textContent = `${file.name} · ${(file.size / 1024 ** 2).toFixed(1)} MB`;
        notice('');
    }
    async function history() {
        const page = state.page;
        const data = await api(`/jobs?page=${page}`);
        if (page !== state.page) return;
        el('history').replaceChildren();
        for (const job of data.items) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'block w-full text-left py-3 hover:text-indigo-700 break-all';
            const size = Number.isFinite(job.source_bytes) ? `${(job.source_bytes / 1024 ** 2).toFixed(1)} MB` : '原文件不可用';
            button.textContent = `${job.task_title} · ${size} · ${labels[job.current_step] || job.status} · ${job.progress || 0}% · ${new Date(job.created_at).toLocaleString()}`;
            button.addEventListener('click', () => select(job.id));
            el('history').append(button);
        }
        if (!data.items.length) el('history').textContent = '还没有转写任务';
        el('previous').disabled = page <= 1;
        el('next').disabled = !data.has_more;
        el('page-label').textContent = String(page);
    }
    function schedule() {
        clearTimeout(state.timer);
        if (!document.hidden && state.id && (!state.job || ['pending', 'processing'].includes(state.job.status))) {
            state.timer = setTimeout(poll, 5000);
        }
    }
    async function select(id) {
        state.id = id; state.job = null; state.cache = {}; state.generation++;
        el('results').hidden = true; el('retry').hidden = true;
        const url = new URL(location.href); url.searchParams.set('job', id); window.history.replaceState(null, '', url);
        await poll();
    }
    async function poll() {
        if (state.polling || document.hidden || !state.id) { schedule(); return; }
        state.polling = true;
        const generation = state.generation;
        try {
            const job = await api(`/jobs/${encodeURIComponent(state.id)}?poll=1`);
            if (generation !== state.generation) return;
            state.job = job;
            el('job-title').textContent = job.task_title;
            el('job-state').textContent = job.error_message || `${labels[job.current_step] || job.current_step} · ${job.progress || 0}%`;
            el('retry').hidden = job.status !== 'failed';
            el('results').hidden = job.status !== 'completed';
            if (job.status === 'completed') await showResult(state.kind);
            if (['completed', 'failed'].includes(job.status)) await history();
        } catch (error) { notice(error.message); }
        finally { state.polling = false; schedule(); }
    }
    async function showResult(kind) {
        state.kind = kind;
        const id = state.id;
        const url = `${base}/jobs/${encodeURIComponent(id)}/${kind}`;
        el('output').value = '';
        el('download').removeAttribute('href');
        try {
            if (!Object.hasOwn(state.cache, kind)) {
                const response = await fetch(url, {credentials: 'include'});
                if (!response.ok) throw new Error('结果读取失败，请重新打开任务');
                const text = await response.text();
                if (id !== state.id) return;
                state.cache[kind] = text;
            }
            if (kind !== state.kind || id !== state.id) return;
            el('output').value = state.cache[kind];
            el('download').href = url + '?download=1';
            for (const tab of ['text', 'srt']) el(tab + '-tab').setAttribute('aria-pressed', String(tab === kind));
        } catch (error) { notice(error.message); }
    }
    el('audio-file').addEventListener('change', event => choose(event.target.files));
    el('drop-zone').addEventListener('dragover', event => event.preventDefault());
    el('drop-zone').addEventListener('drop', event => {event.preventDefault(); if (!state.uploading) choose(event.dataTransfer.files);});
    el('upload-form').addEventListener('submit', event => {
        event.preventDefault();
        if (state.uploading) return;
        if (!state.file) {notice('请先选择音频'); return;}
        state.uploading = true; el('submit').disabled = true; el('audio-file').disabled = true;
        const progress = el('upload-progress'); progress.classList.remove('hidden'); progress.value = 0;
        const data = new FormData(); data.append('audio', state.file); data.append('prompt', el('prompt').value);
        const xhr = new XMLHttpRequest(); xhr.open('POST', base + '/jobs'); xhr.withCredentials = true;
        xhr.upload.onprogress = event => {if (event.lengthComputable) {progress.value = event.loaded / event.total * 100; notice(`上传中 ${Math.round(progress.value)}%`);}};
        xhr.onload = async () => {
            try {
                const body = JSON.parse(xhr.responseText);
                if (body.data?.id) await select(body.data.id);
                notice(body.message || (xhr.status < 300 ? '已提交' : '上传失败'));
                await history();
            } catch (_) {notice('上传失败，请刷新历史确认是否已创建任务');}
        };
        xhr.onerror = () => notice('上传连接中断，请检查网络后重试');
        xhr.onloadend = () => {state.uploading = false; el('submit').disabled = false; el('audio-file').disabled = false; progress.classList.add('hidden');};
        xhr.send(data);
    });
    el('retry').addEventListener('click', async () => {
        el('retry').disabled = true;
        try {await api(`/jobs/${state.id}/retry`, {method: 'POST'}); await select(state.id);}
        catch (error) {notice(error.message);}
        finally {el('retry').disabled = false;}
    });
    for (const kind of ['text', 'srt']) el(kind + '-tab').addEventListener('click', () => showResult(kind));
    el('copy').addEventListener('click', async () => {
        try {await navigator.clipboard.writeText(el('output').value); notice('已复制');}
        catch (_) {el('output').select(); notice('请按 Ctrl+C 或 ⌘C 复制选中文字');}
    });
    el('refresh').addEventListener('click', () => history().catch(error => notice(error.message)));
    for (const [id, delta] of [['previous', -1], ['next', 1]]) el(id).addEventListener('click', () => {state.page += delta; history().catch(error => notice(error.message));});
    document.addEventListener('visibilitychange', () => {clearTimeout(state.timer); if (!document.hidden) poll();});
    window.addEventListener('pagehide', () => clearTimeout(state.timer));
    api('/status').then(data => {state.limit = data.max_upload_bytes; el('upload-limit').textContent = `最大 ${(state.limit / 1024 ** 3).toFixed(1)} GB · MP3 / M4A / WAV / AAC / FLAC / OGG / OPUS`;}).catch(error => notice(error.message));
    history().catch(error => notice(error.message));
    if (state.id) select(state.id);
})();
