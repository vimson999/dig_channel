const detailState = {
    batchId: '',
    groupId: '',
    batch: null,
    error: null,
};

const detailEls = {
    subtitle: document.getElementById('detail-subtitle'),
    meta: document.getElementById('detail-meta'),
    status: document.getElementById('detail-status'),
    summary: document.getElementById('detail-summary'),
    failurePanel: document.getElementById('detail-failure-panel'),
    failureSubtitle: document.getElementById('detail-failure-subtitle'),
    failureList: document.getElementById('detail-failure-list'),
    topicsSubtitle: document.getElementById('detail-topics-subtitle'),
    topics: document.getElementById('detail-topics'),
    refreshBtn: document.getElementById('detail-refresh-btn'),
};

document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    detailState.batchId = (params.get('batch_id') || '').trim();
    detailState.groupId = (params.get('group_id') || '').trim();

    detailEls.refreshBtn?.addEventListener('click', () => loadBatchDetail());

    if (!detailState.batchId) {
        renderDetailError('缺少 batch_id，无法读取批次明细。');
        return;
    }

    loadBatchDetail();
});

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

async function apiPost(endpoint, payload) {
    const response = await fetch(`${apiBaseUrl()}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    let data = {};
    try {
        data = await response.json();
    } catch (error) {
        data = {};
    }
    if (!response.ok || !data.success) {
        const message = response.status === 405
            ? '后端接口未命中，请确认 5001 后端服务已重启并加载最新代码。'
            : (data?.error?.message || data?.message || `HTTP ${response.status}`);
        throw new Error(message);
    }
    return data.data;
}

async function loadBatchDetail() {
    setDetailLoading(true);
    try {
        const data = await apiPost('/zsxq/system-sync-test/batch-detail', {
            batch_id: detailState.batchId,
            group_id: detailState.groupId || null,
        });
        detailState.batch = data?.batch || null;
        detailState.error = null;
    } catch (error) {
        detailState.batch = null;
        detailState.error = error.message || '读取批次明细失败';
    } finally {
        setDetailLoading(false);
        renderBatchDetail();
    }
}

function renderBatchDetail() {
    if (detailState.error) {
        renderDetailError(detailState.error);
        return;
    }

    const batch = detailState.batch;
    if (!batch) {
        renderDetailError('未找到批次明细。');
        return;
    }

    const summary = batch.summary || {};
    const status = batchStatusMeta(batch.status);
    const topics = batch.topics || [];
    const title = batch.group_name || batch.group_id || '未命名星球';
    const roundText = batch.round && batch.max_rounds
        ? `第 ${batch.round}/${batch.max_rounds} 轮`
        : batch.round
            ? `第 ${batch.round} 轮`
            : '历史批次';
    const finishedAt = formatZsxqTime(batch.completed_at || batch.created_at);

    detailEls.subtitle.textContent = `${title} · ${roundText} · ${finishedAt}`;
    detailEls.meta.innerHTML = `
        <span class="font-mono">${escapeHtml(batch.batch_id || detailState.batchId)}</span>
        ${batch.trigger ? ` · 触发：${escapeHtml(batch.trigger)}` : ''}
        ${batch.max_download_files ? ` · 每条最多 ${escapeHtml(String(batch.max_download_files))} 个附件` : ''}
    `;
    detailEls.status.textContent = status.label;
    detailEls.status.dataset.tone = status.tone;
    detailEls.summary.innerHTML = [
        renderBatchMetric('新增', summary.topics_new || 0, 'blue'),
        renderBatchMetric('更新', summary.topics_updated || 0, 'violet'),
        renderBatchMetric('下载', summary.attachments_downloaded || 0, 'emerald'),
        renderBatchMetric('失败', summary.attachments_failed || 0, 'red'),
        renderBatchMetric('抓取', summary.topics_fetched || 0, 'slate'),
        renderBatchMetric('跳过', summary.attachments_skipped || 0, 'slate'),
    ].join('');

    renderFailureReasons(batch);

    detailEls.topicsSubtitle.textContent = batch.topics_truncated
        ? `展示前 ${topics.length} 条内容，历史明细较多时后端会截断。`
        : `共 ${topics.length} 条内容。`;
    delete detailEls.topics.dataset.tone;
    detailEls.topics.className = topics.length ? 'zsxq-topic-detail-list' : 'zsxq-detail-message';
    detailEls.topics.innerHTML = topics.length
        ? renderSystemSyncTopicDetails(topics)
        : '这个批次没有可还原的内容明细，可能是任务未真正入库，或历史记录缺少逐条关联。';
}

function renderDetailError(message) {
    detailEls.subtitle.textContent = '读取批次明细失败。';
    detailEls.meta.textContent = detailState.batchId ? `批次：${detailState.batchId}` : '未提供批次 ID。';
    detailEls.status.textContent = '失败';
    detailEls.status.dataset.tone = 'danger';
    detailEls.summary.innerHTML = '';
    hideFailureReasons();
    detailEls.topicsSubtitle.textContent = '请返回系统同步页重新打开明细，或手动刷新。';
    detailEls.topics.className = 'zsxq-detail-message';
    detailEls.topics.dataset.tone = 'danger';
    detailEls.topics.textContent = message;
}

function setDetailLoading(isLoading) {
    if (!detailEls.refreshBtn) return;
    detailEls.refreshBtn.disabled = isLoading;
    detailEls.refreshBtn.classList.toggle('opacity-60', isLoading);
    detailEls.refreshBtn.innerHTML = isLoading
        ? '<i class="fas fa-circle-notch fa-spin"></i><span>读取中</span>'
        : '<i class="fas fa-rotate"></i><span>手动刷新</span>';
}

function renderBatchMetric(label, value, tone = 'slate') {
    return `
        <div class="zsxq-batch-metric" data-tone="${escapeHtml(tone)}">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(String(value || 0))}</strong>
        </div>
    `;
}

function renderFailureReasons(batch) {
    const reasons = collectFailureReasons(batch);
    const shouldShow = reasons.length || batch.status === 'failed';
    if (!shouldShow) {
        hideFailureReasons();
        return;
    }

    detailEls.failurePanel.hidden = false;
    detailEls.failureSubtitle.textContent = reasons.length
        ? `共 ${reasons.length} 条错误线索，优先看第一条。`
        : '批次已标记失败，但当前 run 未写入具体错误文本。';
    detailEls.failureList.innerHTML = reasons.length
        ? reasons.map(renderFailureReason).join('')
        : `
            <div class="zsxq-detail-failure-item">
                <div class="zsxq-detail-failure-item__head">
                    <span>Run</span>
                    <strong>未记录具体失败文本</strong>
                </div>
                <div class="zsxq-detail-failure-item__message">
                    可以返回系统同步页查看“最近日志”，或重启后端后刷新本页读取更新后的 run 明细。
                </div>
            </div>
        `;
}

function hideFailureReasons() {
    if (!detailEls.failurePanel) return;
    detailEls.failurePanel.hidden = true;
    detailEls.failureList.innerHTML = '';
}

function collectFailureReasons(batch) {
    const reasons = [];
    const seen = new Set();

    const pushReason = (reason) => {
        const message = String(reason.message || '').trim();
        if (!message) return;
        const key = `${reason.type || ''}|${reason.title || ''}|${message}`;
        if (seen.has(key)) return;
        seen.add(key);
        reasons.push({
            type: reason.type || '错误',
            title: reason.title || '失败原因',
            meta: reason.meta || '',
            message,
        });
    };

    (batch.runs || []).forEach((run) => {
        const runTitle = run.tab_label || run.group_name || run.id || '同步任务';
        if (run.error_message) {
            pushReason({
                type: 'Run',
                title: `${syncStatusLabel(run.status)} · ${runTitle}`,
                meta: run.id ? `run_id: ${run.id}` : '',
                message: run.error_message,
            });
        } else if (run.status === 'failed') {
            pushReason({
                type: 'Run',
                title: `失败 · ${runTitle}`,
                meta: run.id ? `run_id: ${run.id}` : '',
                message: '这个 run 被标记为失败，但没有写入 error_message。',
            });
        }

        (run.failed_attachments || []).forEach((item) => {
            pushReason({
                type: '附件',
                title: item.name || item.file_id || item.attachment_key || '附件下载失败',
                meta: [run.id ? `run_id: ${run.id}` : '', item.topic_id ? `topic_id: ${item.topic_id}` : '']
                    .filter(Boolean)
                    .join(' · '),
                message: item.error_message,
            });
        });
    });

    (batch.topics || []).forEach((topic) => {
        (topic.attachments || []).forEach((item) => {
            if (item.download_status !== 'failed' && !item.error_message) return;
            pushReason({
                type: '附件',
                title: item.name || item.file_id || item.attachment_key || '附件处理异常',
                meta: [topic.title || topic.topic_id || '', item.topic_id ? `topic_id: ${item.topic_id}` : '']
                    .filter(Boolean)
                    .join(' · '),
                message: item.error_message || '附件被标记为失败，但没有写入具体错误文本。',
            });
        });
    });

    return reasons.slice(0, 30);
}

function renderFailureReason(reason) {
    return `
        <article class="zsxq-detail-failure-item">
            <div class="zsxq-detail-failure-item__head">
                <span>${escapeHtml(reason.type)}</span>
                <strong>${escapeHtml(reason.title)}</strong>
            </div>
            ${reason.meta ? `<div class="zsxq-detail-failure-item__meta">${escapeHtml(reason.meta)}</div>` : ''}
            <div class="zsxq-detail-failure-item__message">${escapeHtml(reason.message)}</div>
        </article>
    `;
}

function renderSystemSyncTopicDetails(topics) {
    return topics.map(topic => {
        const attachments = topic.attachments || [];
        return `
            <section class="zsxq-topic-detail">
                <div class="zsxq-topic-detail__head">
                    <div class="min-w-0">
                        <div class="zsxq-topic-detail__title">${escapeHtml(topic.title || topic.content || '无标题内容')}</div>
                        <div class="zsxq-topic-detail__meta">
                            <span>${escapeHtml(topic.tab_label || topic.tab_id || '-')}</span>
                            <span>${escapeHtml(topicStatusLabel(topic.status))}</span>
                            <span>${escapeHtml(formatZsxqTime(topic.create_time || ''))}</span>
                            ${topic.author_name ? `<span>${escapeHtml(topic.author_name)}</span>` : ''}
                            ${topic.topic_id ? `<span class="font-mono">${escapeHtml(topic.topic_id)}</span>` : ''}
                        </div>
                    </div>
                    <span class="zsxq-topic-detail__count">${escapeHtml(String(attachments.length))} 附件</span>
                </div>
                ${topic.content ? `<div class="zsxq-topic-detail__content">${escapeHtml(topic.content)}</div>` : ''}
                ${topic.raw_json_path ? `<div class="zsxq-topic-detail__path">JSON：${escapeHtml(topic.raw_json_path)}</div>` : ''}
                ${attachments.length ? renderSystemSyncAttachmentDetails(attachments) : '<div class="mt-2 text-xs text-slate-400">无附件记录。</div>'}
            </section>
        `;
    }).join('');
}

function renderSystemSyncAttachmentDetails(attachments) {
    return `
        <div class="zsxq-attachment-detail-list">
            ${attachments.map(item => {
                const status = attachmentStatusMeta(item);
                const publishStatus = publishStatusMeta(item);
                return `
                    <div class="zsxq-attachment-detail">
                        <div class="min-w-0">
                            <div class="zsxq-attachment-detail__name">
                                <span>${escapeHtml(String(item.file_type || item.attachment_type || 'file').toUpperCase())}</span>
                                ${escapeHtml(item.name || item.file_id || item.attachment_key || '附件')}
                            </div>
                            ${item.local_path ? `<div class="zsxq-attachment-detail__path">${escapeHtml(item.local_path)}</div>` : ''}
                            ${item.error_message ? `<div class="zsxq-attachment-detail__error">${escapeHtml(item.error_message)}</div>` : ''}
                        </div>
                        <div class="flex shrink-0 flex-wrap justify-end gap-2">
                            <span class="zsxq-attachment-status" data-tone="${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span>
                            ${publishStatus.label ? `<span class="zsxq-attachment-status" data-tone="${escapeHtml(publishStatus.tone)}" title="${escapeHtml(publishStatus.title)}">${escapeHtml(publishStatus.label)}</span>` : ''}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function formatZsxqTime(value) {
    if (!value) return '尚未触发';
    let normalized = String(value).trim().replace(/([+-]\d{2})(\d{2})$/, '$1:$2');
    if (/^\d{4}-\d{2}-\d{2}T/.test(normalized) && !/(Z|[+-]\d{2}:\d{2})$/.test(normalized)) {
        normalized = `${normalized}Z`;
    }
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return String(value);

    const parts = new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
    }).formatToParts(date).reduce((acc, part) => {
        acc[part.type] = part.value;
        return acc;
    }, {});
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function syncStatusLabel(status) {
    const map = {
        queued: '排队中',
        completed: '成功',
        failed: '失败',
        running: '进行中',
    };
    return map[status] || status || '未知';
}

function batchStatusMeta(status) {
    if (status === 'completed') {
        return { label: '成功', tone: 'success' };
    }
    if (status === 'failed') {
        return { label: '失败', tone: 'danger' };
    }
    if (status === 'queued' || status === 'running') {
        return { label: syncStatusLabel(status), tone: 'info' };
    }
    return { label: syncStatusLabel(status), tone: 'muted' };
}

function topicStatusLabel(status) {
    const map = {
        new: '新增',
        touched: '本轮处理',
    };
    return map[status] || status || '本轮处理';
}

function attachmentStatusMeta(item) {
    const status = item?.download_status || 'pending';
    if (status === 'downloaded') {
        const localPath = String(item.local_path || '');
        return {
            label: localPath.startsWith('browser-download:') ? '下载过' : '已归档',
            tone: 'success',
        };
    }
    if (status === 'failed') {
        return { label: '失败', tone: 'danger' };
    }
    if (status === 'skipped') {
        return { label: '已跳过', tone: 'muted' };
    }
    return { label: '待处理', tone: 'warning' };
}

function publishStatusMeta(item) {
    const status = String(item?.publish_status || '').toLowerCase();
    const updatedAt = item?.publish_updated_at ? ` · ${formatZsxqTime(item.publish_updated_at)}` : '';
    const taskText = item?.publish_task_id ? `发布任务 ${item.publish_task_id}${updatedAt}` : `发布状态${updatedAt}`;
    if (status === 'published') {
        return { label: '已发布', tone: 'success', title: `${taskText}，已确认可见。` };
    }
    if (status === 'published_unverified') {
        return { label: '发布待核验', tone: 'warning', title: `${taskText}，缺少发布主题 ID，需要人工确认。` };
    }
    if (status === 'submitted') {
        return { label: '已提交', tone: 'info', title: `${taskText}，已提交发布。` };
    }
    if (status === 'publishing') {
        return { label: '发布中', tone: 'warning', title: `${taskText}，发布任务仍在执行。` };
    }
    if (status === 'publish_failed') {
        return { label: '发布失败', tone: 'danger', title: `${taskText}，上次发布失败。` };
    }
    if (status === 'publish_skipped') {
        return { label: '发布跳过', tone: 'warning', title: `${taskText}，发布流程跳过了这个附件。` };
    }
    return { label: '', tone: 'muted', title: '' };
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
