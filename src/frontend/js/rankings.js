// 排行榜模块
class Rankings {
    constructor() {
        this.currentData = null;
        this.refreshInterval = null;
        
        this.init();
    }

    // 初始化
    init() {
        this.bindEvents();
        this.loadRankings();
        this.startAutoRefresh();
    }

    // 绑定事件
    bindEvents() {
        // 页面可见性变化时暂停/恢复自动刷新
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.stopAutoRefresh();
            } else {
                this.startAutoRefresh();
            }
        });
    }

    // 加载排行榜数据
    async loadRankings() {
        try {
            this.showLoading('正在加载排行榜数据...');
            
            // 获取完整排行榜数据
            const rankingsData = await api.getAllRankings(10);
            
            this.currentData = rankingsData.data;
            this.renderAllRankings(this.currentData);
            
            this.hideLoading();
            
        } catch (error) {
            console.error('加载排行榜失败:', error);
            this.hideLoading();
            this.showError('加载排行榜数据失败');
        }
    }

    // 渲染所有排行榜
    renderAllRankings(data) {
        // 日收入排行榜
        this.renderFullRanking(
            'daily-revenue-full-ranking', 
            data.daily_revenue?.rankings || [], 
            'revenue',
            '今日收入'
        );

        // 月收入排行榜
        this.renderFullRanking(
            'monthly-revenue-full-ranking', 
            data.monthly_revenue?.rankings || [], 
            'total_revenue',
            '本月收入'
        );

        // 日订阅增长排行榜
        this.renderFullRanking(
            'daily-subscriber-full-ranking', 
            data.daily_subscriber?.rankings || [], 
            'subscriber_change',
            '今日新增'
        );

        // 月订阅增长排行榜
        this.renderFullRanking(
            'monthly-subscriber-full-ranking', 
            data.monthly_subscriber?.rankings || [], 
            'total_subscriber_change',
            '本月新增'
        );
    }

    // 渲染完整排行榜
    renderFullRanking(containerId, rankings, valueField, valueLabel) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!rankings || rankings.length === 0) {
            container.innerHTML = this.getEmptyState('暂无排行榜数据');
            return;
        }

        const isRevenue = valueField.includes('revenue');
        
        const html = rankings.map((item, index) => {
            const value = item[valueField];
            const formattedValue = isRevenue ? 
                Utils.formatCurrency(value) : 
                Utils.formatChange(value, true);

            return `
                <div class="ranking-item" data-channel-id="${item.channel_id}">
                    <div class="ranking-number rank-${this.getRankClass(index + 1)}">${index + 1}</div>
                    <div class="ranking-content">
                        <div class="ranking-title">${item.channel_name}</div>
                        <div class="ranking-value">
                            <span class="text-muted">${valueLabel}:</span>
                            <span class="fw-bold ${isRevenue ? 'text-success' : 'text-info'}">${formattedValue}</span>
                        </div>
                    </div>
                    <div class="ranking-actions">
                        <button class="btn btn-sm btn-outline-primary" onclick="viewChannelDetails(${item.channel_id})">
                            <i class="bi bi-eye"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;
    }

    // 获取排名样式类
    getRankClass(rank) {
        if (rank === 1) return '1';
        if (rank === 2) return '2';
        if (rank === 3) return '3';
        return 'other';
    }

    // 获取空状态HTML
    getEmptyState(message) {
        return `
            <div class="empty-state">
                <i class="bi bi-trophy"></i>
                <div>${message}</div>
            </div>
        `;
    }

    // 刷新排行榜
    async refresh() {
        await this.loadRankings();
    }

    // 开始自动刷新
    startAutoRefresh() {
        this.stopAutoRefresh();
        
        this.refreshInterval = setInterval(() => {
            this.refresh();
        }, CONFIG.REFRESH_INTERVALS.RANKINGS);
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
            
            setTimeout(() => {
                this.hideLoading();
            }, 3000);
        }
    }

    // 导出排行榜数据
    exportRankings() {
        if (!this.currentData) {
            showToast('没有可导出的数据', 'warning');
            return;
        }

        try {
            const exportData = this.prepareExportData(this.currentData);
            this.downloadAsCSV(exportData, `rankings_${Utils.formatDate(new Date())}.csv`);
            showToast('排行榜数据导出成功', 'success');
        } catch (error) {
            console.error('导出排行榜失败:', error);
            showToast('导出失败', 'error');
        }
    }

    // 准备导出数据
    prepareExportData(data) {
        const rows = [];
        
        // 添加表头
        rows.push(['排行榜类型', '排名', '频道名称', '频道ID', '数值', '格式化数值']);
        
        // 添加各排行榜数据
        const rankingTypes = [
            { key: 'daily_revenue', name: '日收入排行榜', valueField: 'revenue' },
            { key: 'monthly_revenue', name: '月收入排行榜', valueField: 'total_revenue' },
            { key: 'daily_subscriber', name: '日订阅增长排行榜', valueField: 'subscriber_change' },
            { key: 'monthly_subscriber', name: '月订阅增长排行榜', valueField: 'total_subscriber_change' }
        ];

        rankingTypes.forEach(type => {
            const rankings = data[type.key]?.rankings || [];
            rankings.forEach((item, index) => {
                const value = item[type.valueField];
                const formattedValue = type.valueField.includes('revenue') ? 
                    Utils.formatCurrency(value) : 
                    Utils.formatChange(value, true);

                rows.push([
                    type.name,
                    index + 1,
                    item.channel_name,
                    item.youtube_id,
                    value,
                    formattedValue
                ]);
            });
        });

        return rows;
    }

    // 下载CSV文件
    downloadAsCSV(data, filename) {
        const csvContent = data.map(row => 
            row.map(cell => `"${cell}"`).join(',')
        ).join('\n');
        
        const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        
        if (link.download !== undefined) {
            const url = URL.createObjectURL(blob);
            link.setAttribute('href', url);
            link.setAttribute('download', filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    }

    // 销毁
    destroy() {
        this.stopAutoRefresh();
    }
}

// 排行榜相关的全局函数
function refreshRankings() {
    if (rankingsInstance) {
        rankingsInstance.refresh();
    }
}

function viewChannelDetails(channelId) {
    // 切换到频道管理页面并显示频道详情
    const channelsTab = document.querySelector('[href="#channels"]');
    if (channelsTab) {
        const tab = new bootstrap.Tab(channelsTab);
        tab.show();
        
        // 延迟执行，确保页面已切换
        setTimeout(() => {
            if (channelsInstance) {
                channelsInstance.showChannelDetails(channelId);
            }
        }, 100);
    }
}

function exportRankingsData() {
    if (rankingsInstance) {
        rankingsInstance.exportRankings();
    }
}

// 排行榜工具函数
const RankingsUtils = {
    // 获取排名变化趋势
    getRankingTrend(currentRank, previousRank) {
        if (!previousRank) return 'new';
        
        if (currentRank < previousRank) return 'up';
        if (currentRank > previousRank) return 'down';
        return 'same';
    },

    // 格式化排名变化
    formatRankingChange(currentRank, previousRank) {
        const trend = this.getRankingTrend(currentRank, previousRank);
        
        switch (trend) {
            case 'up':
                return {
                    text: `↑${previousRank - currentRank}`,
                    className: 'text-success',
                    icon: 'bi-arrow-up'
                };
            case 'down':
                return {
                    text: `↓${currentRank - previousRank}`,
                    className: 'text-danger',
                    icon: 'bi-arrow-down'
                };
            case 'same':
                return {
                    text: '→',
                    className: 'text-secondary',
                    icon: 'bi-dash'
                };
            case 'new':
                return {
                    text: 'NEW',
                    className: 'text-info',
                    icon: 'bi-star'
                };
        }
    },

    // 计算排行榜统计信息
    calculateRankingStats(rankings) {
        if (!rankings || rankings.length === 0) {
            return {
                total: 0,
                average: 0,
                median: 0,
                sum: 0
            };
        }

        const values = rankings.map(item => {
            return item.revenue || item.total_revenue || item.subscriber_change || item.total_subscriber_change || 0;
        });

        const sum = values.reduce((a, b) => a + b, 0);
        const average = sum / values.length;
        
        const sortedValues = [...values].sort((a, b) => a - b);
        const median = sortedValues.length % 2 === 0 ?
            (sortedValues[sortedValues.length / 2 - 1] + sortedValues[sortedValues.length / 2]) / 2 :
            sortedValues[Math.floor(sortedValues.length / 2)];

        return {
            total: rankings.length,
            average: average,
            median: median,
            sum: sum
        };
    }
};

// 当DOM加载完成时初始化排行榜
let rankingsInstance;

document.addEventListener('DOMContentLoaded', function() {
    // 监听标签页切换
    document.addEventListener('shown.bs.tab', function(e) {
        if (e.target.getAttribute('href') === '#rankings') {
            if (!rankingsInstance) {
                rankingsInstance = new Rankings();
            } else {
                rankingsInstance.refresh();
            }
        }
    });
});

// 页面卸载时清理
window.addEventListener('beforeunload', function() {
    if (rankingsInstance) {
        rankingsInstance.destroy();
    }
});

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Rankings, RankingsUtils };
}