(function () {
    'use strict';

    const COLORS = ['#ec4899', '#2563eb', '#f59e0b', '#10b981', '#8b5cf6', '#06b6d4'];

    class BilibiliChargingDashboard {
        constructor() {
            this.chart = null;
            this.days = 31;
            this.loading = false;
            this.bindEvents();
            this.load();
        }

        bindEvents() {
            document.getElementById('bilibili-charging-range')?.addEventListener('change', (event) => {
                this.days = Number(event.target.value) || 31;
                this.load();
            });
            document.getElementById('bilibili-charging-sync')?.addEventListener('click', () => this.sync());
            window.addEventListener('resize', () => this.chart?.resize());
        }

        async load() {
            this.setStatus('正在加载充电人数...');
            try {
                const response = await api.get('/bilibili-intelligence/charging-dashboard', { days: this.days });
                if (!response?.success) {
                    throw new Error(response?.message || '接口返回失败');
                }
                this.render(response.data || {});
            } catch (error) {
                console.error('[BilibiliChargingDashboard] load failed:', error);
                this.setStatus(`加载失败：${error.message}`, true);
                this.renderEmpty('暂时无法读取充电人数');
            }
        }

        async sync() {
            if (this.loading) return;
            this.loading = true;
            const button = document.getElementById('bilibili-charging-sync');
            button?.setAttribute('disabled', 'disabled');
            button?.classList.add('opacity-60', 'cursor-not-allowed');
            this.setStatus('正在从 B站同步 5 个 UP 主的充电人数...');
            try {
                const response = await api.post('/bilibili-intelligence/charging-dashboard/sync', {});
                if (!response?.success) {
                    throw new Error(response?.message || '同步失败');
                }
                const result = response.data || {};
                const suffix = result.failed ? `，${result.failed} 个失败` : '';
                this.setStatus(`同步完成：${result.synced || 0} 个成功${suffix}`);
                await this.load();
            } catch (error) {
                console.error('[BilibiliChargingDashboard] sync failed:', error);
                this.setStatus(`同步失败：${error.message}`, true);
            } finally {
                this.loading = false;
                button?.removeAttribute('disabled');
                button?.classList.remove('opacity-60', 'cursor-not-allowed');
            }
        }

        render(payload) {
            const accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
            const dates = Array.isArray(payload.dates) ? payload.dates : [];
            this.renderChart(accounts, dates);
            this.renderSummary(accounts);

            const updated = payload.last_fetched_at ? this.formatDateTime(payload.last_fetched_at) : '';
            const updatedElement = document.getElementById('bilibili-charging-updated-at');
            if (updatedElement) {
                updatedElement.textContent = updated ? `最近同步 ${updated}` : '';
            }
            if (payload.has_data) {
                this.setStatus(`已跟踪 ${accounts.length} 个 UP 主；同一天重复同步会更新当天快照`);
            } else {
                this.setStatus('尚无快照，点击“立即同步”生成今天的第一份数据');
            }
        }

        renderChart(accounts, dates) {
            const element = document.getElementById('bilibili-charging-chart');
            if (!element || typeof echarts === 'undefined') return;
            if (!this.chart) {
                this.chart = echarts.init(element);
            }
            const series = accounts.map((account, index) => {
                const values = new Map((account.points || []).map((point) => [point.date, point.charger_count]));
                return {
                    name: account.name,
                    type: 'line',
                    smooth: 0.25,
                    symbol: 'circle',
                    symbolSize: 7,
                    connectNulls: false,
                    showSymbol: dates.length <= 31,
                    itemStyle: { color: COLORS[index % COLORS.length] },
                    lineStyle: { width: 2.5, color: COLORS[index % COLORS.length] },
                    data: dates.map((day) => values.has(day) ? values.get(day) : null)
                };
            });
            this.chart.setOption({
                animationDuration: 450,
                color: COLORS,
                tooltip: {
                    trigger: 'axis',
                    backgroundColor: 'rgba(15, 23, 42, 0.94)',
                    borderWidth: 0,
                    textStyle: { color: '#fff' },
                    valueFormatter: (value) => value == null ? '-' : `${this.number(value)} 人`
                },
                legend: {
                    type: 'scroll',
                    top: 8,
                    left: 24,
                    right: 24,
                    textStyle: { color: '#475569' }
                },
                grid: { left: 68, right: 32, top: 62, bottom: 45 },
                xAxis: {
                    type: 'category',
                    boundaryGap: false,
                    data: dates,
                    axisLabel: { color: '#64748b', formatter: (value) => String(value).slice(5) },
                    axisLine: { lineStyle: { color: '#e2e8f0' } }
                },
                yAxis: {
                    type: 'value',
                    scale: true,
                    axisLabel: { color: '#64748b', formatter: (value) => this.number(value) },
                    splitLine: { lineStyle: { color: '#f1f5f9' } }
                },
                series
            }, true);
        }

        renderSummary(accounts) {
            const body = document.getElementById('bilibili-charging-summary');
            if (!body) return;
            if (!accounts.length) {
                body.innerHTML = '<tr><td colspan="6" class="px-6 py-8 text-center text-gray-400">情报页尚未配置监控 UP 主</td></tr>';
                return;
            }
            body.innerHTML = accounts.map((account, index) => {
                const latest = account.latest;
                const growth = latest?.daily_growth;
                const growthClass = growth > 0 ? 'text-emerald-600' : (growth < 0 ? 'text-rose-600' : 'text-gray-500');
                const sourceUrl = this.escape(account.source_url || `https://space.bilibili.com/${account.uid}`);
                return `
                    <tr class="hover:bg-slate-50 transition-colors">
                        <td class="px-6 py-4">
                            <a href="${sourceUrl}" target="_blank" rel="noopener noreferrer" class="flex items-center gap-3 group">
                                <span class="w-8 h-8 rounded-lg flex items-center justify-center text-white text-xs font-bold" style="background:${COLORS[index % COLORS.length]}">${index + 1}</span>
                                <span>
                                    <span class="block font-semibold text-gray-800 group-hover:text-pink-600">${this.escape(account.name)}</span>
                                    <span class="block text-xs text-gray-400">UID ${this.escape(account.uid)}</span>
                                </span>
                            </a>
                        </td>
                        <td class="px-4 py-4 text-right font-bold text-gray-800">${latest ? this.number(latest.charger_count) : '-'}</td>
                        <td class="px-4 py-4 text-right font-semibold ${growthClass}">${latest ? this.signed(growth) : '-'}</td>
                        <td class="px-4 py-4 text-right text-gray-700">${latest ? this.currency(latest.daily_revenue) : '-'}</td>
                        <td class="px-4 py-4 text-right text-gray-700">${latest ? this.currency(latest.daily_cash) : '-'}</td>
                        <td class="px-6 py-4 text-right font-bold text-pink-600">${latest ? this.currency(latest.monthly_income) : '-'}</td>
                    </tr>
                `;
            }).join('');
        }

        renderEmpty(message) {
            this.chart?.clear();
            const body = document.getElementById('bilibili-charging-summary');
            if (body) {
                body.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-gray-400">${this.escape(message)}</td></tr>`;
            }
        }

        setStatus(message, isError = false) {
            const element = document.getElementById('bilibili-charging-status');
            if (!element) return;
            element.textContent = message;
            element.className = `px-6 pt-5 text-sm ${isError ? 'text-rose-600' : 'text-gray-500'}`;
        }

        number(value) {
            return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value) || 0);
        }

        currency(value) {
            return `¥${this.number(value)}`;
        }

        signed(value) {
            const number = Number(value) || 0;
            return `${number > 0 ? '+' : ''}${this.number(number)}`;
        }

        formatDateTime(value) {
            const parsed = new Date(value);
            return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleString('zh-CN', { hour12: false });
        }

        escape(value) {
            return String(value ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('bilibili-charging-dashboard')) {
            window.bilibiliChargingDashboard = new BilibiliChargingDashboard();
        }
    });
})();
