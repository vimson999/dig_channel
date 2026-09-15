const TREND_METRICS = {
    revenue: { label: '预估收入', unit: '美元' },
    subscribers: { label: '订阅总量', unit: '订阅' },
    views: { label: '播放总量', unit: '播放' },
};

class RankingsTrends {
    constructor({
        apiClient = api,
        echartsRef = echarts,
        documentRef = document,
        windowRef = typeof window !== 'undefined' ? window : null,
    } = {}) {
        this.api = apiClient;
        this.echarts = echartsRef;
        this.document = documentRef;
        this.window = windowRef;
        this.chart = null;
        this.initialized = false;
        this.requestSequence = 0;
        this.metric = 'revenue';
        this.chartType = 'line';
        this.granularity = 'daily';
        this.timeRange = 7;
        this.monthRange = 6;
        this.channels = [];
        this.selectedChannelIds = [];
        this.temporaryChannelIds = [];
        this.chartData = { dates: [], series: [] };
        this.chartMeta = {};

        this.elements = {
            chart: this.document.getElementById('trend-chart'),
            status: this.document.getElementById('trend-status'),
            meta: this.document.getElementById('trend-meta'),
            empty: this.document.getElementById('trend-empty'),
            metric: this.document.getElementById('trend-metric'),
            dailyRange: this.document.getElementById('trend-daily-range'),
            monthlyRange: this.document.getElementById('trend-monthly-range'),
            filterButton: this.document.getElementById('trend-channel-filter'),
            filterPanel: this.document.getElementById('trend-channel-panel'),
            channelList: this.document.getElementById('trend-channel-list'),
            channelSearch: this.document.getElementById('trend-channel-search'),
            channelSummary: this.document.getElementById('trend-channel-summary'),
            filterApply: this.document.getElementById('trend-filter-apply'),
            filterClear: this.document.getElementById('trend-filter-clear'),
            filterClose: this.document.getElementById('trend-filter-close'),
        };
        this.chartButtons = Array.from(this.document.querySelectorAll('[data-trend-chart]'));
        this.granularityButtons = Array.from(this.document.querySelectorAll('[data-trend-granularity]'));
    }

    async init() {
        if (this.initialized) {
            this.resize();
            return;
        }
        this.initialized = true;
        this.bindEvents();
        this.updateControls();
        this.closeChannelFilter();
        this.setHidden(this.elements.empty, true);

        if (!this.elements.chart || !this.echarts?.init) {
            this.showEmpty('图表组件未能加载，请刷新页面重试');
            return;
        }
        this.chart = this.echarts.init(this.elements.chart);
        await this.loadChannels();
        await this.loadData();
    }

    bindEvents() {
        this.chartButtons.forEach((button) => {
            button.addEventListener('click', () => this.selectChartType(button.dataset.trendChart));
        });
        this.granularityButtons.forEach((button) => {
            button.addEventListener('click', () => this.selectGranularity(button.dataset.trendGranularity));
        });
        this.elements.metric?.addEventListener('change', () => {
            this.metric = TREND_METRICS[this.elements.metric.value] ? this.elements.metric.value : 'revenue';
            this.loadData();
        });
        this.elements.dailyRange?.addEventListener('change', () => {
            this.timeRange = RankingsTrends.normalizeRange(this.elements.dailyRange.value, [7, 30, 90], 7);
            this.loadData();
        });
        this.elements.monthlyRange?.addEventListener('change', () => {
            this.monthRange = RankingsTrends.normalizeRange(this.elements.monthlyRange.value, [3, 6, 12, 24], 6);
            this.loadData();
        });
        this.elements.filterButton?.addEventListener('click', () => this.openChannelFilter());
        this.elements.filterClose?.addEventListener('click', () => this.closeChannelFilter());
        this.elements.filterClear?.addEventListener('click', () => {
            this.temporaryChannelIds = [];
            this.renderChannelList();
        });
        this.elements.filterApply?.addEventListener('click', () => this.applyFilterFromPanel());
        this.elements.channelSearch?.addEventListener('input', () => this.renderChannelList());
        this.elements.channelList?.addEventListener('change', (event) => {
            const checkbox = event.target.closest?.('[data-channel-id]');
            if (!checkbox) return;
            const channelId = Number(checkbox.dataset.channelId);
            if (!Number.isInteger(channelId)) return;
            if (checkbox.checked) {
                if (!this.temporaryChannelIds.includes(channelId)) this.temporaryChannelIds.push(channelId);
            } else {
                this.temporaryChannelIds = this.temporaryChannelIds.filter((id) => id !== channelId);
            }
        });
        this.elements.filterPanel?.addEventListener('click', (event) => {
            if (event.target === this.elements.filterPanel) this.closeChannelFilter();
        });
        this.window?.addEventListener?.('resize', () => this.resize());
    }

    async loadChannels() {
        try {
            const response = await this.api.getChannels({ active_only: true, page: 1, per_page: 100 });
            this.channels = response?.success && Array.isArray(response.data?.channels)
                ? response.data.channels
                : [];
            this.renderChannelList();
        } catch (_error) {
            this.channels = [];
            if (this.elements.channelList) {
                this.elements.channelList.innerHTML = '<p class="px-5 py-8 text-center text-sm text-red-500">频道列表加载失败</p>';
            }
        }
    }

    async loadData() {
        const sequence = ++this.requestSequence;
        if (this.elements.status) this.elements.status.textContent = '正在加载趋势…';
        this.setHidden(this.elements.empty, true);

        const params = { time_range: this.timeRange, group: this.granularity };
        if (this.granularity === 'monthly') params.months = this.monthRange;
        if (this.selectedChannelIds.length) params.channel_ids = this.selectedChannelIds.join(',');

        try {
            const response = await this.api.getChartData(this.metric, params);
            if (sequence !== this.requestSequence) return;
            const data = response?.success ? response.data : null;
            const chartData = data?.chart_data;
            if (!chartData || !Array.isArray(chartData.dates) || !Array.isArray(chartData.series)) {
                this.chartData = { dates: [], series: [] };
                this.chartMeta = {};
                this.showEmpty('暂无趋势数据，请调整筛选条件');
                return;
            }
            this.chartData = chartData;
            this.chartMeta = data;
            this.renderChart();
            this.renderMeta();
            if (this.elements.status) this.elements.status.textContent = '数据已更新';
        } catch (error) {
            if (sequence !== this.requestSequence) return;
            this.chartData = { dates: [], series: [] };
            this.chartMeta = {};
            this.showEmpty(error?.message || '趋势数据加载失败，请稍后重试');
        }
    }

    selectChartType(type) {
        if (!['line', 'bar', 'pie'].includes(type)) return;
        this.chartType = type;
        this.updateControls();
        this.renderChart();
    }

    async selectGranularity(value) {
        const next = value === 'monthly' ? 'monthly' : 'daily';
        if (next === this.granularity) {
            this.updateControls();
            return;
        }
        this.granularity = next;
        this.updateControls();
        await this.loadData();
    }

    async applyChannelSelection(channelIds) {
        this.selectedChannelIds = Array.from(new Set((channelIds || [])
            .map(Number)
            .filter(Number.isInteger)));
        this.temporaryChannelIds = [...this.selectedChannelIds];
        this.updateChannelSummary();
        this.renderChannelList();
        this.closeChannelFilter();
        await this.loadData();
    }

    async applyFilterFromPanel() {
        await this.applyChannelSelection(this.temporaryChannelIds);
    }

    openChannelFilter() {
        this.temporaryChannelIds = [...this.selectedChannelIds];
        if (this.elements.channelSearch) this.elements.channelSearch.value = '';
        this.renderChannelList();
        this.setHidden(this.elements.filterPanel, false);
        this.elements.channelSearch?.focus?.();
    }

    closeChannelFilter() {
        this.setHidden(this.elements.filterPanel, true);
    }

    renderChannelList() {
        if (!this.elements.channelList) return;
        const query = String(this.elements.channelSearch?.value || '').trim().toLowerCase();
        const filtered = this.channels.filter((channel) => {
            const haystack = `${channel.name || channel.channel_name || ''} ${channel.category || ''}`.toLowerCase();
            return !query || haystack.includes(query);
        });
        if (!filtered.length) {
            this.elements.channelList.innerHTML = '<p class="px-5 py-8 text-center text-sm text-slate-400">没有匹配的频道</p>';
            return;
        }
        this.elements.channelList.innerHTML = filtered.map((channel) => {
            const id = Number(channel.id);
            const checked = this.temporaryChannelIds.includes(id) ? 'checked' : '';
            const name = RankingsTrends.escapeHtml(channel.name || channel.channel_name || '未命名频道');
            const category = RankingsTrends.escapeHtml(channel.category || '未分类');
            return `<label class="flex cursor-pointer items-center gap-3 px-5 py-3.5 transition hover:bg-indigo-50/60">
                <input type="checkbox" data-channel-id="${id}" ${checked} class="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500">
                <span class="min-w-0 flex-1"><span class="block truncate text-sm font-semibold text-slate-800">${name}</span><span class="mt-0.5 block truncate text-xs text-slate-400">${category}</span></span>
            </label>`;
        }).join('');
    }

    updateControls() {
        this.chartButtons.forEach((button) => {
            const selected = button.dataset.trendChart === this.chartType;
            button.setAttribute('aria-pressed', selected ? 'true' : 'false');
            button.classList.toggle('bg-white', selected);
            button.classList.toggle('text-indigo-700', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('text-slate-500', !selected);
        });
        this.granularityButtons.forEach((button) => {
            const selected = button.dataset.trendGranularity === this.granularity;
            button.setAttribute('aria-pressed', selected ? 'true' : 'false');
            button.classList.toggle('bg-white', selected);
            button.classList.toggle('text-indigo-700', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('text-slate-500', !selected);
        });
        if (this.elements.dailyRange) this.elements.dailyRange.hidden = this.granularity !== 'daily';
        if (this.elements.monthlyRange) this.elements.monthlyRange.hidden = this.granularity !== 'monthly';
        this.updateChannelSummary();
    }

    updateChannelSummary() {
        if (!this.elements.channelSummary) return;
        this.elements.channelSummary.textContent = this.selectedChannelIds.length
            ? `已选 ${this.selectedChannelIds.length} 个频道`
            : '全部频道';
    }

    renderChart() {
        if (!this.chart) return;
        const dates = Array.isArray(this.chartData.dates) ? this.chartData.dates : [];
        const series = Array.isArray(this.chartData.series) ? this.chartData.series : [];
        if (!dates.length || !series.length) {
            this.showEmpty('暂无趋势数据，请调整筛选条件');
            return;
        }
        this.setHidden(this.elements.empty, true);
        const labels = Array.isArray(this.chartData.labels) ? this.chartData.labels : dates;
        const metric = TREND_METRICS[this.metric] || TREND_METRICS.revenue;
        const base = {
            animationDuration: 280,
            color: ['#4f6fdd', '#65bde3', '#6fbf61', '#f6ad2f', '#e969a8', '#28a271', '#8b5fc0', '#f26c4f', '#ef5b64', '#64748b'],
            tooltip: { trigger: this.chartType === 'pie' ? 'item' : 'axis', confine: true },
            legend: { type: 'scroll', top: 4, left: 0, right: 0, textStyle: { color: '#475569', fontSize: 11 } },
        };
        if (this.chartType === 'pie') {
            const rankedSlices = series
                .map((item) => ({ name: item.name, value: RankingsTrends.latestValue(item.data) }))
                .filter((item) => item.value > 0)
                .sort((left, right) => right.value - left.value);
            const pieData = rankedSlices.slice(0, 10);
            const remainingTotal = rankedSlices
                .slice(10)
                .reduce((total, item) => total + item.value, 0);
            if (remainingTotal > 0) pieData.push({ name: '其他频道', value: remainingTotal });
            base.tooltip.formatter = ({ name, value, percent }) => `${RankingsTrends.escapeHtml(name)}<br>${this.formatValue(value)} (${percent}%)`;
            base.series = [{
                name: `${metric.label}占比`, type: 'pie', radius: ['38%', '68%'], center: ['50%', '57%'],
                data: pieData,
                label: { formatter: '{b}\n{d}%', color: '#475569', fontSize: 11 },
                emphasis: { scaleSize: 8 },
            }];
        } else {
            base.grid = { left: 20, right: 24, top: 76, bottom: 20, containLabel: true };
            base.xAxis = { type: 'category', boundaryGap: this.chartType === 'bar', data: labels, axisLine: { lineStyle: { color: '#cbd5e1' } }, axisLabel: { color: '#64748b', hideOverlap: true } };
            base.yAxis = { type: 'value', name: metric.unit, nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b' } };
            base.series = series.map((item) => ({
                name: item.name,
                type: this.chartType,
                data: item.data,
                smooth: this.chartType === 'line' ? 0.22 : false,
                symbolSize: 5,
                showSymbol: dates.length <= 31,
                lineStyle: { width: 2 },
                barMaxWidth: 26,
                emphasis: { focus: 'series' },
            }));
        }
        this.chart.setOption(base, true);
    }

    renderMeta() {
        const total = Number(this.chartMeta.total_channels) || this.chartData.series.length;
        const range = this.chartMeta.date_range || {};
        if (this.elements.meta) {
            this.elements.meta.textContent = range.start_date && range.end_date
                ? `${total} 个频道 · ${range.start_date} 至 ${range.end_date}`
                : `${total} 个频道`;
        }
    }

    showEmpty(message) {
        this.setHidden(this.elements.empty, false);
        if (this.elements.status) this.elements.status.textContent = message;
        if (this.elements.meta) this.elements.meta.textContent = '没有可显示的数据';
        this.chart?.clear?.();
    }

    resize() {
        this.chart?.resize?.();
    }

    setHidden(element, hidden) {
        if (!element) return;
        element.hidden = hidden;
        element.classList.toggle('hidden', hidden);
    }

    formatValue(value) {
        const number = Number(value) || 0;
        if (this.metric === 'revenue') return `$${number.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
        return number.toLocaleString('en-US');
    }

    static latestValue(values) {
        const data = Array.isArray(values) ? values : [];
        for (let index = data.length - 1; index >= 0; index -= 1) {
            if (data[index] != null) return Number(data[index]) || 0;
        }
        return 0;
    }

    static normalizeRange(value, allowed, fallback) {
        const number = Number(value);
        return allowed.includes(number) ? number : fallback;
    }

    static escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { RankingsTrends, TREND_METRICS };
}
