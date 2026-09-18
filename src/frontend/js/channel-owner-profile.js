class ChannelOwnerProfile {
    constructor({ apiClient = api, config = CONFIG, documentRef = document, locationRef = window.location, chartFactory = window.echarts } = {}) {
        this.api = apiClient;
        this.config = config;
        this.document = documentRef;
        this.location = locationRef;
        this.chartFactory = chartFactory;
        this.metric = 'revenue';
        this.period = 'daily';
        this.chart = null;
        this.data = null;
        this.channelId = Number(new URLSearchParams(this.location.search || '').get('channel_id'));
    }

    async init() {
        if (!Number.isInteger(this.channelId) || this.channelId < 1) {
            this.renderError('请从排行榜选择一个频道查看档案');
            return;
        }
        this.document.querySelectorAll('[data-owner-metric]').forEach((button) => {
            button.addEventListener('click', () => {
                this.metric = button.dataset.ownerMetric;
                this.updateMetricButtons();
                this.renderChart();
            });
        });
        this.document.querySelectorAll('[data-owner-period]').forEach((button) => {
            button.addEventListener('click', () => {
                this.period = button.dataset.ownerPeriod;
                this.updatePeriodButtons();
                this.renderPeriodData();
            });
        });
        window.addEventListener('resize', () => this.chart?.resize());
        try {
            const response = await this.api.get(`${this.config.ENDPOINTS.CHANNEL_OWNER_PROFILE}/${this.channelId}/profile`);
            if (!response?.success || !response.data) throw new Error(response?.message || '档案数据不可用');
            this.data = response.data;
            this.render();
        } catch (error) {
            this.renderError(error?.message || '频道主档案加载失败');
        }
    }

    render() {
        const { channel, latest_metrics: metrics, recent_performance: performance, rankings, recent_videos: videos, top_video: topVideo } = this.data;
        this.document.getElementById('channel-owner-loading').hidden = true;
        this.document.getElementById('channel-owner-content').hidden = false;
        this.document.getElementById('channel-owner-name').textContent = channel.name;
        this.document.getElementById('channel-owner-meta').textContent = [channel.category || '未分类', `频道 ID：${channel.youtube_id}`, `数据更新：${metrics.date}`].join(' · ');
        const external = this.document.getElementById('channel-owner-external-link');
        external.href = channel.url;
        const videosLink = this.document.getElementById('channel-owner-videos-link');
        videosLink.href = `videos.html?channel_id=${encodeURIComponent(channel.id)}&channel_name=${encodeURIComponent(channel.name)}`;
        this.document.getElementById('channel-owner-metrics').innerHTML = [
            this.metricCard('订阅总量', this.formatNumber(metrics.subscribers), 'fas fa-users', 'text-violet-600'),
            this.metricCard('播放总量', this.formatNumber(metrics.total_views), 'fas fa-play', 'text-blue-600'),
            this.metricCard('近 30 天收入', this.formatCurrency(performance.revenue), 'fas fa-dollar-sign', 'text-emerald-600'),
            this.metricCard('近 30 天订阅增长', this.formatSigned(performance.subscriber_change), 'fas fa-arrow-trend-up', 'text-amber-600'),
        ].join('');
        this.document.getElementById('channel-owner-rank-date').textContent = `统计至 ${rankings.as_of} · 仅比较本账户频道`;
        this.document.getElementById('channel-owner-rankings').innerHTML = [
            ['日收入', rankings.daily_revenue_rank], ['日增订阅', rankings.daily_subscriber_rank],
            ['月收入', rankings.monthly_revenue_rank], ['月增订阅', rankings.monthly_subscriber_rank],
        ].map(([label, rank]) => `<div class="rounded-lg bg-slate-50 px-3 py-3"><p class="text-xs text-slate-500">${label}</p><p class="mt-1 text-xl font-bold text-slate-900">#${this.formatNumber(rank)}</p></div>`).join('');
        this.document.getElementById('channel-owner-top-video').innerHTML = topVideo ? this.videoRow(topVideo, true) : this.empty('暂无视频数据');
        this.document.getElementById('channel-owner-video-count').textContent = `${videos.length} 条近期视频`;
        this.document.getElementById('channel-owner-videos').innerHTML = videos.length ? videos.map((video) => this.videoRow(video)).join('') : this.empty('暂无视频数据');
        this.updateMetricButtons();
        this.updatePeriodButtons();
        this.renderPeriodData();
    }

    periodRows() {
        const rows = this.data?.periods?.[this.period];
        return Array.isArray(rows) ? rows : [];
    }

    renderPeriodData() {
        const rows = this.periodRows();
        const labels = { daily: '日', monthly: '月', yearly: '年' };
        const periodLabel = labels[this.period] || '日';
        const title = this.document.getElementById('channel-owner-trend-title');
        if (title) title.textContent = `${periodLabel}趋势`;
        const summary = this.document.getElementById('channel-owner-period-summary');
        if (summary) summary.textContent = `最近 ${rows.length} 个${periodLabel}周期`;
        const table = this.document.getElementById('channel-owner-period-table');
        if (table) {
            table.innerHTML = rows.length ? rows.map((row) => `<tr class="hover:bg-slate-50"><td class="px-3 py-2.5 font-medium text-slate-800">${ChannelOwnerProfile.escapeHtml(row.period)}</td><td class="px-3 py-2.5 text-right tabular-nums">${this.formatNumber(row.subscribers)}</td><td class="px-3 py-2.5 text-right tabular-nums">${this.formatNumber(row.total_views)}</td><td class="px-3 py-2.5 text-right tabular-nums">${this.formatNumber(row.video_count)}</td><td class="px-3 py-2.5 text-right font-semibold tabular-nums ${Number(row.subscriber_change) >= 0 ? 'text-violet-600' : 'text-rose-600'}">${this.formatSigned(row.subscriber_change)}</td><td class="px-3 py-2.5 text-right tabular-nums">${this.formatSigned(row.view_change)}</td><td class="px-3 py-2.5 text-right font-semibold tabular-nums text-emerald-600">${this.formatCurrency(row.estimated_revenue)}</td></tr>`).join('') : `<tr><td colspan="7" class="px-3 py-8 text-center text-slate-400">当前维度暂无数据</td></tr>`;
        }
        this.renderChart();
    }

    renderChart() {
        const target = this.document.getElementById('channel-owner-chart');
        if (!target || !this.data || !this.chartFactory) return;
        this.chart?.dispose();
        this.chart = this.chartFactory.init(target);
        const rows = this.periodRows().slice().reverse();
        const showingRevenue = this.metric === 'revenue';
        this.chart.setOption({
            grid: { left: 54, right: 22, top: 24, bottom: 36 },
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: rows.map((row) => row.period), boundaryGap: false, axisLabel: { color: '#64748b' } },
            yAxis: { type: 'value', axisLabel: { color: '#64748b', formatter: showingRevenue ? '${value}' : '{value}' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
            series: [{ type: 'line', smooth: true, showSymbol: false, areaStyle: { opacity: 0.12 }, lineStyle: { width: 3, color: showingRevenue ? '#10b981' : '#6366f1' }, itemStyle: { color: showingRevenue ? '#10b981' : '#6366f1' }, data: rows.map((row) => showingRevenue ? row.estimated_revenue : row.subscribers) }],
        });
    }

    updateMetricButtons() {
        this.document.querySelectorAll('[data-owner-metric]').forEach((button) => {
            const selected = button.dataset.ownerMetric === this.metric;
            button.classList.toggle('bg-white', selected);
            button.classList.toggle('text-indigo-700', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('text-slate-500', !selected);
        });
    }

    updatePeriodButtons() {
        this.document.querySelectorAll('[data-owner-period]').forEach((button) => {
            const selected = button.dataset.ownerPeriod === this.period;
            button.classList.toggle('bg-indigo-600', selected);
            button.classList.toggle('text-white', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('text-slate-500', !selected);
        });
    }

    metricCard(label, value, icon, color) {
        return `<div class="rounded-xl border border-slate-100 bg-white p-4 shadow-sm"><div class="flex items-center justify-between"><span class="text-xs font-semibold text-slate-500">${label}</span><i class="${icon} ${color}" aria-hidden="true"></i></div><p class="mt-2 text-xl font-bold tabular-nums text-slate-900">${value}</p></div>`;
    }

    videoRow(video, featured = false) {
        const title = ChannelOwnerProfile.escapeHtml(video.title || '未命名视频');
        const published = video.published_at ? video.published_at.slice(0, 10) : '未知日期';
        return `<a href="${ChannelOwnerProfile.escapeHtml(video.url || '#')}" target="_blank" rel="noopener noreferrer" class="flex gap-3 py-3 transition hover:bg-slate-50 ${featured ? 'rounded-lg px-2' : ''}"><div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${featured ? 'bg-amber-50 text-amber-600' : 'bg-blue-50 text-blue-600'}"><i class="fas ${featured ? 'fa-trophy' : 'fa-play'}" aria-hidden="true"></i></div><div class="min-w-0 flex-1"><p class="truncate text-sm font-semibold text-slate-800">${title}</p><p class="mt-1 text-xs text-slate-500">${published} · ${this.formatNumber(video.view_count)} 播放 · ${this.formatNumber(video.like_count)} 点赞</p></div><i class="fas fa-arrow-up-right-from-square mt-1 text-xs text-slate-300" aria-hidden="true"></i></a>`;
    }

    empty(message) { return `<p class="py-6 text-center text-sm text-slate-400">${message}</p>`; }

    renderError(message) {
        const container = this.document.getElementById('channel-owner-profile');
        container.innerHTML = `<div class="px-6 py-24 text-center text-slate-500"><i class="fas fa-circle-exclamation mb-3 text-3xl text-amber-500" aria-hidden="true"></i><p class="font-semibold">${ChannelOwnerProfile.escapeHtml(message)}</p><a href="rankings.html" class="mt-4 inline-flex rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">返回排行榜</a></div>`;
    }

    formatNumber(value) { return new Intl.NumberFormat('zh-CN').format(Number(value) || 0); }
    formatCurrency(value) { return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(Number(value) || 0); }
    formatSigned(value) { const number = Number(value) || 0; return `${number > 0 ? '+' : ''}${this.formatNumber(number)}`; }
    static escapeHtml(value) { return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;'); }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { ChannelOwnerProfile };
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', async () => {
        const canAccessPage = await (window.pageAccessGuard || Promise.resolve(true));
        if (canAccessPage) window.channelOwnerProfile = new ChannelOwnerProfile(), window.channelOwnerProfile.init();
    });
}
