const RANKING_PAGE_TYPES = {
    daily_revenue: {
        periodMode: 'daily',
        endpointKey: 'RANKINGS_DAILY_REVENUE',
        valueField: 'revenue',
        valueKind: 'currency',
        valueLabel: '日收入',
        valueClass: 'text-green-600',
        progressColor: '#22c55e',
    },
    monthly_revenue: {
        periodMode: 'monthly',
        endpointKey: 'RANKINGS_MONTHLY_REVENUE',
        valueField: 'total_revenue',
        valueKind: 'currency',
        valueLabel: '月收入',
        valueClass: 'text-blue-600',
        progressColor: '#3b82f6',
    },
    yearly_revenue: {
        periodMode: 'yearly',
        endpointKey: 'RANKINGS_YEARLY_REVENUE',
        valueField: 'total_revenue',
        valueKind: 'currency',
        valueLabel: '年收入',
        valueClass: 'text-cyan-600',
        progressColor: '#0891b2',
    },
    daily_subscriber: {
        periodMode: 'daily',
        endpointKey: 'RANKINGS_DAILY_SUBSCRIBER',
        valueField: 'subscriber_change',
        valueKind: 'subscriber',
        valueLabel: '日增订阅',
        valueClass: 'text-purple-600',
        progressColor: '#a855f7',
    },
    monthly_subscriber: {
        periodMode: 'monthly',
        endpointKey: 'RANKINGS_MONTHLY_SUBSCRIBER',
        valueField: 'total_subscriber_change',
        valueKind: 'subscriber',
        valueLabel: '月增订阅',
        valueClass: 'text-orange-600',
        progressColor: '#f97316',
    },
    yearly_subscriber: {
        periodMode: 'yearly',
        endpointKey: 'RANKINGS_YEARLY_SUBSCRIBER',
        valueField: 'total_subscriber_change',
        valueKind: 'subscriber',
        valueLabel: '年增订阅',
        valueClass: 'text-rose-600',
        progressColor: '#e11d48',
    },
};

class RankingsPage {
    constructor({
        apiClient = api,
        config = CONFIG,
        documentRef = document,
        locationRef = window.location,
        historyRef = window.history,
        today = RankingsPage.formatLocalDate(new Date()),
    } = {}) {
        this.api = apiClient;
        this.config = config;
        this.document = documentRef;
        this.location = locationRef;
        this.history = historyRef;
        this.page = 1;
        this.perPage = 20;
        this.requestSequence = 0;
        this.leaderValues = {};

        this.today = today;
        this.currentMonth = today.slice(0, 7);
        this.currentYear = today.slice(0, 4);

        const query = new URLSearchParams(this.location.search || '');
        const requestedType = query.get('type');
        this.type = RANKING_PAGE_TYPES[requestedType] ? requestedType : 'daily_revenue';
        this.dailyDate = RankingsPage.validDate(query.get('date')) ? query.get('date') : this.today;
        this.monthlyMonth = RankingsPage.validMonth(query.get('month')) ? query.get('month') : this.currentMonth;
        this.yearlyYear = RankingsPage.validYear(query.get('year')) ? query.get('year') : this.currentYear;
        this.tabs = Array.from(this.document.querySelectorAll('[data-ranking-type]'));
        this.results = this.document.getElementById('ranking-results');
        this.total = this.document.getElementById('ranking-total');
        this.period = this.document.getElementById('ranking-period');
        this.pagination = this.document.getElementById('ranking-pagination');
        this.pageSize = this.document.getElementById('ranking-page-size');
        this.status = this.document.getElementById('ranking-status');
        this.valueHeader = this.document.getElementById('ranking-value-header');
        this.periodInput = this.document.getElementById('ranking-period-input');
        this.currentPeriodButton = this.document.getElementById('ranking-current-period');
        this.periodControls = this.document.getElementById('ranking-period-controls');
        this.periodShiftButtons = Array.from(this.document.querySelectorAll('[data-period-shift]'));
    }

    async init() {
        this.tabs.forEach((tab) => {
            tab.addEventListener('click', () => this.selectType(tab.dataset.rankingType));
        });
        this.pageSize?.addEventListener('change', () => {
            this.perPage = Number(this.pageSize.value) || 20;
            this.page = 1;
            return this.load();
        });
        this.pagination?.addEventListener('click', (event) => {
            const button = event.target.closest?.('[data-page]');
            if (!button || button.disabled) return;
            const nextPage = Number(button.dataset.page);
            if (!Number.isInteger(nextPage) || nextPage < 1 || nextPage === this.page) return;
            this.page = nextPage;
            this.load();
        });
        this.periodInput?.addEventListener('change', () => this.selectPeriod(this.periodInput.value));
        this.currentPeriodButton?.addEventListener('click', () => this.selectCurrentPeriod());
        this.periodShiftButtons.forEach((button) => {
            button.addEventListener('click', () => this.shiftPeriod(Number(button.dataset.periodShift)));
        });
        this.updateTabs();
        this.updatePeriodControl();
        return this.load();
    }

    async selectType(type) {
        if (!RANKING_PAGE_TYPES[type]) return;
        this.type = type;
        this.page = 1;
        this.updateTabs();
        this.updatePeriodControl();
        this.syncUrl();
        return this.load();
    }

    periodMode() {
        return RANKING_PAGE_TYPES[this.type].periodMode;
    }

    selectedPeriod() {
        if (this.periodMode() === 'yearly') return this.yearlyYear;
        return this.periodMode() === 'monthly' ? this.monthlyMonth : this.dailyDate;
    }

    updatePeriodControl() {
        const monthly = this.periodMode() === 'monthly';
        const yearly = this.periodMode() === 'yearly';
        const selected = this.selectedPeriod();
        const current = yearly ? this.currentYear : monthly ? this.currentMonth : this.today;
        if (this.periodInput) {
            this.periodInput.type = yearly ? 'number' : monthly ? 'month' : 'date';
            this.periodInput.value = selected;
            this.periodInput.max = current;
            this.periodInput.min = yearly ? '2020' : '';
            this.periodInput.step = yearly ? '1' : '';
            this.periodInput.setAttribute('aria-label', yearly ? '选择排行榜年份' : monthly ? '选择排行榜月份' : '选择排行榜日期');
        }
        this.periodShiftButtons.forEach((button) => {
            if (Number(button.dataset.periodShift) > 0) button.disabled = selected >= current;
        });
        if (this.currentPeriodButton) this.currentPeriodButton.disabled = selected === current;
    }

    periodParams() {
        if (this.periodMode() === 'yearly') return { year: Number(this.yearlyYear) };
        if (this.periodMode() === 'monthly') {
            const [year, month] = this.monthlyMonth.split('-').map(Number);
            return { year, month };
        }
        return { date: this.dailyDate };
    }

    syncUrl() {
        const params = new URLSearchParams({ type: this.type });
        const periodKey = this.periodMode() === 'yearly' ? 'year' : this.periodMode() === 'monthly' ? 'month' : 'date';
        params.set(periodKey, this.selectedPeriod());
        this.history?.replaceState?.({}, '', `${this.location.pathname}?${params.toString()}`);
    }

    async selectPeriod(value) {
        const monthly = this.periodMode() === 'monthly';
        const yearly = this.periodMode() === 'yearly';
        const normalizedValue = String(value);
        const current = yearly ? this.currentYear : monthly ? this.currentMonth : this.today;
        const valid = yearly ? RankingsPage.validYear(normalizedValue) : monthly ? RankingsPage.validMonth(normalizedValue) : RankingsPage.validDate(normalizedValue);
        if (!valid) {
            this.updatePeriodControl();
            return;
        }
        const selected = normalizedValue > current ? current : normalizedValue;
        if (yearly) this.yearlyYear = selected;
        else if (monthly) this.monthlyMonth = selected;
        else this.dailyDate = selected;
        this.page = 1;
        this.updatePeriodControl();
        this.syncUrl();
        return this.load();
    }

    async shiftPeriod(delta) {
        if (!Number.isInteger(delta) || delta === 0) return;
        let shifted;
        if (this.periodMode() === 'yearly') {
            shifted = String(Number(this.yearlyYear) + delta);
        } else if (this.periodMode() === 'monthly') {
            const [year, month] = this.monthlyMonth.split('-').map(Number);
            const date = new Date(year, month - 1 + delta, 1, 12);
            shifted = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
        } else {
            const [year, month, day] = this.dailyDate.split('-').map(Number);
            const date = new Date(year, month - 1, day, 12);
            date.setDate(date.getDate() + delta);
            shifted = RankingsPage.formatLocalDate(date);
        }
        return this.selectPeriod(shifted);
    }

    async selectCurrentPeriod() {
        const current = this.periodMode() === 'yearly' ? this.currentYear : this.periodMode() === 'monthly' ? this.currentMonth : this.today;
        return this.selectPeriod(current);
    }

    updateTabs() {
        this.tabs.forEach((tab) => {
            const selected = tab.dataset.rankingType === this.type;
            tab.setAttribute('aria-selected', selected ? 'true' : 'false');
            tab.setAttribute('tabindex', selected ? '0' : '-1');
            tab.classList.toggle('border-blue-600', selected);
            tab.classList.toggle('text-blue-700', selected);
            tab.classList.toggle('bg-blue-50', selected);
            tab.classList.toggle('border-transparent', !selected);
            tab.classList.toggle('text-slate-500', !selected);
            if (selected && this.results) {
                this.results.setAttribute('aria-labelledby', tab.id);
            }
        });
        if (this.valueHeader) this.valueHeader.textContent = RANKING_PAGE_TYPES[this.type].valueLabel;
    }

    async load() {
        const sequence = ++this.requestSequence;
        const definition = RANKING_PAGE_TYPES[this.type];
        const endpoint = this.config.ENDPOINTS[definition.endpointKey];
        this.renderLoading();

        try {
            const response = await this.api.get(endpoint, {
                page: this.page,
                per_page: this.perPage,
                ...this.periodParams(),
            });
            if (sequence !== this.requestSequence) return;
            if (!response?.success || !response.data) {
                throw new Error(response?.message || '排行榜数据不可用');
            }
            this.render(response.data, definition);
        } catch (error) {
            if (sequence !== this.requestSequence) return;
            this.renderError(error);
        }
    }

    renderLoading() {
        if (this.status) this.status.textContent = '正在加载…';
        if (this.results) {
            this.results.innerHTML = `
                <div class="px-6 py-16 text-center text-slate-400">
                    <i class="fas fa-spinner fa-spin mb-3 text-2xl" aria-hidden="true"></i>
                    <p>正在加载完整排行榜…</p>
                </div>`;
        }
    }

    render(data, definition) {
        const rankings = Array.isArray(data.rankings) ? data.rankings : [];
        const pagination = data.pagination || {
            page: this.page,
            per_page: this.perPage,
            total: Number(data.total_count) || rankings.length,
            total_pages: 1,
        };
        this.page = pagination.page;
        if (this.total) this.total.textContent = `共 ${pagination.total} 个频道`;
        if (this.status) this.status.textContent = rankings.length ? `第 ${pagination.page} / ${Math.max(1, pagination.total_pages)} 页` : '';
        this.renderPeriod(data);

        if (!rankings.length) {
            this.results.innerHTML = `
                <div class="px-6 py-16 text-center text-slate-400">
                    <i class="fas fa-chart-line mb-3 text-3xl opacity-50" aria-hidden="true"></i>
                    <p class="font-medium">当前周期暂无排行榜数据</p>
                </div>`;
        } else {
            if (Number(pagination.page) === 1) {
                this.leaderValues[this.leaderKey()] = Math.max(0, Number(rankings[0]?.[definition.valueField]) || 0);
            }
            const leaderValue = this.leaderValues[this.leaderKey()]
                || Math.max(0, ...rankings.map((item) => Number(item[definition.valueField]) || 0));
            this.results.innerHTML = rankings
                .map((item) => this.renderRow(item, definition, { leaderValue }))
                .join('');
        }
        this.renderPagination(pagination);
    }

    renderPeriod(data) {
        if (!this.period) return;
        if (data.date) {
            this.period.textContent = `统计日期：${data.date}`;
        } else if (data.period?.start_date && data.period?.end_date) {
            this.period.textContent = `统计周期：${data.period.start_date} 至 ${data.period.end_date}`;
        } else {
            this.period.textContent = '';
        }
    }

    leaderKey() {
        return `${this.type}:${this.selectedPeriod()}`;
    }

    renderRow(item, definition, { leaderValue = 0 } = {}) {
        const rank = Number(item.rank) || 0;
        const rawValue = Number(item[definition.valueField]) || 0;
        const value = this.formatValue(rawValue, definition.valueKind);
        const relativePercent = leaderValue > 0
            ? Math.min(100, Math.max(0, Number(((rawValue / leaderValue) * 100).toFixed(1))))
            : 0;
        const channelName = RankingsPage.escapeHtml(item.channel_name || '未命名频道');
        const category = RankingsPage.escapeHtml(item.category || '未分类');
        const cpm = Number(item.cpm);
        const cpmLabel = Number.isFinite(cpm) ? `$${cpm.toFixed(2)}/千次` : '未设置';
        const channelId = encodeURIComponent(item.channel_id);
        const videoUrl = `videos.html?channel_id=${channelId}&channel_name=${encodeURIComponent(item.channel_name || '')}`;
        return `
            <div data-ranking-row class="grid min-h-6 grid-cols-[40px_minmax(140px,1fr)_110px_28px] items-center px-3 py-0.5 transition-colors hover:bg-blue-50/50 sm:grid-cols-[56px_minmax(360px,520px)_160px_minmax(220px,1fr)_44px] sm:px-4 lg:grid-cols-[56px_minmax(260px,390px)_72px_112px_150px_minmax(180px,1fr)_44px]">
                <div>${rank <= 3
                    ? `<span class="inline-flex h-6 w-6 items-center justify-center rounded-full bg-blue-50 text-xs font-bold text-blue-700">${rank}</span>`
                    : `<span class="inline-flex h-6 w-6 items-center justify-center text-xs font-semibold text-slate-500">${rank}</span>`}</div>
                <div data-channel-identity class="flex min-w-0 items-center gap-3">
                    <span class="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800 sm:w-52 sm:flex-none sm:shrink-0" title="${channelName}">${channelName}</span>
                    <span class="hidden min-w-0 truncate pr-3 text-xs text-slate-400 sm:inline">${RankingsPage.escapeHtml(item.youtube_id || '')}</span>
                </div>
                <div data-category-column class="hidden truncate border-l border-slate-100 pl-3 text-xs font-medium text-slate-600 lg:block" title="分类：${category}">${category}</div>
                <div data-cpm-column class="hidden whitespace-nowrap border-l border-slate-100 px-3 text-right text-xs font-semibold tabular-nums text-amber-700 lg:block" title="千次点击参考值">${cpmLabel}</div>
                <p class="text-right text-sm font-bold ${definition.valueClass}">${value}</p>
                <div class="hidden items-center gap-3 pl-8 sm:flex">
                    <progress value="${relativePercent}" max="100" class="ranking-progress min-w-24 flex-1" style="--ranking-progress-color: ${definition.progressColor || '#3b82f6'}" aria-label="${channelName} 相对榜首 ${relativePercent}%"></progress>
                    <span class="w-11 text-right text-xs tabular-nums text-slate-500">${relativePercent}%</span>
                </div>
                <div class="text-right">
                    <a href="${videoUrl}" class="inline-flex h-6 w-6 items-center justify-center rounded-md text-xs text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600" aria-label="查看 ${channelName} 的视频">
                        <i class="fas fa-arrow-right" aria-hidden="true"></i>
                    </a>
                </div>
            </div>`;
    }

    formatValue(value, kind) {
        const number = Number(value) || 0;
        if (kind === 'currency') {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD',
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            }).format(number);
        }
        const sign = number > 0 ? '+' : '';
        return `${sign}${new Intl.NumberFormat('en-US').format(number)}`;
    }

    renderPagination(pagination) {
        if (!this.pagination) return;
        const totalPages = Number(pagination.total_pages) || 0;
        if (totalPages <= 1) {
            this.pagination.innerHTML = '';
            return;
        }
        const current = Number(pagination.page) || 1;
        const pageNumbers = [];
        for (let page = Math.max(1, current - 2); page <= Math.min(totalPages, current + 2); page += 1) {
            pageNumbers.push(page);
        }
        const button = (label, page, disabled = false, selected = false) => `
            <button type="button" data-page="${page}" ${disabled ? 'disabled' : ''} ${selected ? 'aria-current="page"' : ''}
                class="min-w-7 rounded-md border px-2 py-1 text-xs font-semibold transition-colors ${selected ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-600'} disabled:cursor-not-allowed disabled:opacity-40">
                ${label}
            </button>`;
        this.pagination.innerHTML = [
            button('<i class="fas fa-chevron-left" aria-hidden="true"></i><span class="sr-only">上一页</span>', current - 1, current === 1),
            ...pageNumbers.map((page) => button(page, page, false, page === current)),
            button('<i class="fas fa-chevron-right" aria-hidden="true"></i><span class="sr-only">下一页</span>', current + 1, current === totalPages),
        ].join('');
    }

    renderError(error) {
        if (this.total) this.total.textContent = '排行榜加载失败';
        if (this.period) this.period.textContent = '';
        if (this.status) this.status.textContent = '请稍后重试';
        if (this.pagination) this.pagination.innerHTML = '';
        if (this.results) {
            this.results.innerHTML = `
                <div class="px-6 py-16 text-center text-red-500">
                    <i class="fas fa-circle-exclamation mb-3 text-3xl" aria-hidden="true"></i>
                    <p class="font-medium">${RankingsPage.escapeHtml(error?.message || '加载排行榜失败')}</p>
                    <button type="button" data-ranking-retry class="mt-4 rounded-lg bg-red-50 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-100">重新加载</button>
                </div>`;
            this.results.querySelector?.('[data-ranking-retry]')?.addEventListener('click', () => this.load());
        }
    }

    static escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    static formatLocalDate(date) {
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }

    static validDate(value) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false;
        const [year, month, day] = value.split('-').map(Number);
        const date = new Date(year, month - 1, day, 12);
        return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
    }

    static validMonth(value) {
        return /^\d{4}-(0[1-9]|1[0-2])$/.test(value || '');
    }

    static validYear(value) {
        return /^\d{4}$/.test(value || '') && Number(value) >= 2020 && Number(value) <= 2030;
    }
}

class RankingsHub {
    constructor({
        rankingPage,
        trendsPage,
        documentRef = document,
        locationRef = window.location,
        historyRef = window.history,
    } = {}) {
        this.rankingPage = rankingPage;
        this.trendsPage = trendsPage;
        this.document = documentRef;
        this.location = locationRef;
        this.history = historyRef;
        this.buttons = Array.from(this.document.querySelectorAll('[data-page-view]'));
        this.rankingSection = this.document.getElementById('ranking-workspace');
        this.trendSection = this.document.getElementById('trend-workspace');
        this.loaded = { rankings: false, trends: false };
        const requestedView = new URLSearchParams(this.location.search || '').get('view');
        this.view = requestedView === 'trends' ? 'trends' : 'rankings';
    }

    async init() {
        this.buttons.forEach((button) => {
            button.addEventListener('click', () => this.selectView(button.dataset.pageView));
        });
        await this.activateView();
    }

    async selectView(view) {
        if (!['rankings', 'trends'].includes(view) || view === this.view) return;
        this.view = view;
        const url = view === 'trends'
            ? `${this.location.pathname}?view=trends`
            : `${this.location.pathname}?type=${this.rankingPage.type}`;
        this.history?.replaceState?.({}, '', url);
        await this.activateView();
    }

    async activateView() {
        const rankingsActive = this.view === 'rankings';
        if (this.rankingSection) this.rankingSection.hidden = !rankingsActive;
        if (this.trendSection) this.trendSection.hidden = rankingsActive;
        this.buttons.forEach((button) => {
            const selected = button.dataset.pageView === this.view;
            button.setAttribute('aria-selected', selected ? 'true' : 'false');
            button.classList.toggle('bg-indigo-600', selected);
            button.classList.toggle('text-white', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('text-slate-600', !selected);
            button.classList.toggle('hover:bg-slate-100', !selected);
        });

        if (rankingsActive && !this.loaded.rankings) {
            this.loaded.rankings = true;
            await this.rankingPage.init();
        } else if (!rankingsActive && !this.loaded.trends) {
            this.loaded.trends = true;
            await this.trendsPage.init();
        } else if (!rankingsActive) {
            this.trendsPage.resize();
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { RankingsPage, RankingsHub, RANKING_PAGE_TYPES };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', async () => {
        const canAccessPage = await (window.pageAccessGuard || Promise.resolve(true));
        if (!canAccessPage) return;
        window.rankingsPage = new RankingsPage();
        window.rankingsTrends = new RankingsTrends();
        window.rankingsHub = new RankingsHub({
            rankingPage: window.rankingsPage,
            trendsPage: window.rankingsTrends,
        });
        await window.rankingsHub.init();
    });
}
