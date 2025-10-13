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
        this.channelCategories = [];
        this.channelCategoryDefaults = {};
        this.defaultChannelCpm = 2.5;

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

        // 预加载频道分类与默认CPM
        await this.loadChannelMetadata();

        // 初始化Dashboard（排行榜功能）
        if (typeof Dashboard !== 'undefined') {
            this.dashboard = new Dashboard();
            console.log('✅ [Main] Dashboard已初始化');
        }
        
        // 加载初始数据
        await this.loadInitialData();
        
        console.log('✅ 应用初始化完成');
    }

    bindEvents() {
        // 时间范围选择
        const timeRangeSelect = document.getElementById('time-range');
        if (timeRangeSelect) {
            timeRangeSelect.addEventListener('change', async (e) => {
                this.currentTimeRange = parseInt(e.target.value, 10);

                this.showLoading('正在切换时间范围...');

                try {
                    await Promise.all([
                        this.loadChartData(),
                        this.loadTableData()
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

    async loadInitialData() {
        try {
            this.showLoading('正在加载数据...');
            
            // 并行加载所有数据
            const [channelsResult, rankingsResult] = await Promise.all([
                this.loadChannels(),
                this.loadRankings()
            ]);

            if (channelsResult) {
                await this.loadTableData();
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
            const response = await api.getChannels();
            if (response.success) {
                this.channels = response.data.channels;
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
            return false;
        } catch (error) {
            console.error('加载排行榜失败:', error);
            return false;
        }
    }

    async loadChannelMetadata() {
        const fallbackCategories = [
            { name: '科技', default_cpm: 9.0 },
            { name: '教育', default_cpm: 4.0 },
            { name: '娱乐', default_cpm: 3.0 },
            { name: '转发', default_cpm: 6.0 },
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

    renderRanking(containerId, rankings, type) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!rankings || rankings.length === 0) {
            container.innerHTML = `
                <div class="text-center text-gray-400 py-8">
                    <i class="fas fa-chart-line text-2xl mb-2 opacity-50"></i>
                    <div>暂无数据</div>
                </div>
            `;
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

        const html = rankings.map((item, index) => {
            const rank = index + 1;
            const value = type === 'revenue' 
                ? Utils.formatCurrency(item.revenue || item.total_revenue)
                : Utils.formatChange(item.subscriber_change || item.total_subscriber_change, true);

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
                        <div class="flex items-center gap-3">
                            <div class="w-8 h-8 flex items-center justify-center">
                                ${getRankIcon(rank)}
                            </div>
                            <span class="${channelClass} truncate max-w-[120px]" title="${item.channel_name}">
                                ${item.channel_name}
                            </span>
                        </div>
                        <div class="flex items-center">
                            <span class="${valueClass}">${value}</span>
                            ${getTrendIcon(value, type)}
                        </div>
                    </div>
                </a>
            `;
        }).join('');

        container.innerHTML = `<div class="space-y-3">${html}</div>`;
    }

    async loadTableData() {
        try {
            // 计算日期范围 - 默认从本月1日开始
            const endDate = new Date();
            const startDate = new Date();
            
            // 如果是默认的7天，改为本月1日到今天
            if (this.currentTimeRange === 7) {
                startDate.setDate(1); // 本月1日
            } else {
                startDate.setDate(endDate.getDate() - this.currentTimeRange);
            }
            
            const response = await api.getTableData({
                active_only: true,
                start_date: this.formatDate(startDate),
                end_date: this.formatDate(endDate),
                per_page: 100  // 增加每页返回数量
            });

            if (response.success) {
                // 调试输出
                console.log('=== 首页数据表格调试信息 ===');
                console.log('当前时间范围:', this.currentTimeRange);
                console.log('API响应:', response);
                console.log('表格数据:', response.data);
                
                this.renderTable(response.data);
            }
        } catch (error) {
            console.error('加载表格数据失败:', error);
        }
    }
    
    formatDate(date) {
        return date.toISOString().split('T')[0];
    }

    renderTable(data) {
        const table = document.getElementById('data-table');
        if (!table) return;

        const thead = table.querySelector('thead');
        const tbody = table.querySelector('tbody');

        // 生成表头
        let headerHtml = `
            <tr class="bg-gray-50">
                <th class="sticky left-0 bg-gray-50 px-6 py-4 border-b border-gray-200 font-medium text-gray-500 text-left" rowspan="2">日期</th>
        `;

        data.channels.forEach((channel, index) => {
            const channelClass = index % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';
            headerHtml += `
                <th class="px-6 py-4 border-b border-gray-200 font-medium text-gray-700 text-center ${channelClass} channel-divider" colspan="7">
                    ${channel.channel_name} (CPM: ${channel.cpm})
                </th>
            `;
        });

        headerHtml += '</tr><tr class="bg-gray-50">';

        data.channels.forEach((channel, index) => {
            const channelClass = index % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';
            headerHtml += `
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} channel-divider whitespace-nowrap min-w-[100px]">订阅数</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[110px]">总浏览量</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">视频总量</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增订阅</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增浏览</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">新增视频</th>
                <th class="px-3 py-4 border-b border-gray-200 font-medium text-gray-500 text-right ${channelClass} whitespace-nowrap min-w-[100px]">预估收入</th>
            `;
        });

        headerHtml += '</tr>';
        thead.innerHTML = headerHtml;

        // 生成数据行
        let bodyHtml = '';
        data.rows.forEach(row => {
            bodyHtml += `
                <tr>
                    <td class="sticky left-0 bg-white px-6 py-4 border-b border-gray-200 text-gray-900">
                        ${Utils.formatDate(row.date)}
                    </td>
            `;

            row.channels_data.forEach((channelData, channelIndex) => {
                const subsciberChange = channelData.subscriber_change || 0;
                const viewChange = channelData.view_change || 0;
                const videoChange = channelData.video_change || 0;
                
                // 使用API返回的预估收入
                const estimatedRevenue = channelData.estimated_revenue || 0;
                
                // 频道区分样式
                const channelClass = channelIndex % 2 === 0 ? 'channel-group-even' : 'channel-group-odd';

                bodyHtml += `
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} channel-divider whitespace-nowrap min-w-[100px]">
                        ${Utils.formatNumber(channelData.subscribers)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} whitespace-nowrap min-w-[110px]">
                        ${Utils.formatNumber(channelData.total_views)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 text-gray-900 text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${channelData.video_count || 0}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${subsciberChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(subsciberChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${viewChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(viewChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${videoChange > 0 ? 'text-green-600' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${Utils.formatChange(videoChange, true)}
                    </td>
                    <td class="px-3 py-4 border-b border-gray-200 ${estimatedRevenue > 0 ? 'text-blue-600 font-medium' : 'text-gray-900'} text-right ${channelClass} whitespace-nowrap min-w-[100px]">
                        ${estimatedRevenue > 0 ? Utils.formatCurrency(estimatedRevenue) : '-'}
                    </td>
                `;
            });

            bodyHtml += '</tr>';
        });

        tbody.innerHTML = bodyHtml;
    }

    async loadChartData() {
        try {
            const params = {
                time_range: this.currentTimeRange,
                group: this.chartGranularity
            };

            if (this.chartGranularity === 'monthly') {
                params.months = this.currentMonthRange;
            }

            const response = await api.getChartData(this.chartMetric, params);

            if (response.success && response.data) {
                const { chart_data: chartData, ...meta } = response.data;
                this.chartData = chartData || {};
                this.chartMeta = meta || {};
                this.refreshChart();
            }
        } catch (error) {
            console.error('加载图表数据失败:', error);
            this.showToast('加载图表数据失败', 'error');
        }
    }

    refreshChart() {
        if (!this.chart || !this.chartData || !Array.isArray(this.chartData.dates)) {
            return;
        }

        const isPieChart = this.currentChartType === 'pie';
        const legendData = Array.isArray(this.chartData.series)
            ? this.chartData.series.map(series => series.name)
            : [];

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
            title: { text: '频道数据趋势' },
            tooltip: {
                trigger: isPieChart ? 'item' : 'axis',
                formatter: tooltipFormatter,
                renderMode: 'html',
                confine: true,
                extraCssText: 'max-height:540px;max-width:780px;overflow:auto;border-radius:18px;border:1px solid rgba(148,163,184,0.45);box-shadow:0 20px 48px rgba(15,23,42,0.2);padding:18px 22px 20px;background:rgba(255,255,255,0.98);color:#1f2937;'
            },
            legend: {
                data: legendData
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            }
        };

        if (isPieChart) {
            // 取每个系列最后一个非空值用于饼图展示
            const pieData = (this.chartData.series || []).map(series => {
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

            option.yAxis = { type: 'value' };

            option.series = (this.chartData.series || []).map(series => ({
                name: series.name,
                type: this.currentChartType,
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

    // 同步所有频道数据
    async syncAllChannels() {
        if (!confirm('确定要立即同步所有频道的数据吗？这将从YouTube获取最新数据。')) {
            return;
        }

        try {
            const defaultWorkers = 5;
            const input = prompt('请输入并行线程数 (1-10)', String(defaultWorkers));
            if (input === null) {
                return;
            }

            const parsed = parseInt(input, 10);
            const maxWorkers = Math.min(10, Math.max(1, Number.isNaN(parsed) ? defaultWorkers : parsed));

            this.showLoading(`正在使用 ${maxWorkers} 线程同步所有频道数据...`);
            const response = await api.syncAllChannels({ use_parallel: true, max_workers: maxWorkers });
            
            if (response.success) {
                const data = response.data || {};
                const duration = typeof data.duration_seconds === 'number' ? `，耗时 ${data.duration_seconds}s` : '';
                const workerInfo = data.max_workers ? `（${data.max_workers} 线程）` : '';
                this.showToast(
                    `同步完成${workerInfo}！成功: ${data.success_count || 0}, 失败: ${data.failed_count || 0}${duration}`, 
                    (data.failed_count || 0) > 0 ? 'warning' : 'success'
                );
                
                // 自动刷新页面数据
                await this.loadInitialData();
            } else {
                this.showToast('同步失败: ' + response.message, 'error');
            }
            
            this.hideLoading();
        } catch (error) {
            this.hideLoading();
            this.showToast('同步失败: ' + error.message, 'error');
        }
    }

    // 工具方法
    updateChart(option) {
        if (this.chart) {
            this.chart.setOption(option);
        }
    }

    showLoading(message = '正在加载...') {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            statusText.textContent = message;
            statusBar.classList.remove('hidden');
        }
    }

    hideLoading() {
        const statusBar = document.getElementById('status-bar');
        if (statusBar) {
            statusBar.classList.add('hidden');
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
