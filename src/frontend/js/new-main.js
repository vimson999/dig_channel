// 高保真设计的主应用文件 - 强制更新版本
console.log('🎯 [Main] 主应用模块已加载 - 版本', new Date().toISOString());

const ACTIVE_BUTTON_CLASS = 'px-3 py-2 text-primary-600 bg-white border border-primary-200 rounded-md font-medium shadow-sm hover:bg-primary-50 transition-all duration-200';
const INACTIVE_BUTTON_CLASS = 'px-3 py-2 text-gray-600 hover:text-primary-600 hover:bg-white rounded-md transition-all duration-200';

class YouTubeDataApp {
    constructor() {
        this.chart = null;
        this.currentChartType = 'line';
        this.currentTimeRange = 7;
        this.chartGranularity = 'daily';
        this.chartMetric = 'revenue';
        this.currentMonthRange = 6;
        this.channels = [];
        this.chartData = {};
        this.chartMeta = {};
        this.selectedChartChannelIds = null; // null: 自动 Top 5；[]: 明确不选频道
        this.channelCategories = [];
        this.channelCategoryDefaults = {};
        this.defaultChannelCpm = 2.5;
        this.loadingCount = 0;

        this.init();
    }

    async init() {
        console.log('🚀 YouTube数据追踪工具启动中...');
        
        // 检查认证状态
        console.log('🔍 [Main] 开始认证检查...');
        const isAuthenticated = await this.checkAuthStatus();
        
        if (!isAuthenticated) {
            console.log('❌ [Main] 用户未认证，重定向到登录页');
            window.location.href = 'login.html';
            return;
        }
        
        console.log('✅ [Main] 用户已认证，继续初始化应用');
        
        // 初始化图表
        this.initChart();
        
        // 绑定事件
        this.bindEvents();

        // 首页只保留一个加载入口，分类元数据不阻塞图表和排行榜。
        await Promise.all([this.loadChannelMetadata(), this.loadInitialData()]);

        if (typeof initializeDataTable === 'function') {
            initializeDataTable();
        }
        this.startAutoRefresh();
        
        console.log('✅ 应用初始化完成');
    }

    startAutoRefresh() {
        if (this.refreshPoller || !window.VisibilityPolling) return;
        this.refreshPoller = window.VisibilityPolling.create({
            intervalMs: 5 * 60 * 1000,
            poll: () => Promise.all([
                this.loadRankings(), this.loadChartData(),
                window.dataTableInstance?.loadTableData(),
                window.bilibiliChargingDashboard?.load(),
            ]),
        });
        this.refreshPoller.start();
    }

    bindEvents() {
        window.addEventListener('resize', () => this.chart?.resize());
        document.getElementById('chart-metric')?.addEventListener('change', (event) => {
            this.chartMetric = event.target.value;
            this.loadChartData();
        });
        document.getElementById('chart-reset')?.addEventListener('click', () => {
            this.selectedChartChannelIds = null;
            this.loadChartData();
        });
        // 时间范围选择
        const timeRangeSelect = document.getElementById('time-range');
        if (timeRangeSelect) {
            timeRangeSelect.addEventListener('change', async (e) => {
                this.currentTimeRange = parseInt(e.target.value, 10);

                this.showLoading('正在切换时间范围...');

                try {
                    await Promise.all([
                        this.loadChartData(),
                        window.dataTableInstance ? window.dataTableInstance.setRange(this.currentTimeRange) : Promise.resolve()
                    ]);
                    this.hideLoading();
                } catch (error) {
                    console.error('切换时间范围失败:', error);
                    this.hideLoading();
                    this.showToast('切换时间范围失败', 'error');
                }
            });
        }

        const monthRangeSelect = document.getElementById('month-range');
        if (monthRangeSelect) {
            monthRangeSelect.addEventListener('change', async (e) => {
                const parsed = parseInt(e.target.value, 10);
                if (!Number.isNaN(parsed) && parsed > 0) {
                    this.currentMonthRange = parsed;
                }

                this.showLoading('正在切换时间范围...');

                try {
                    await this.loadChartData();
                    this.hideLoading();
                } catch (error) {
                    console.error('切换月份范围失败:', error);
                    this.hideLoading();
                    this.showToast('切换月份范围失败', 'error');
                }
            });
        }

        // 默认CPM修改（如果元素存在）
        const defaultCpmElement = document.getElementById('default-cpm');
        console.log('🔍 检查 default-cpm 元素:', defaultCpmElement);
        if (defaultCpmElement) {
            console.log('✅ default-cpm 元素存在，绑定事件');
            defaultCpmElement.addEventListener('change', (e) => {
                this.updateDefaultCPM(parseFloat(e.target.value));
            });
        } else {
            console.log('❌ default-cpm 元素不存在，跳过绑定');
        }

        // 点击模态框外部关闭
        document.getElementById('addChannelModal').addEventListener('click', (e) => {
            if (e.target.id === 'addChannelModal') {
                this.closeAddChannelModal();
            }
        });

        const addCategorySelect = document.getElementById('channel-category');
        if (addCategorySelect) {
            addCategorySelect.addEventListener('change', (e) => {
                this.updateAddChannelCpm(e.target.value);
            });
        }

        const editCategorySelect = document.getElementById('edit-channel-category');
        if (editCategorySelect) {
            editCategorySelect.addEventListener('change', (e) => {
                this.updateEditChannelCpm(e.target.value);
            });
        }
        this.updateGranularityUI();
    }

    updateGranularityUI() {
        this.setGranularityButtonState('granularity-daily', this.chartGranularity === 'daily');
        this.setGranularityButtonState('granularity-monthly', this.chartGranularity === 'monthly');
        this.toggleRangeSelectors();
    }

    setGranularityButtonState(buttonId, isActive) {
        const button = document.getElementById(buttonId);
        if (!button) {
            return;
        }

        button.className = isActive ? ACTIVE_BUTTON_CLASS : INACTIVE_BUTTON_CLASS;
    }

    toggleRangeSelectors() {
        const timeWrapper = document.getElementById('time-range-wrapper');
        const monthWrapper = document.getElementById('month-range-wrapper');

        if (this.chartGranularity === 'monthly') {
            if (timeWrapper) {
                timeWrapper.classList.add('hidden');
            }
            if (monthWrapper) {
                monthWrapper.classList.remove('hidden');
            }
        } else {
            if (timeWrapper) {
                timeWrapper.classList.remove('hidden');
            }
            if (monthWrapper) {
                monthWrapper.classList.add('hidden');
            }
        }
    }

    formatAxisLabel(value) {
        const granularity = (this.chartMeta?.granularity || this.chartData?.granularity || 'daily').toLowerCase();
        if (!value) {
            return '-';
        }

        if (granularity === 'monthly') {
            if (typeof value === 'string') {
                return value.slice(0, 7);
            }
            const dateObj = new Date(value);
            if (Number.isNaN(dateObj.getTime())) {
                return '-';
            }
            return `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}`;
        }

        return Utils.formatDate(value, 'MM-DD');
    }

    getTooltipLabel(index) {
        const dates = this.chartData?.dates;
        if (!Array.isArray(dates) || index == null || index < 0 || index >= dates.length) {
            return '-';
        }

        const raw = dates[index];
        const granularity = (this.chartMeta?.granularity || this.chartData?.granularity || 'daily').toLowerCase();

        if (granularity === 'monthly') {
            if (typeof raw === 'string') {
                return raw.slice(0, 7);
            }
            return Utils.formatDate(raw, 'YYYY-MM');
        }

        return Utils.formatDate(raw, 'YYYY-MM-DD');
    }

    formatChartValue(value) {
        if (value == null || value === '-') return '待同步';
        const numeric = typeof value === 'number' ? value : Number(value) || 0;
        switch (this.chartMetric) {
            case 'revenue':
                return Utils.formatCurrency(numeric);
            case 'subscribers':
            case 'views':
                return Utils.formatNumber(numeric);
            default:
                return numeric;
        }
    }

    sanitizeTooltipText(value) {
        if (value === null || value === undefined) {
            return '';
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    normalizeSeriesColor(color) {
        if (!color) {
            return '#2563eb';
        }
        if (typeof color === 'string') {
            return color;
        }
        if (color.colorStops && color.colorStops.length) {
            return color.colorStops[color.colorStops.length - 1]?.color || '#2563eb';
        }
        if (color.color) {
            return color.color;
        }
        return '#2563eb';
    }

    initChart() {
        const chartDom = document.getElementById('main-chart');
        this.chart = echarts.init(chartDom);
        
        // 设置默认图表
        this.updateChart({
            title: { text: '频道数据趋势' },
            tooltip: { 
                trigger: 'item',
                formatter: function(params) {
                    return `${params.seriesName}<br/>
                            日期: ${params.name}<br/>
                            数值: ${params.value}`;
                }
            },
            legend: { data: [] },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                boundaryGap: false,
                data: []
            },
            yAxis: { type: 'value' },
            series: []
        });
    }

    // 打开图表频道筛选弹层
    async openChartChannelFilter() {
        const modal = document.getElementById('channelFilterModal');
        const content = document.getElementById('channelFilterContent');

        if (modal && content) {
            // 设置当前筛选模式为图表
            this.channelFilterMode = 'chart';
            
            const token = window.homeChannelDialog?.open('图表', () => this.closeFilterModal());
            this.filterDialogToken = token;
            const isCurrent = () => !window.homeChannelDialog?.isActive || window.homeChannelDialog.isActive(token);

            // 初始化筛选数据（如果还没有的话）
            try {
                if (!this.allChannelsForFilter || this.allChannelsForFilter.length === 0) {
                    const response = await api.getChannels({ per_page: 1000 });
                    if (!isCurrent()) return;
                    if (!response?.success) throw new Error(response?.message || '频道列表加载失败');
                    this.allChannelsForFilter = response.data.channels || [];
                }
            } catch (error) {
                if (!isCurrent()) return;
                this.showToast('频道列表加载失败，请关闭后重试', 'error');
                this.closeFilterModal();
                return;
            }
            if (!isCurrent()) return;
            
            this.tempSelectedIds = this.selectedChartChannelIds === null
                ? this.getVisibleChartSeries().map(series => series.id)
                : [...this.selectedChartChannelIds];
            
            this.filterSearchTerm = '';
            this.filterSelectedCategory = '';
            this.filterSelectedStatus = 'active';

            // 渲染列表
            this.renderFilterList();
            this.updateFilterCount();
            
            // 绑定弹层内特有的临时事件
            this.bindFilterModalEvents();
            window.homeChannelDialog?.setBusy?.(false);
            window.homeChannelDialog?.focus();
        }
    }

    // 绑定筛选弹层事件
    bindFilterModalEvents() {
        const searchInput = document.getElementById('channelFilterSearch');
        if (searchInput) {
            searchInput.value = '';
            // 移除旧的监听器并添加新的
            const newSearchInput = searchInput.cloneNode(true);
            searchInput.parentNode.replaceChild(newSearchInput, searchInput);
            newSearchInput.addEventListener('input', (e) => {
                this.filterSearchTerm = e.target.value.toLowerCase();
                this.renderFilterList();
            });
        }

        const categorySelect = document.getElementById('categoryFilter');
        if (categorySelect) {
            categorySelect.value = '';
            const newCategorySelect = categorySelect.cloneNode(true);
            categorySelect.parentNode.replaceChild(newCategorySelect, categorySelect);
            newCategorySelect.addEventListener('change', (e) => {
                this.filterSelectedCategory = e.target.value;
                this.renderFilterList();
            });
        }

        const statusSelect = document.getElementById('statusFilter');
        if (statusSelect) {
            statusSelect.value = 'active';
            const newStatusSelect = statusSelect.cloneNode(true);
            statusSelect.parentNode.replaceChild(newStatusSelect, statusSelect);
            newStatusSelect.addEventListener('change', (e) => {
                this.filterSelectedStatus = e.target.value;
                this.renderFilterList();
            });
        }

        // 覆盖全局函数，重定向到当前实例的方法
        window.selectAllChannels = () => this.filterSelectAll();
        window.clearAllChannels = () => this.filterClearAll();
        window.toggleAllChannelSelection = () => this.filterToggleAll();
        window.toggleChannelSelection = (id) => this.filterToggleItem(id);
        window.applyChannelFilter = () => this.applyChartChannelFilter();
        window.closeChannelFilterModal = () => this.closeFilterModal();
    }

    // 渲染筛选列表
    renderFilterList() {
        const listEl = document.getElementById('channelFilterList');
        if (!listEl) return;

        const filtered = this.allChannelsForFilter.filter(channel => {
            const name = (channel.name || channel.channel_name || '').toLowerCase();
            const category = channel.category || '';
            const isActive = channel.is_active;

            const matchesSearch = !this.filterSearchTerm || name.includes(this.filterSearchTerm);
            const matchesCategory = !this.filterSelectedCategory || category === this.filterSelectedCategory;
            const matchesStatus = !this.filterSelectedStatus || 
                (this.filterSelectedStatus === 'active' && isActive) ||
                (this.filterSelectedStatus === 'inactive' && !isActive);

            return matchesSearch && matchesCategory && matchesStatus;
        });

        this.currentFilteredIds = filtered.map(c => c.id);
        if (filtered.length === 0) {
            listEl.innerHTML = '<div class="p-8 text-center text-gray-500">没有找到匹配的频道</div>';
            return;
        }

        const html = filtered.map(channel => {
            const isSelected = this.tempSelectedIds.includes(channel.id);
            const isActive = channel.is_active;
            return `
                <label class="block p-4 hover:bg-gray-50 transition-colors cursor-pointer ${!isActive ? 'opacity-75' : ''}">
                    <div class="flex items-center gap-3">
                        <div class="flex-shrink-0">
                            <input type="checkbox" ${isSelected ? 'checked' : ''} 
                                   class="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
                                   data-channel-id="${channel.id}" onchange="toggleChannelSelection(${channel.id})" aria-label="选择 ${this.sanitizeTooltipText(channel.name || channel.channel_name)}">
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="font-medium ${isActive ? 'text-gray-900' : 'text-gray-500'} truncate">
                                ${this.sanitizeTooltipText(channel.name || channel.channel_name)}
                                ${!isActive ? '<span class="text-xs text-gray-400 ml-2">(已暂停)</span>' : ''}
                            </div>
                            <div class="text-sm text-gray-500 flex items-center gap-2">
                                <span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${isActive ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}">
                                    ${isActive ? '活跃' : '暂停'} • ${this.sanitizeTooltipText(channel.category || '未分类')}
                                </span>
                            </div>
                        </div>
                    </div>
                </label>
            `;
        }).join('');

        listEl.innerHTML = html;
    }

    filterToggleItem(id) {
        const index = this.tempSelectedIds.indexOf(id);
        if (index > -1) {
            this.tempSelectedIds.splice(index, 1);
        } else {
            this.tempSelectedIds.push(id);
        }
        this.renderFilterList();
        this.updateFilterCount();
        document.querySelector(`#channelFilterList input[data-channel-id="${id}"]`)?.focus();
    }

    filterSelectAll() {
        this.currentFilteredIds.forEach(id => {
            if (!this.tempSelectedIds.includes(id)) {
                this.tempSelectedIds.push(id);
            }
        });
        this.renderFilterList();
        this.updateFilterCount();
    }

    filterClearAll() {
        this.tempSelectedIds = [];
        this.renderFilterList();
        this.updateFilterCount();
    }

    filterToggleAll() {
        this.currentFilteredIds.forEach(id => {
            const index = this.tempSelectedIds.indexOf(id);
            if (index > -1) {
                this.tempSelectedIds.splice(index, 1);
            } else {
                this.tempSelectedIds.push(id);
            }
        });
        this.renderFilterList();
        this.updateFilterCount();
    }

    updateFilterCount() {
        const countEl = document.getElementById('selectedChannelCount');
        if (countEl) {
            countEl.textContent = this.tempSelectedIds.length;
        }
    }

    closeFilterModal() {
        if (window.homeChannelDialog) {
            window.homeChannelDialog.close();
            return;
        }
        const modal = document.getElementById('channelFilterModal');
        const content = document.getElementById('channelFilterContent');
        if (modal && content) {
            content.classList.remove('scale-100', 'opacity-100');
            content.classList.add('scale-95', 'opacity-0');
            setTimeout(() => modal.classList.add('hidden'), 300);
        }
        
        // 恢复 data-table.js 可能需要的全局函数（如果它们存在）
        // 这里我们可以选择不恢复，因为 data-table.js 在打开弹窗时会重新绑定它们
    }

    async applyChartChannelFilter() {
        const token = this.filterDialogToken;
        this.selectedChartChannelIds = [...this.tempSelectedIds];
        this.showLoading('正在更新图表...');
        try {
            const loaded = await this.loadChartData();
            this.hideLoading();
            if (loaded === false) return;
            if (window.homeChannelDialog?.isActive && !window.homeChannelDialog.isActive(token)) return;
            this.showToast(`已筛选 ${this.selectedChartChannelIds.length} 个频道`, 'success');
            this.closeFilterModal();
        } catch (error) {
            this.hideLoading();
            console.error('更新图表失败:', error);
        }
    }

    async loadInitialData() {
        try {
            this.showLoading('正在加载数据...');
            
            // 并行加载所有数据
            const [channelsResult, rankingsResult] = await Promise.all([
                this.loadChannels(),
                this.loadRankings()
            ]);

            if (channelsResult) {
                await this.loadChartData();
            }

            this.hideLoading();
        } catch (error) {
            console.error('加载初始数据失败:', error);
            this.hideLoading();
            this.showToast('加载数据失败: ' + error.message, 'error');
        }
    }

    async loadChannels() {
        try {
            const response = await api.getChannels({ per_page: 1000 });
            if (response.success) {
                this.channels = response.data.channels;
                this.allChannelsForFilter = this.channels.filter(channel => channel.is_active);
                return true;
            }
            return false;
        } catch (error) {
            console.error('加载频道列表失败:', error);
            return false;
        }
    }

    async loadRankings() {
        try {
            const response = await api.getAllRankings(10);
            if (response.success) {
                this.updateRankings(response.data);
                return true;
            }
            throw new Error(response.message || '排行榜接口返回失败');
        } catch (error) {
            console.error('加载排行榜失败:', error);
            for (const type of ['daily-revenue', 'monthly-revenue', 'daily-subscriber', 'monthly-subscriber']) {
                this.renderRanking(`${type}-ranking`, [], type.includes('revenue') ? 'revenue' : 'subscriber', '加载失败，请点击刷新重试');
            }
            return false;
        }
    }

    async loadChannelMetadata() {
        const fallbackCategories = [
            { name: '科技', default_cpm: 9.0 },
            { name: '教育', default_cpm: 4.0 },
            { name: '娱乐', default_cpm: 3.0 },
            { name: '转发', default_cpm: 6.0 },
            { name: '反贼', default_cpm: 6.0 },
            { name: '生活', default_cpm: 2.5 },
            { name: '游戏', default_cpm: 3.0 },
            { name: '音乐', default_cpm: 3.0 },
            { name: '美食', default_cpm: 2.0 },
            { name: '旅行', default_cpm: 2.0 },
            { name: '其他', default_cpm: 2.5 }
        ];

        const applyFallback = () => {
            this.channelCategories = fallbackCategories;
            this.channelCategoryDefaults = fallbackCategories.reduce((acc, item) => {
                acc[item.name] = item.default_cpm;
                return acc;
            }, { default: 2.5 });
            this.defaultChannelCpm = this.channelCategoryDefaults.default;
        };

        try {
            const [defaultsResponse, categoriesResponse] = await Promise.all([
                api.getChannelDefaults().catch(() => null),
                api.getChannelCategories().catch(() => null)
            ]);

            if (defaultsResponse?.success && defaultsResponse.data?.defaults) {
                this.channelCategoryDefaults = {};
                Object.entries(defaultsResponse.data.defaults).forEach(([key, value]) => {
                    const numeric = Number(value);
                    if (!Number.isNaN(numeric)) {
                        this.channelCategoryDefaults[key] = numeric;
                    }
                });
                const defaultValue = Number(defaultsResponse.data.default_cpm);
                if (!Number.isNaN(defaultValue)) {
                    this.defaultChannelCpm = defaultValue;
                } else if (typeof this.channelCategoryDefaults.default === 'number') {
                    this.defaultChannelCpm = this.channelCategoryDefaults.default;
                } else {
                    this.defaultChannelCpm = 2.5;
                }
            } else {
                applyFallback();
            }

            if (categoriesResponse?.success && Array.isArray(categoriesResponse.data?.categories) && categoriesResponse.data.categories.length) {
                this.channelCategories = categoriesResponse.data.categories.map((category) => {
                    const rawDefault = category.default_cpm;
                    const numericDefault = Number(rawDefault);
                    const fallbackDefault = this.channelCategoryDefaults[category.name] ?? this.defaultChannelCpm;
                    const resolvedDefault = !Number.isNaN(numericDefault) ? numericDefault : fallbackDefault;
                    this.channelCategoryDefaults[category.name] = resolvedDefault;
                    return {
                        name: category.name,
                        count: category.count ?? 0,
                        default_cpm: resolvedDefault
                    };
                });
            } else if (!this.channelCategories.length) {
                applyFallback();
            }

            if (typeof this.channelCategoryDefaults.default !== 'number') {
                this.channelCategoryDefaults.default = this.defaultChannelCpm;
            }
        } catch (error) {
            console.error('加载频道分类信息失败:', error);
            applyFallback();
        }

        this.populateChannelCategoryOptions();
        this.updateAddChannelCpm(document.getElementById('channel-category')?.value || '');
        this.updateEditChannelCpm(document.getElementById('edit-channel-category')?.value || '');
    }

    populateChannelCategoryOptions() {
        const addSelect = document.getElementById('channel-category');
        const editSelect = document.getElementById('edit-channel-category');
        const categories = this.channelCategories || [];

        if (addSelect) {
            const current = addSelect.value;
            addSelect.innerHTML = '<option value="">选择分类</option>';
            categories.forEach((category) => {
                const defaultValue = this.getCategoryDefaultCpm(category.name);
                const label = Number.isFinite(defaultValue)
                    ? `${category.name}（$${defaultValue.toFixed(2)}）`
                    : category.name;
                const option = document.createElement('option');
                option.value = category.name;
                option.textContent = label;
                addSelect.appendChild(option);
            });
            if (current && Array.from(addSelect.options).some(option => option.value === current)) {
                addSelect.value = current;
            } else {
                addSelect.value = '';
            }
        }

        if (editSelect) {
            const current = editSelect.value;
            editSelect.innerHTML = '<option value="">选择分类</option>';
            categories.forEach((category) => {
                const defaultValue = this.getCategoryDefaultCpm(category.name);
                const label = Number.isFinite(defaultValue)
                    ? `${category.name}（$${defaultValue.toFixed(2)}）`
                    : category.name;
                const option = document.createElement('option');
                option.value = category.name;
                option.textContent = label;
                editSelect.appendChild(option);
            });
            if (current && Array.from(editSelect.options).some(option => option.value === current)) {
                editSelect.value = current;
            } else {
                editSelect.value = '';
            }
        }
    }

    getCategoryDefaultCpm(category) {
        if (!category) {
            return this.defaultChannelCpm;
        }
        const value = this.channelCategoryDefaults?.[category];
        if (typeof value === 'number' && !Number.isNaN(value)) {
            return value;
        }
        return this.defaultChannelCpm;
    }

    updateAddChannelCpm(category) {
        const cpmInput = document.getElementById('channel-cpm');
        const hintElement = document.getElementById('channel-cpm-hint');
        const defaultValue = this.getCategoryDefaultCpm(category);

        if (cpmInput) {
            cpmInput.value = defaultValue.toFixed(2);
        }

        if (hintElement) {
            hintElement.textContent = category
                ? `分类默认值：$${defaultValue.toFixed(2)}`
                : `默认CPM：$${defaultValue.toFixed(2)}`;
        }
    }

    updateEditChannelCpm(category, options = {}) {
        const cpmInput = document.getElementById('edit-channel-cpm');
        const hintElement = document.getElementById('edit-channel-cpm-hint');
        const defaultValue = this.getCategoryDefaultCpm(category);
        const existingValue = (options && typeof options.existingValue === 'number') ? options.existingValue : undefined;

        if (cpmInput) {
            const valueToUse = (typeof existingValue === 'number' && !Number.isNaN(existingValue))
                ? existingValue
                : defaultValue;
            cpmInput.value = valueToUse.toFixed(2);
        }

        if (hintElement) {
            if (typeof existingValue === 'number' && Math.abs(existingValue - defaultValue) > 0.001) {
                hintElement.textContent = `分类默认值：$${defaultValue.toFixed(2)}（当前：$${existingValue.toFixed(2)}）`;
            } else {
                hintElement.textContent = category
                    ? `分类默认值：$${defaultValue.toFixed(2)}`
                    : `默认CPM：$${defaultValue.toFixed(2)}`;
            }
        }
    }

    updateRankings(data) {
        // 更新日收入排行
        this.renderRanking('daily-revenue-ranking', data.daily_revenue?.rankings || [], 'revenue');
        
        // 更新月收入排行
        this.renderRanking('monthly-revenue-ranking', data.monthly_revenue?.rankings || [], 'revenue');
        
        // 更新日订阅排行
        this.renderRanking('daily-subscriber-ranking', data.daily_subscriber?.rankings || [], 'subscriber');
        
        // 更新月订阅排行
        this.renderRanking('monthly-subscriber-ranking', data.monthly_subscriber?.rankings || [], 'subscriber');
    }

    renderRanking(containerId, rankings, type, emptyMessage = '所选周期暂无数据') {
        const container = document.getElementById(containerId);
        if (!container) return;
        const rankingType = containerId.replace('-ranking', '').replaceAll('-', '_');
        const detailLink = `<a href="rankings.html?type=${rankingType}" class="ranking-more">查看全部 <i class="fas fa-arrow-right text-xs" aria-hidden="true"></i></a>`;

        if (!rankings || rankings.length === 0) {
            container.innerHTML = `
                <div class="text-center text-gray-400 py-3">
                    <i class="fas fa-chart-line text-2xl mb-2 opacity-50"></i>
                    <div>${this.sanitizeTooltipText(emptyMessage)}</div><div class="text-xs mt-1">缺失数据不会计作零</div>
                </div>
            ` + detailLink;
            return;
        }

        const getRankIcon = (rank) => {
            switch(rank) {
                case 1: return '<i class="fas fa-trophy text-yellow-500"></i>';
                case 2: return '<i class="fas fa-medal text-gray-400"></i>';
                case 3: return '<i class="fas fa-award text-orange-400"></i>';
                default: return `<span class="text-gray-600 font-medium">${rank}</span>`;
            }
        };

        const getTrendIcon = (value, type) => {
            if (type === 'subscriber') {
                const numValue = parseInt(value.toString().replace(/[,+]/g, ''));
                if (numValue > 0) return '<i class="fas fa-arrow-trend-up text-green-500 text-xs ml-1"></i>';
                if (numValue < 0) return '<i class="fas fa-arrow-trend-down text-red-500 text-xs ml-1"></i>';
            }
            return '';
        };

        const html = rankings.slice(0, 10).map((item, index) => {
            const rank = index + 1;
            const value = type === 'revenue' 
                ? Utils.formatCurrency(item.revenue ?? item.total_revenue ?? 0)
                : Utils.formatChange(item.subscriber_change ?? item.total_subscriber_change ?? 0, true);

            const isTop3 = rank <= 3;
            const cardClass = isTop3 
                ? 'bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-100 hover:from-blue-100 hover:to-indigo-100' 
                : 'bg-white hover:bg-gray-50 border border-gray-100';
            
            const valueClass = rank === 1 
                ? 'text-blue-600 font-bold' 
                : rank <= 3 
                    ? 'text-gray-700 font-semibold' 
                    : 'text-gray-600 font-medium';

            const channelClass = rank === 1 ? 'text-gray-800 font-medium' : 'text-gray-700';

            // 构建跳转链接，将频道信息作为参数传递
            const videoPageUrl = `videos.html?channel_id=${item.channel_id}&channel_name=${encodeURIComponent(item.channel_name)}`;

            return `
                <a href="${videoPageUrl}" class="ranking-item ${cardClass} rounded-lg p-3 transition-all duration-200 cursor-pointer hover:shadow-md transform hover:-translate-y-0.5 block">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2 min-w-0 flex-1">
                            <div class="w-6 h-8 shrink-0 flex items-center justify-center">
                                ${getRankIcon(rank)}
                            </div>
                            <span class="${channelClass} truncate" title="${this.sanitizeTooltipText(item.channel_name)}">
                                ${this.sanitizeTooltipText(item.channel_name)}
                            </span>
                        </div>
                        <div class="flex items-center shrink-0 ml-2 tabular-nums">
                            <span class="${valueClass}">${value}</span>
                            ${getTrendIcon(value, type)}
                        </div>
                    </div>
                </a>
            `;
        }).join('');

        container.innerHTML = `<div class="space-y-2">${html}</div>${detailLink}`;
    }

    async loadChartData() {
        const requestId = this.chartRequestId = (this.chartRequestId || 0) + 1;
        this.chartError = null;
        try {
            const params = {
                time_range: this.currentTimeRange,
                group: this.chartGranularity
            };

            if (this.chartGranularity === 'monthly') {
                params.months = this.currentMonthRange;
            }
            
            // 如果有选中的频道，则加入筛选参数
            if (this.selectedChartChannelIds && this.selectedChartChannelIds.length > 0) {
                params.channel_ids = this.selectedChartChannelIds.join(',');
            }

            const response = await api.getChartData(this.chartMetric, params);
            if (requestId !== this.chartRequestId) return false;

            if (response.success && response.data) {
                const { chart_data: chartData, ...meta } = response.data;
                this.chartData = chartData || {};
                this.chartMeta = meta || {};
                this.refreshChart();
                return true;
            } else {
                this.chartData = { dates: [], series: [] };
                this.chartMeta = {};
                this.chartError = response.message || '所选频道暂无可用数据';
                this.refreshChart();
                return false;
            }
        } catch (error) {
            if (requestId !== this.chartRequestId) return false;
            console.error('加载图表数据失败:', error);
            this.chartData = { dates: [], series: [] };
            this.chartMeta = {};
            this.chartError = '加载失败，请调整筛选或点击刷新重试';
            this.refreshChart();
            this.showToast('加载图表数据失败: ' + (error.message || '服务器内部错误'), 'error');
            return false;
        }
    }

    getVisibleChartSeries() {
        const series = (this.chartData.series || []).map(item => ({
            ...item, id: item.id ?? (this.allChannelsForFilter || this.channels).find(channel => channel.channel_id === item.channel_id)?.id,
        }));
        if (this.selectedChartChannelIds === null) {
            const last = item => [...(item.data || [])].reverse().find(value => value != null) ?? -Infinity;
            return [...series].sort((a, b) => last(b) - last(a)).slice(0, 5);
        }
        return series.filter(item => this.selectedChartChannelIds.includes(item.id));
    }

    refreshChart() {
        if (!this.chart || !this.chartData || !Array.isArray(this.chartData.dates)) {
            return;
        }

        const isPieChart = this.currentChartType === 'pie';
        const visibleSeries = this.getVisibleChartSeries();
        const legendData = visibleSeries.map(series => series.name);
        const metricTitle = { revenue: '估算收入（USD）', subscribers: '订阅人数', views: '累计播放量' }[this.chartMetric];
        const status = document.getElementById('chart-data-status');
        if (status) {
            const latest = this.chartMeta.latest_data_date;
            const freshness = 'latest_data_date' in this.chartMeta
                ? (latest ? `范围内最新快照 ${latest}` : '所选范围暂无快照')
                : '后端尚未提供快照日期，请重启后端以启用缺失值识别';
            status.textContent = `${this.selectedChartChannelIds === null ? '自动 Top 5' : '自选频道'} · 显示 ${visibleSeries.length} 个频道 · ${freshness}${'latest_data_date' in this.chartMeta ? ' · 缺失数据留空，不计为 0' : ''}${this.chartMeta.pending_channels ? ` · ${this.chartMeta.pending_channels} 个频道今日待同步` : ''}`;
            if (this.chartError) status.textContent = this.chartError;
        }

        const tooltipFormatter = (params) => {
            if (Array.isArray(params)) {
                if (params.length === 0) {
                    return '';
                }

                const dataIndex = params[0]?.dataIndex ?? 0;
                const label = this.sanitizeTooltipText(this.getTooltipLabel(dataIndex));

                const entries = params.map((item) => ({
                    name: this.sanitizeTooltipText(item.seriesName),
                    value: this.formatChartValue(item.value),
                    color: this.normalizeSeriesColor(item.color),
                    rawValue: Number(item.value) || 0
                }));

                entries.sort((a, b) => b.rawValue - a.rawValue);

                const columnCount = entries.length > 20 ? 5 : entries.length > 12 ? 4 : entries.length > 6 ? 3 : entries.length > 3 ? 2 : 1;
                const itemsPerColumn = Math.ceil(entries.length / columnCount);
                const columns = [];

                for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
                    const start = columnIndex * itemsPerColumn;
                    const columnEntries = entries.slice(start, start + itemsPerColumn);
                    if (!columnEntries.length) {
                        continue;
                    }

                    const columnHtml = columnEntries.map((entry) => (
                        `<div style="display:flex;align-items:center;gap:12px;font-size:12px;line-height:1.45;color:#1f2937;padding-bottom:8px;border-bottom:1px dashed rgba(148,163,184,0.35);">` +
                            `<span style="width:8px;height:8px;border-radius:50%;background:${entry.color};flex-shrink:0;"></span>` +
                            `<span style="flex:1;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${entry.name}</span>` +
                            `<span style="white-space:nowrap;font-weight:600;color:${entry.rawValue >= 0 ? '#0f172a' : '#ef4444'};">${entry.value}</span>` +
                        `</div>`
                    )).join('');

                    columns.push(`<div style="display:flex;flex-direction:column;gap:8px;min-width:220px;">${columnHtml}</div>`);
                }

                return `
                    <div style="min-width:320px;max-width:760px;padding-bottom:6px;">
                        <div style="font-weight:600;color:#0f172a;margin-bottom:14px;font-size:13px;">${label}</div>
                        <div style="display:flex;flex-wrap:wrap;gap:18px 20px;">${columns.join('')}</div>
                    </div>
                `;
            }

            if (isPieChart) {
                const name = this.sanitizeTooltipText(params.name);
                const percent = typeof params.percent === 'number' ? `${params.percent}%` : '';
                const percentSuffix = percent ? ` (${percent})` : '';
                return `${name}<br/>最新数值: ${this.formatChartValue(params.value)}${percentSuffix}`;
            }

            const dataIndex = params?.dataIndex ?? 0;
            const label = this.sanitizeTooltipText(this.getTooltipLabel(dataIndex));
            const seriesName = this.sanitizeTooltipText(params.seriesName);
            return `${seriesName}<br/>日期: ${label}<br/>数值: ${this.formatChartValue(params.value)}`;
        };

        const option = {
            animation: false,
            title: { text: isPieChart ? `${metricTitle} · 各频道最新有效值` : metricTitle, textStyle: { fontSize: 14 } },
            tooltip: {
                trigger: isPieChart ? 'item' : 'axis',
                formatter: tooltipFormatter,
                renderMode: 'html',
                confine: true,
                extraCssText: 'max-height:540px;max-width:780px;overflow:auto;border-radius:18px;border:1px solid rgba(148,163,184,0.45);box-shadow:0 20px 48px rgba(15,23,42,0.2);padding:18px 22px 20px;background:rgba(255,255,255,0.98);color:#1f2937;'
            },
            legend: {
                type: 'scroll', top: 32, left: 8, right: 8,
                data: legendData
            },
            grid: {
                left: '3%',
                right: '4%',
                top: 90,
                bottom: '3%',
                containLabel: true
            }
        };

        if (isPieChart) {
            // 取每个系列最后一个非空值用于饼图展示
            const pieData = visibleSeries.filter(series => series.data.some(value => value != null)).map(series => {
                const dataPoints = Array.isArray(series.data) ? [...series.data] : [];
                let value = 0;
                while (dataPoints.length) {
                    const candidate = dataPoints.pop();
                    if (candidate != null) {
                        value = Number(candidate) || 0;
                        break;
                    }
                }
                return {
                    name: series.name,
                    value
                };
            });

            option.series = [
                {
                    name: '占比',
                    type: 'pie',
                    radius: '60%',
                    data: pieData,
                    label: {
                        formatter: ({ name, value, percent }) => `${name}\n${this.formatChartValue(value)} (${percent}%)`
                    }
                }
            ];
        } else {
            option.xAxis = {
                type: 'category',
                boundaryGap: this.currentChartType === 'bar',
                data: this.chartData.dates,
                axisLabel: {
                    formatter: (value) => this.formatAxisLabel(value)
                }
            };

            option.yAxis = { type: 'value', name: this.chartMetric === 'revenue' ? 'USD' : (this.chartMetric === 'subscribers' ? '人' : '次') };

            option.series = visibleSeries.map(series => ({
                name: series.name,
                type: this.currentChartType,
                connectNulls: false,
                data: series.data
            }));
        }

        this.chart.setOption(option, true);
    }

    // 图表类型切换
    switchChartType(type) {
        this.currentChartType = type;

        ['line', 'bar', 'pie'].forEach((chartType) => {
            const button = document.getElementById(`chart-type-${chartType}`);
            if (button) {
                button.className = chartType === type ? ACTIVE_BUTTON_CLASS : INACTIVE_BUTTON_CLASS;
            }
        });

        this.refreshChart();
    }

    async switchGranularity(granularity) {
        const normalized = (granularity || '').toLowerCase();
        const target = normalized === 'monthly' ? 'monthly' : 'daily';

        if (this.chartGranularity === target) {
            this.updateGranularityUI();
            return;
        }

        this.chartGranularity = target;
        this.updateGranularityUI();

        const loadingMessage = target === 'monthly' ? '正在切换按月视图...' : '正在切换按日视图...';
        this.showLoading(loadingMessage);

        try {
            await this.loadChartData();
            this.hideLoading();
        } catch (error) {
            console.error('切换图表粒度失败:', error);
            this.hideLoading();
            this.showToast('切换图表粒度失败', 'error');
        }
    }

    // 模态框控制
    openAddChannelModal() {
        const modal = document.getElementById('addChannelModal');
        const content = document.getElementById('modalContent');
        
        this.populateChannelCategoryOptions();
        this.updateAddChannelCpm(document.getElementById('channel-category')?.value || '');

        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        
        // 动画效果
        setTimeout(() => {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }, 10);
    }

    closeAddChannelModal() {
        const modal = document.getElementById('addChannelModal');
        const content = document.getElementById('modalContent');
        
        // 动画效果
        content.classList.remove('scale-100', 'opacity-100');
        content.classList.add('scale-95', 'opacity-0');
        
        setTimeout(() => {
            modal.classList.add('hidden');
            document.body.style.overflow = 'auto';
            document.getElementById('addChannelForm').reset();
            document.getElementById('channel-preview').classList.add('hidden');
            this.updateAddChannelCpm('');
        }, 300);
    }

    // 预览频道
    async previewChannel() {
        const url = document.getElementById('channel-url').value.trim();
        if (!url) {
            this.showToast('请输入频道URL', 'warning');
            return;
        }

        if (!Utils.isYouTubeURL(url)) {
            this.showToast('请输入有效的YouTube频道URL', 'warning');
            return;
        }

        try {
            this.showLoading('正在预览频道...');
            const response = await api.previewChannel(url);
            
            if (response.success) {
                const preview = document.getElementById('channel-preview');
                const content = document.getElementById('preview-content');
                
                content.innerHTML = `
                    <div class="text-sm">
                        <div><strong>频道名称:</strong> ${response.data.name}</div>
                        <div><strong>频道ID:</strong> ${response.data.channel_id}</div>
                        <div><strong>订阅数:</strong> ${Utils.formatNumber(response.data.subscriber_count)}</div>
                        <div><strong>视频数:</strong> ${Utils.formatNumber(response.data.video_count)}</div>
                    </div>
                `;
                
                preview.classList.remove('hidden');
                
                // 自动填充表单
                document.getElementById('channel-name').value = response.data.name;
            } else {
                this.showToast('预览失败: ' + response.message, 'error');
            }
            
            this.hideLoading();
        } catch (error) {
            this.hideLoading();
            this.showToast('预览失败: ' + error.message, 'error');
        }
    }

    // 添加频道
    async addChannel() {
        const url = document.getElementById('channel-url').value.trim();
        const name = document.getElementById('channel-name').value.trim();
        const category = (document.getElementById('channel-category')?.value || '').trim();
        const cpmValue = Number(document.getElementById('channel-cpm')?.value);

        if (!url) {
            this.showToast('请输入频道URL', 'warning');
            return;
        }

        try {
            this.showLoading('正在添加频道...');
            const options = {};
            if (name) {
                options.name = name;
            }
            if (category) {
                options.category = category;
            }
            if (!Number.isNaN(cpmValue)) {
                options.cpm = cpmValue;
            }

            const response = await api.addChannel(url, options);
            
            if (response.success) {
                this.showToast('频道添加成功', 'success');
                this.closeAddChannelModal();
                await this.loadInitialData(); // 重新加载数据
                await this.loadChannelMetadata();
            } else {
                this.showToast('添加失败: ' + response.message, 'error');
            }
            
            this.hideLoading();
        } catch (error) {
            this.hideLoading();
            this.showToast('添加失败: ' + error.message, 'error');
        }
    }

    // 刷新数据
    async refreshData() {
        await this.loadInitialData();
        await window.dataTableInstance?.loadTableData();
        this.showToast('数据已刷新', 'success');
    }

    // 导出数据
    async exportData() {
        try {
            this.showLoading('正在准备导出数据...');
            await api.exportData({
                format: 'csv',
                days: this.currentTimeRange
            });
            this.showToast('数据导出成功', 'success');
            this.hideLoading();
        } catch (error) {
            this.hideLoading();
            this.showToast('导出失败: ' + error.message, 'error');
        }
    }

    // 手动依次执行首页的三项数据采集任务，不改变后台计划任务。
    async runHomepageSync() {
        if (this.homepageSyncRunning) return;

        this.homepageSyncRunning = true;
        const button = document.getElementById('home-run-sync-btn');
        const label = document.getElementById('home-run-sync-label');
        const status = document.getElementById('home-run-sync-status');
        const originalLabel = label?.textContent || '执行三项同步';
        const summaries = [];
        let hasIssues = false;

        if (button) {
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.classList.add('opacity-60', 'cursor-not-allowed');
        }

        const updateStatus = (text, state = 'running') => {
            if (!status) return;
            status.hidden = false;
            status.textContent = text;
            status.dataset.state = state;
        };

        const tasks = [
            {
                name: 'B站充电人数采集',
                run: () => api.post('/bilibili-intelligence/charging-dashboard/sync', {}),
                summarize: data => {
                    const synced = Number(data.synced) || 0;
                    const failed = Number(data.failed) || 0;
                    return {
                        text: `B站充电人数采集：${synced} 个成功${failed ? `，${failed} 个失败` : ''}`,
                        hasIssues: failed > 0,
                    };
                },
            },
            {
                name: 'YouTube 频道数据同步',
                run: () => api.syncAllChannels({ use_parallel: true, max_workers: 5, active_only: true }),
                summarize: data => {
                    const succeeded = Number(data.success_count) || 0;
                    const failed = Number(data.failed_count) || 0;
                    return {
                        text: `YouTube 频道数据同步：${succeeded} 个成功${failed ? `，${failed} 个失败` : ''}`,
                        hasIssues: failed > 0,
                    };
                },
            },
            {
                name: 'YouTube 视频同步',
                run: () => api.syncVideos({ days_back: 10, use_optimization: true, max_workers: 5 }),
                summarize: data => {
                    const processed = Number(data.processed) || 0;
                    const added = Number(data.new_videos) || 0;
                    const updated = Number(data.updated_videos) || 0;
                    const errors = Number(data.errors) || 0;
                    return {
                        text: `YouTube 视频同步：处理 ${processed} 个频道，新增 ${added} 个，更新 ${updated} 个${errors ? `，${errors} 个错误` : ''}`,
                        hasIssues: errors > 0,
                    };
                },
            },
        ];

        try {
            for (const [index, task] of tasks.entries()) {
                const progress = `${index + 1}/3`;
                if (label) label.textContent = `${progress} ${task.name}…`;
                updateStatus(`正在执行 ${progress}：${task.name}…`);

                try {
                    const response = await task.run();
                    if (!response?.success) {
                        throw new Error(response?.message || '接口返回失败');
                    }
                    const result = task.summarize(response.data || {});
                    summaries.push(result.text);
                    hasIssues ||= result.hasIssues;
                } catch (error) {
                    hasIssues = true;
                    summaries.push(`${task.name}：失败（${error.message || '未知错误'}）`);
                }
            }

            const refreshers = [
                () => this.loadInitialData(),
                () => window.dataTableInstance?.loadTableData(),
                () => window.bilibiliChargingDashboard?.load(),
            ];
            await Promise.allSettled(refreshers.map(refresh => Promise.resolve().then(refresh)));

            updateStatus(`${hasIssues ? '执行完成（部分失败）' : '执行完成'}\n${summaries.join('\n')}`, hasIssues ? 'warning' : 'success');
        } finally {
            this.homepageSyncRunning = false;
            if (button) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
                button.classList.remove('opacity-60', 'cursor-not-allowed');
            }
            if (label) label.textContent = originalLabel;
        }
    }

    // 同步所有频道数据
    async syncAllChannels() {
        this.openSyncOptionsModal();
    }

    openSyncOptionsModal() {
        const today = new Date().toISOString().slice(0, 10);
        const existing = document.getElementById('syncOptionsModal');
        if (existing) {
            existing.remove();
        }

        const modal = document.createElement('div');
        modal.id = 'syncOptionsModal';
        modal.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4';
        modal.innerHTML = `
            <div class="bg-white rounded-2xl shadow-strong max-w-lg w-full overflow-hidden">
                <div class="p-6 border-b border-gray-100 bg-gradient-to-r from-blue-50 to-white">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 bg-blue-100 text-blue-600 rounded-xl flex items-center justify-center">
                                <i class="fas fa-cloud-download-alt"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-bold text-gray-900">同步频道数据</h3>
                                <p class="text-sm text-gray-500">从 YouTube 获取频道最新快照</p>
                            </div>
                        </div>
                        <button type="button" onclick="document.getElementById('syncOptionsModal').remove()" class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-white">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
                <div class="p-6 space-y-5">
                    <div>
                        <label class="block text-xs font-bold text-gray-500 uppercase tracking-wide mb-2">同步日期</label>
                        <input id="sync-target-date" type="date" value="${today}" class="w-full px-3 py-2.5 border border-gray-300 rounded-button focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none">
                    </div>
                    <div>
                        <div class="flex items-center justify-between mb-2">
                            <label class="block text-xs font-bold text-gray-500 uppercase tracking-wide">并行线程</label>
                            <span id="sync-workers-label" class="text-xs font-bold text-blue-600">5 线程</span>
                        </div>
                        <input id="sync-max-workers" type="range" min="1" max="10" value="5" class="w-full" oninput="document.getElementById('sync-workers-label').textContent = this.value + ' 线程'">
                        <p class="mt-2 text-xs text-gray-500">线程越高越快，但会更集中消耗 YouTube API 配额。</p>
                    </div>
                    <label class="flex items-center gap-3 p-3 bg-gray-50 rounded-xl border border-gray-100 cursor-pointer">
                        <input id="sync-active-only" type="checkbox" checked class="w-4 h-4 text-blue-600 rounded">
                        <div>
                            <div class="text-sm font-bold text-gray-800">只同步启用频道</div>
                            <div class="text-xs text-gray-500">跳过已禁用频道，减少无效请求</div>
                        </div>
                    </label>
                    <div class="rounded-xl bg-amber-50 border border-amber-100 p-3 text-xs text-amber-700">
                        同步会写入当天频道数据。若今天已经同步过，会更新对应日期的数据。
                    </div>
                </div>
                <div class="px-6 py-4 bg-gray-50 flex justify-end gap-3">
                    <button type="button" onclick="document.getElementById('syncOptionsModal').remove()" class="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-button font-medium">取消</button>
                    <button type="button" onclick="app.runChannelSyncFromModal()" class="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-button font-bold shadow-sm">
                        开始同步
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => {
            if (event.target.id === 'syncOptionsModal') {
                modal.remove();
            }
        });
    }

    async runChannelSyncFromModal() {
        const modal = document.getElementById('syncOptionsModal');
        const targetDate = document.getElementById('sync-target-date')?.value;
        const maxWorkers = parseInt(document.getElementById('sync-max-workers')?.value || '5', 10);
        const activeOnly = document.getElementById('sync-active-only')?.checked !== false;
        if (modal) {
            modal.remove();
        }

        await this.executeChannelSync({
            date: targetDate,
            max_workers: maxWorkers,
            active_only: activeOnly
        });
    }

    async executeChannelSync(options = {}) {
        try {
            const syncButton = document.getElementById('sync-all-btn');
            if (syncButton) {
                syncButton.disabled = true;
                syncButton.classList.add('opacity-60', 'cursor-not-allowed');
                syncButton.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>同步中';
            }

            this.showLoading(`正在使用 ${options.max_workers || 5} 线程同步频道数据...`);
            const response = await api.syncAllChannels({
                use_parallel: true,
                max_workers: options.max_workers || 5,
                active_only: options.active_only !== false,
                date: options.date || undefined
            });
            
            if (response.success) {
                const data = response.data || {};
                const duration = typeof data.duration_seconds === 'number' ? `，耗时 ${data.duration_seconds}s` : '';
                const workerInfo = data.max_workers ? `（${data.max_workers} 线程）` : '';
                this.showToast(
                    `同步完成${workerInfo}！成功: ${data.success_count || 0}, 失败: ${data.failed_count || 0}${duration}`, 
                    (data.failed_count || 0) > 0 ? 'warning' : 'success'
                );
                this.showSyncResultModal(data);
                
                // 自动刷新页面数据
                await this.loadInitialData();
            } else {
                this.showToast('同步失败: ' + response.message, 'error');
            }
            
            this.hideLoading();
        } catch (error) {
            this.hideLoading();
            this.showToast('同步失败: ' + error.message, 'error');
        } finally {
            const syncButton = document.getElementById('sync-all-btn');
            if (syncButton) {
                syncButton.disabled = false;
                syncButton.classList.remove('opacity-60', 'cursor-not-allowed');
                syncButton.innerHTML = '<i class="fas fa-cloud-download-alt mr-2"></i>同步';
            }
        }
    }

    showSyncResultModal(data = {}) {
        const existing = document.getElementById('syncResultModal');
        if (existing) {
            existing.remove();
        }

        const results = Array.isArray(data.results) ? data.results : [];
        const failedResults = results.filter(item => !item.success);
        const successResults = results.filter(item => item.success);
        const detailRows = (failedResults.length > 0 ? failedResults : successResults.slice(0, 8)).map(item => `
            <div class="flex items-start justify-between gap-3 py-2 border-b border-gray-100 last:border-0">
                <div class="min-w-0">
                    <div class="text-sm font-semibold text-gray-800 truncate">${this.escapeHtml(item.channel_name || '未知频道')}</div>
                    <div class="text-xs text-gray-500 mt-1">${this.escapeHtml(item.message || '-')}</div>
                </div>
                <span class="shrink-0 text-xs font-bold px-2 py-1 rounded-full ${item.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}">
                    ${item.success ? '成功' : '失败'}
                </span>
            </div>
        `).join('');

        const modal = document.createElement('div');
        modal.id = 'syncResultModal';
        modal.className = 'fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4';
        modal.innerHTML = `
            <div class="bg-white rounded-2xl shadow-strong max-w-2xl w-full overflow-hidden">
                <div class="p-6 border-b border-gray-100">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-lg font-bold text-gray-900">同步结果</h3>
                            <p class="text-sm text-gray-500 mt-1">共 ${data.total_channels || 0} 个频道，成功 ${data.success_count || 0} 个，失败 ${data.failed_count || 0} 个</p>
                        </div>
                        <button onclick="document.getElementById('syncResultModal').remove()" class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-50">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
                <div class="p-6">
                    <div class="grid grid-cols-3 gap-3 mb-5">
                        <div class="rounded-xl bg-blue-50 p-4">
                            <div class="text-xs font-bold text-blue-500">总频道</div>
                            <div class="text-2xl font-black text-blue-700">${data.total_channels || 0}</div>
                        </div>
                        <div class="rounded-xl bg-green-50 p-4">
                            <div class="text-xs font-bold text-green-500">成功</div>
                            <div class="text-2xl font-black text-green-700">${data.success_count || 0}</div>
                        </div>
                        <div class="rounded-xl bg-red-50 p-4">
                            <div class="text-xs font-bold text-red-500">失败</div>
                            <div class="text-2xl font-black text-red-700">${data.failed_count || 0}</div>
                        </div>
                    </div>
                    <div class="rounded-xl border border-gray-100 overflow-hidden">
                        <div class="px-4 py-3 bg-gray-50 text-xs font-bold text-gray-500">
                            ${failedResults.length > 0 ? '失败明细' : '成功样例'}
                        </div>
                        <div class="px-4 max-h-72 overflow-y-auto">
                            ${detailRows || '<div class="py-6 text-center text-gray-400 text-sm">暂无明细</div>'}
                        </div>
                    </div>
                </div>
                <div class="px-6 py-4 bg-gray-50 flex justify-end">
                    <button onclick="document.getElementById('syncResultModal').remove()" class="px-5 py-2 bg-gray-900 text-white rounded-button font-bold">知道了</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
    }

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // 工具方法
    updateChart(option) {
        if (this.chart) {
            this.chart.setOption(option, true);
            }

    }

    showLoading(message = '正在加载...') {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            this.loadingCount += 1;
            statusText.textContent = message;
            statusBar.classList.remove('hidden');
        }
    }

    hideLoading() {
        const statusBar = document.getElementById('status-bar');
        if (statusBar) {
            this.loadingCount = Math.max(0, this.loadingCount - 1);
            if (this.loadingCount === 0) {
                statusBar.classList.add('hidden');
            }
        }
    }

    showToast(message, type = 'info', duration = 3000) {
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

    updateDefaultCPM(cpm) {
        // 更新弹层中的CPM默认值
        const channelCpmElement = document.getElementById('channel-cpm');
        if (channelCpmElement) {
            channelCpmElement.value = cpm;
        }
        console.log('默认CPM已更新为:', cpm);
        this.showToast(`默认CPM已设置为 $${cpm}`, 'success');
    }

    // 显示用户信息
    displayUserInfo() {
        const user = api.getCurrentUser();
        if (!user) return;

        // 更新页面中的用户名显示
        const userNameElements = document.querySelectorAll('.user-name');
        userNameElements.forEach(element => {
            element.textContent = user.display_name || user.username;
        });

        // 更新用户头像
        const userAvatarElements = document.querySelectorAll('.user-avatar');
        userAvatarElements.forEach(element => {
            const firstLetter = (user.display_name || user.username).charAt(0).toUpperCase();
            element.textContent = firstLetter;
        });

        // 添加登出按钮事件监听
        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => this.handleLogout());
        }

        console.log('当前用户:', user.username);
    }

    // 处理登出
    async handleLogout() {
        try {
            // 显示确认对话框
            if (!confirm('确定要退出登录吗？')) {
                return;
            }

            // 显示加载状态
            this.showLoading('正在退出登录...');

            // 调用登出API
            await api.logout();

            // 清除本地数据
            api.clearAuth();

            this.hideLoading();
            this.showToast('已成功退出登录', 'success');

            // 短暂延迟后跳转到登录页
            setTimeout(() => {
                window.location.href = 'login.html';
            }, 1000);

        } catch (error) {
            console.error('登出失败:', error);
            this.hideLoading();
            
            // 即使API失败也清除本地认证信息
            api.clearAuth();
            this.showToast('退出登录', 'info');
            
            setTimeout(() => {
                window.location.href = 'login.html';
            }, 1000);
        }
    }

    // 检查认证状态
    async checkAuthStatus() {
        try {
            console.log('🔐 [Auth] 开始验证认证状态...');
            
            const token = localStorage.getItem('access_token');
            if (!token) {
                console.log('❌ [Auth] 没有找到token');
                return false;
            }
            
            console.log('🔐 [Auth] 找到token，验证有效性...');
            const response = await api.verifyToken();
            
            console.log('🔐 [Auth] 验证响应:', response);
            
            if (response && response.success && response.data && response.data.user) {
                console.log('✅ [Auth] Token验证成功，用户:', response.data.user.username);
                // 更新本地用户信息
                localStorage.setItem('user_info', JSON.stringify(response.data.user));
                this.displayUserInfo();
                return true;
            } else {
                console.log('❌ [Auth] Token验证失败，响应格式:', response);
                console.log('❌ [Auth] 清除认证信息');
                api.clearAuth();
                return false;
            }
        } catch (error) {
            console.error('❌ [Auth] 认证验证异常:', error);
            if (error.status === 401) {
                console.log('❌ [Auth] Token已过期，清除认证信息');
                api.clearAuth();
                return false;
            } else {
                console.log('❌ [Auth] 网络错误，跳过认证检查，直接进入应用');
                // 网络错误时假设用户已认证，让应用正常运行
                return true;
            }
        }
    }
}

// 全局函数
function openAddChannelModal() {
    if (window.app) {
        window.app.openAddChannelModal();
    }
}

function closeAddChannelModal() {
    if (window.app) {
        window.app.closeAddChannelModal();
    }
}

function previewChannel() {
    if (window.app) {
        window.app.previewChannel();
    }
}

function addChannel() {
    if (window.app) {
        window.app.addChannel();
    }
}

function switchChartType(type) {
    if (window.app) {
        window.app.switchChartType(type);
    }
}

function switchGranularity(granularity) {
    if (window.app) {
        window.app.switchGranularity(granularity);
    }
}

function refreshData() {
    if (window.app) {
        window.app.refreshData();
    }
}

function exportData() {
    if (window.app) {
        window.app.exportData();
    }
}

function syncAllChannels() {
    if (window.app) {
        window.app.syncAllChannels();
    }
}

function logout() {
    if (window.app) {
        window.app.handleLogout();
    }
}

// 页面切换功能
function switchPage(pageName) {
    // 隐藏所有页面
    const pages = ['dashboard-page', 'channels-page'];
    pages.forEach(pageId => {
        const page = document.getElementById(pageId);
        if (page) {
            page.classList.add('hidden');
        }
    });
    
    // 显示目标页面
    const targetPage = document.getElementById(pageName + '-page');
    if (targetPage) {
        targetPage.classList.remove('hidden');
    }
    
    // 更新导航状态
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => {
        link.className = 'nav-link text-gray-600 hover:text-primary-600 px-3 py-2 rounded-lg hover:bg-gray-50 transition-colors';
    });
    
    const activeNav = document.getElementById('nav-' + pageName);
    if (activeNav) {
        activeNav.className = 'nav-link text-primary-600 font-medium px-3 py-2 rounded-lg bg-primary-50 border border-primary-200';
    }
    
    // 更新面包屑
    const breadcrumb = document.querySelector('nav.flex .text-gray-700');
    if (breadcrumb) {
        breadcrumb.textContent = pageName === 'dashboard' ? '首页' : '频道管理';
    }
    
    // 如果切换到频道管理页面，初始化频道管理
    if (pageName === 'channels') {
        initChannelManagement();
    }
    
    // 防止默认链接行为
    return false;
}

// 应用启动
document.addEventListener('DOMContentLoaded', function() {
    window.app = new YouTubeDataApp();
    console.log('🎉 YouTube数据追踪工具已启动');
});
