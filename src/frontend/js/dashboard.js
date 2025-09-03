// 仪表板模块
class Dashboard {
    constructor() {
        this.chart = null;
        this.refreshInterval = null;
        this.currentChartType = 'revenue';
        
        this.init();
    }

    // 初始化
    init() {
        this.bindEvents();
        this.loadDashboard();
        this.startAutoRefresh();
    }

    // 绑定事件
    bindEvents() {
        // 图表类型切换
        const chartTypeSelect = document.getElementById('chart-type');
        if (chartTypeSelect) {
            chartTypeSelect.addEventListener('change', (e) => {
                this.currentChartType = e.target.value;
                this.loadTrendChart();
            });
        }

        // 页面可见性变化时暂停/恢复自动刷新
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.stopAutoRefresh();
            } else {
                this.startAutoRefresh();
            }
        });
    }

    // 加载仪表板数据
    async loadDashboard() {
        try {
            this.showLoading('正在加载仪表板数据...');
            
            // 并行加载所有数据
            const [summaryData, rankingsData] = await Promise.all([
                api.getSummaryStatistics(),
                api.getAllRankings(10)
            ]);

            // 更新统计卡片
            this.updateStatisticsCards(summaryData.data);

            // 更新排行榜概览
            this.updateRankingsOverview(rankingsData.data);

            // 加载趋势图表
            await this.loadTrendChart();

            this.hideLoading();
            
        } catch (error) {
            console.error('加载仪表板失败:', error);
            this.hideLoading();
            this.showError('加载仪表板数据失败');
        }
    }

    // 更新统计卡片
    updateStatisticsCards(data) {
        // 活跃频道数
        const activeChannelsEl = document.getElementById('active-channels-count');
        if (activeChannelsEl) {
            activeChannelsEl.textContent = Utils.formatNumber(data.active_channels);
        }

        // 今日收入
        const todayRevenueEl = document.getElementById('today-revenue');
        if (todayRevenueEl) {
            todayRevenueEl.textContent = Utils.formatCurrency(data.today.revenue);
        }

        // 今日新增订阅
        const todaySubscribersEl = document.getElementById('today-subscribers');
        if (todaySubscribersEl) {
            todaySubscribersEl.textContent = Utils.formatChange(data.today.subscriber_growth, false);
        }

        // 总记录数
        const totalRecordsEl = document.getElementById('total-records');
        if (totalRecordsEl) {
            totalRecordsEl.textContent = Utils.formatNumber(data.total_records);
        }
    }

    // 更新排行榜概览
    updateRankingsOverview(data) {
        // 日收入排行
        this.renderRankingList('daily-revenue-ranking', data.daily_revenue?.rankings || []);
        
        // 月收入排行
        this.renderRankingList('monthly-revenue-ranking', data.monthly_revenue?.rankings || []);
        
        // 日订阅排行
        this.renderRankingList('daily-subscriber-ranking', data.daily_subscriber?.rankings || []);
        
        // 月订阅排行
        this.renderRankingList('monthly-subscriber-ranking', data.monthly_subscriber?.rankings || []);
    }

    // 渲染排行榜列表
    renderRankingList(containerId, rankings) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!rankings || rankings.length === 0) {
            container.innerHTML = '<div class="text-center text-gray-400 py-4">暂无数据</div>';
            return;
        }

        const html = rankings.map((item, index) => {
            const isRevenue = containerId.includes('revenue');
            const value = isRevenue ? 
                Utils.formatCurrency(item.revenue || item.total_revenue) :
                Utils.formatChange(item.subscriber_change || item.total_subscriber_change, true);

            // 构建跳转链接，将频道信息作为参数传递
            const videoPageUrl = `videos.html?channel_id=${item.channel_id}&channel_name=${encodeURIComponent(item.channel_name)}`;

            return `
                <a href="${videoPageUrl}" class="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 transition-colors group cursor-pointer border border-transparent hover:border-gray-200">
                    <div class="flex items-center gap-3">
                        <div class="flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${this.getRankingBadgeClass(index + 1)}">
                            ${index + 1}
                        </div>
                        <div>
                            <div class="text-sm font-medium text-gray-800 group-hover:text-primary-600 transition-colors">${item.channel_name}</div>
                        </div>
                    </div>
                    <div class="text-right">
                        <div class="text-sm font-bold ${isRevenue ? 'text-green-600' : 'text-blue-600'}">${value}</div>
                    </div>
                </a>
            `;
        }).join('');

        container.innerHTML = html;
    }

    // 获取排名徽章样式类
    getRankingBadgeClass(rank) {
        switch(rank) {
            case 1: return 'bg-gradient-to-r from-yellow-400 to-yellow-500 text-white';
            case 2: return 'bg-gradient-to-r from-gray-300 to-gray-400 text-white';
            case 3: return 'bg-gradient-to-r from-orange-400 to-orange-500 text-white';
            default: return 'bg-gray-100 text-gray-600';
        }
    }

    // 加载趋势图表
    async loadTrendChart() {
        try {
            const chartData = await api.getChartData(this.currentChartType, {
                time_range: 7
            });

            this.renderTrendChart(chartData.data.chart_data);
            
        } catch (error) {
            console.error('加载趋势图表失败:', error);
            this.showChartError();
        }
    }

    // 渲染趋势图表
    renderTrendChart(chartData) {
        const ctx = document.getElementById('trend-chart');
        if (!ctx) return;

        // 销毁现有图表
        if (this.chart) {
            this.chart.destroy();
        }

        // 准备数据
        const datasets = chartData.series.map((series, index) => ({
            label: series.name,
            data: series.data,
            borderColor: Utils.getColor(index),
            backgroundColor: Utils.getColor(index) + '20',
            fill: false,
            tension: 0.4
        }));

        // 配置选项
        const options = Utils.deepClone(CONFIG.CHARTS.DEFAULT_OPTIONS);
        
        // 根据图表类型设置Y轴格式化
        options.scales.y.ticks = {
            callback: (value) => {
                switch (this.currentChartType) {
                    case 'revenue':
                        return Utils.formatCurrency(value);
                    case 'subscribers':
                    case 'views':
                        return Utils.formatNumber(value);
                    default:
                        return value;
                }
            }
        };

        // 创建图表
        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: chartData.dates.map(date => Utils.formatDate(date, 'MM-DD')),
                datasets: datasets
            },
            options: options
        });
    }

    // 显示图表错误
    showChartError() {
        const ctx = document.getElementById('trend-chart');
        if (!ctx) return;

        const parent = ctx.parentElement;
        parent.innerHTML = `
            <div class="d-flex align-items-center justify-content-center" style="height: 200px;">
                <div class="text-muted">
                    <i class="bi bi-exclamation-triangle fs-1"></i>
                    <div class="mt-2">图表加载失败</div>
                </div>
            </div>
        `;
    }

    // 刷新仪表板
    async refresh() {
        await this.loadDashboard();
    }

    // 开始自动刷新
    startAutoRefresh() {
        this.stopAutoRefresh(); // 先停止现有的刷新
        
        this.refreshInterval = setInterval(() => {
            this.refresh();
        }, CONFIG.REFRESH_INTERVALS.DASHBOARD);
    }

    // 停止自动刷新
    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    // 显示加载状态
    showLoading(message = '正在加载...') {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            statusText.textContent = message;
            statusBar.classList.remove('d-none', 'alert-danger');
            statusBar.classList.add('alert-info');
        }
    }

    // 隐藏加载状态
    hideLoading() {
        const statusBar = document.getElementById('status-bar');
        if (statusBar) {
            statusBar.classList.add('d-none');
        }
    }

    // 显示错误
    showError(message) {
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (statusBar && statusText) {
            statusText.textContent = message;
            statusBar.classList.remove('d-none', 'alert-info');
            statusBar.classList.add('alert-danger');
            
            // 3秒后自动隐藏
            setTimeout(() => {
                this.hideLoading();
            }, 3000);
        }
    }

    // 销毁
    destroy() {
        this.stopAutoRefresh();
        
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }
}

// 仪表板工具函数
const DashboardUtils = {
    // 计算变化百分比
    calculateChangePercentage(current, previous) {
        if (!previous || previous === 0) return 0;
        return ((current - previous) / previous) * 100;
    },

    // 获取变化状态
    getChangeStatus(change) {
        if (change > 0) return 'positive';
        if (change < 0) return 'negative';
        return 'neutral';
    },

    // 格式化变化显示
    formatChangeDisplay(current, previous, isPercentage = false) {
        const change = current - previous;
        const percentage = this.calculateChangePercentage(current, previous);
        const status = this.getChangeStatus(change);
        
        const changeText = isPercentage ? 
            Utils.formatPercentage(percentage) : 
            Utils.formatChange(change);
            
        return {
            value: current,
            change: change,
            changeText: changeText,
            percentage: percentage,
            status: status,
            className: `text-${status}`
        };
    },

    // 生成颜色渐变
    generateGradient(ctx, color) {
        const gradient = ctx.createLinearGradient(0, 0, 0, 400);
        gradient.addColorStop(0, color + '40');
        gradient.addColorStop(1, color + '00');
        return gradient;
    }
};

// 当DOM加载完成时初始化仪表板
let dashboardInstance;

document.addEventListener('DOMContentLoaded', function() {
    // 只在仪表板标签页激活时初始化
    const dashboardTab = document.querySelector('[href="#dashboard"]');
    if (dashboardTab && dashboardTab.classList.contains('active')) {
        dashboardInstance = new Dashboard();
    }
    
    // 监听标签页切换
    document.addEventListener('shown.bs.tab', function(e) {
        if (e.target.getAttribute('href') === '#dashboard') {
            if (!dashboardInstance) {
                dashboardInstance = new Dashboard();
            } else {
                dashboardInstance.refresh();
            }
        }
    });
});

// 页面卸载时清理
window.addEventListener('beforeunload', function() {
    if (dashboardInstance) {
        dashboardInstance.destroy();
    }
});

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Dashboard, DashboardUtils };
}