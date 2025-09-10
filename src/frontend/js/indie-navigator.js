// 独立开发者领航员 - 商机发现主模块
console.log('🧭 [INDIE_NAVIGATOR] 商机发现模块已加载');

class IndieNavigator {
    constructor() {
        this.opportunities = [];
        this.filteredOpportunities = [];
        this.currentFilter = 'all';
        this.currentSort = 'newest';
        this.searchQuery = '';
        this.myFunnel = this.loadMyFunnel();
        
        // 设置API基础URL - 支持配置
        this.apiBaseUrl = window.INDIE_NAVIGATOR_API_BASE || 'http://localhost:5001';
        
        this.init();
    }
    
    async init() {
        console.log('🚀 [INDIE_NAVIGATOR] 初始化商机发现系统');
        
        // 检查用户登录状态
        await this.checkAuth();
        
        // 绑定事件监听器
        this.bindEventListeners();
        
        // 显示加载状态
        this.showLoadingState();
        
        // 加载商机数据
        await this.loadOpportunities();
        
        // 更新统计数据
        this.updateStats();
        
        console.log('✅ [INDIE_NAVIGATOR] 初始化完成');
    }
    
    async checkAuth() {
        // 用户认证现在由 shared-navigation.js 统一处理，这里不需要重复处理
        console.log('🔑 [AUTH] 用户认证由导航组件统一管理');
    }
    
    bindEventListeners() {
        // 搜索功能
        const searchInput = document.getElementById('search-input');
        if (searchInput) {
            searchInput.addEventListener('input', Utils.debounce((e) => {
                this.searchQuery = e.target.value.toLowerCase();
                this.filterAndDisplayOpportunities();
            }, 300));
        }
        
        // 筛选标签
        document.querySelectorAll('.filter-tag').forEach(tag => {
            tag.addEventListener('click', (e) => {
                // 更新激活状态
                document.querySelectorAll('.filter-tag').forEach(t => {
                    t.classList.remove('active');
                    t.classList.remove('bg-primary-500', 'text-white', 'border-primary-500');
                    t.classList.add('bg-white', 'text-gray-700', 'border-gray-300');
                });
                
                e.target.classList.add('active');
                e.target.classList.remove('bg-white', 'text-gray-700', 'border-gray-300');
                e.target.classList.add('bg-primary-500', 'text-white', 'border-primary-500');
                
                this.currentFilter = e.target.dataset.filter;
                this.filterAndDisplayOpportunities();
            });
        });
        
        // 排序选择
        const sortSelect = document.getElementById('sort-select');
        if (sortSelect) {
            sortSelect.addEventListener('change', (e) => {
                this.currentSort = e.target.value;
                this.filterAndDisplayOpportunities();
            });
        }
        
        // 刷新按钮
        document.querySelector('button[title="刷新数据"]')?.addEventListener('click', () => {
            this.refreshOpportunities();
        });
    }
    
    showLoadingState() {
        const loadingContainer = document.getElementById('loading-state');
        const grid = document.getElementById('opportunities-grid');
        
        if (loadingContainer && grid) {
            // 隐藏正常网格
            grid.classList.add('hidden');
            
            // 显示加载骨架
            loadingContainer.classList.remove('hidden');
            loadingContainer.innerHTML = '';
            
            // 生成6个加载骨架卡片
            for (let i = 0; i < 6; i++) {
                const skeleton = document.createElement('div');
                skeleton.className = 'bg-white/90 backdrop-blur-sm rounded-xl p-6 loading-skeleton';
                skeleton.innerHTML = `
                    <div class="h-4 bg-gray-200 rounded mb-4"></div>
                    <div class="h-6 bg-gray-200 rounded mb-3"></div>
                    <div class="flex gap-2 mb-4">
                        <div class="h-6 w-16 bg-gray-200 rounded"></div>
                        <div class="h-6 w-20 bg-gray-200 rounded"></div>
                    </div>
                    <div class="h-12 bg-gray-200 rounded mb-3"></div>
                    <div class="h-4 bg-gray-200 rounded"></div>
                `;
                loadingContainer.appendChild(skeleton);
            }
        }
    }
    
    hideLoadingState() {
        const loadingContainer = document.getElementById('loading-state');
        const grid = document.getElementById('opportunities-grid');
        
        if (loadingContainer && grid) {
            loadingContainer.classList.add('hidden');
            grid.classList.remove('hidden');
        }
    }
    
    async loadOpportunities() {
        try {
            // 构建查询参数
            const params = new URLSearchParams({
                search: this.searchQuery,
                filter: this.currentFilter,
                sort: this.currentSort,
                limit: 50,
                offset: 0
            });
            
            const response = await fetch(`${this.apiBaseUrl}/api/indie-navigator/opportunities?${params}`);
            const result = await response.json();
            
            if (result.success) {
                this.opportunities = result.data.opportunities;
                this.filterAndDisplayOpportunities();
                this.hideLoadingState();
            } else {
                throw new Error(result.error || '获取数据失败');
            }
            
        } catch (error) {
            console.error('❌ [INDIE_NAVIGATOR] 加载商机数据失败:', error);
            this.showErrorState('加载数据失败，请稍后重试');
            
            // 如果API失败，回退到模拟数据
            this.opportunities = this.generateMockOpportunities();
            this.filterAndDisplayOpportunities();
            this.hideLoadingState();
        }
    }
    
    async refreshOpportunities() {
        this.showToast('正在刷新数据...', 'info');
        this.showLoadingState();
        await this.loadOpportunities();
        this.showToast('数据刷新完成', 'success');
    }
    
    generateMockOpportunities() {
        const mockData = [
            {
                id: 1,
                platform: 'twitter',
                projectName: 'MicroSaaS Builder',
                creatorName: 'Alex Chen',
                creatorHandle: '@alexbuilds',
                metrics: {
                    mrr: 12000,
                    users: 850,
                    likes: 234,
                    retweets: 67
                },
                category: 'saas',
                description: '帮助开发者快速构建微型SaaS应用的平台，提供模板、支付集成和用户管理功能。',
                originalContent: '🎉 MicroSaaS Builder 终于突破了 $12k MRR！感谢所有用户的支持。从想法到现在用了8个月时间，证明小而美的产品也能成功！ #buildinpublic #SaaS',
                platformUrl: 'https://twitter.com/alexbuilds/status/123456',
                websiteUrl: 'https://microsaasbuilder.com',
                publishedAt: new Date('2025-09-07T10:30:00Z'),
                tags: ['SaaS', 'Developer Tools', 'No-Code'],
                engagement: 301
            },
            {
                id: 2,
                platform: 'twitter',
                projectName: 'AI Content Optimizer',
                creatorName: 'Sarah Johnson',
                creatorHandle: '@sarahcodes',
                metrics: {
                    mrr: 8500,
                    users: 2100,
                    likes: 189,
                    retweets: 43
                },
                category: 'ai',
                description: 'AI驱动的内容优化工具，帮助创作者提升文章、社交媒体帖子的表现。',
                originalContent: 'AI Content Optimizer 刚刚达到了 $8.5k MRR！用户增长到了2100人。AI辅助写作真的是刚需，下个目标 $10k MRR 🚀',
                platformUrl: 'https://twitter.com/sarahcodes/status/123457',
                websiteUrl: 'https://aicontentopt.com',
                publishedAt: new Date('2025-09-06T15:45:00Z'),
                tags: ['AI', 'Content', 'Writing'],
                engagement: 232
            },
            {
                id: 3,
                platform: 'twitter',
                projectName: 'Mobile App Analytics',
                creatorName: 'David Kim',
                creatorHandle: '@davidkimdev',
                metrics: {
                    arr: 95000,
                    users: 1200,
                    likes: 156,
                    retweets: 29
                },
                category: 'mobile',
                description: '专为移动应用开发者设计的轻量级分析工具，简单易用且价格实惠。',
                originalContent: '🎊 大milestone！Mobile App Analytics 年收入突破 $95k！感谢1200+开发者的信任。简单的产品往往是最好的产品。',
                platformUrl: 'https://twitter.com/davidkimdev/status/123458',
                websiteUrl: 'https://mobileanalytics.dev',
                publishedAt: new Date('2025-09-05T09:15:00Z'),
                tags: ['Mobile', 'Analytics', 'Developer Tools'],
                engagement: 185
            },
            {
                id: 4,
                platform: 'twitter',
                projectName: 'Code Snippet Manager',
                creatorName: 'Emma Wilson',
                creatorHandle: '@emmacodes',
                metrics: {
                    users: 15000,
                    revenue: 3200,
                    likes: 278,
                    retweets: 91
                },
                category: 'extension',
                description: '浏览器扩展，帮助开发者管理和分享代码片段，支持多种编程语言。',
                originalContent: '🔥 Code Snippet Manager 用户数突破15k！这个月收入$3.2k，浏览器扩展的潜力真的很大。下一步计划添加AI代码生成功能。',
                platformUrl: 'https://twitter.com/emmacodes/status/123459',
                websiteUrl: 'https://codesnippets.dev',
                publishedAt: new Date('2025-09-04T14:20:00Z'),
                tags: ['Browser Extension', 'Developer Tools', 'Productivity'],
                engagement: 369
            },
            {
                id: 5,
                platform: 'twitter',
                projectName: 'Newsletter Automation',
                creatorName: 'Mark Thompson',
                creatorHandle: '@markbuilds',
                metrics: {
                    mrr: 6800,
                    users: 680,
                    likes: 123,
                    retweets: 34
                },
                category: 'saas',
                description: '自动化邮件营销工具，专注于内容创作者和小企业的newsletter管理。',
                originalContent: '💪 Newsletter Automation 达到$6.8k MRR！680个付费用户，转化率保持在12%。专注细分市场是正确的选择！',
                platformUrl: 'https://twitter.com/markbuilds/status/123460',
                websiteUrl: 'https://newsletterautomation.io',
                publishedAt: new Date('2025-09-03T11:30:00Z'),
                tags: ['SaaS', 'Email Marketing', 'Automation'],
                engagement: 157
            },
            {
                id: 6,
                platform: 'twitter',
                projectName: 'Design System Generator',
                creatorName: 'Lisa Rodriguez',
                creatorHandle: '@lisadesigns',
                metrics: {
                    users: 8500,
                    revenue: 4500,
                    likes: 445,
                    retweets: 128
                },
                category: 'saas',
                description: '自动生成设计系统的工具，帮助设计师和开发者快速建立一致的UI组件库。',
                originalContent: '🎨 Design System Generator 用户数达到8.5k！这个月收入$4.5k。设计工具市场比想象中更大，准备推出团队协作功能！',
                platformUrl: 'https://twitter.com/lisadesigns/status/123461',
                websiteUrl: 'https://designsystemgen.com',
                publishedAt: new Date('2025-09-02T16:45:00Z'),
                tags: ['Design Tools', 'SaaS', 'UI/UX'],
                engagement: 573
            }
        ];
        
        return mockData;
    }
    
    filterAndDisplayOpportunities() {
        // 应用筛选器
        this.filteredOpportunities = this.opportunities.filter(opportunity => {
            // 类别筛选
            if (this.currentFilter !== 'all' && opportunity.category !== this.currentFilter) {
                return false;
            }
            
            // 搜索筛选
            if (this.searchQuery) {
                const searchableText = `${opportunity.projectName} ${opportunity.creatorName} ${opportunity.description} ${opportunity.tags.join(' ')}`.toLowerCase();
                if (!searchableText.includes(this.searchQuery)) {
                    return false;
                }
            }
            
            return true;
        });
        
        // 应用排序
        this.sortOpportunities();
        
        // 显示结果
        this.displayOpportunities();
        
        // 更新统计
        this.updateStats();
    }
    
    sortOpportunities() {
        this.filteredOpportunities.sort((a, b) => {
            switch (this.currentSort) {
                case 'newest':
                    return new Date(b.publishedAt) - new Date(a.publishedAt);
                case 'revenue':
                    return ((b.metrics?.mrr || b.metrics?.arr || b.metrics?.revenue || 0) - (a.metrics?.mrr || a.metrics?.arr || a.metrics?.revenue || 0));
                case 'users':
                    return (b.metrics?.users || 0) - (a.metrics?.users || 0);
                case 'engagement':
                    return (b.engagement || 0) - (a.engagement || 0);
                default:
                    return 0;
            }
        });
    }
    
    displayOpportunities() {
        const grid = document.getElementById('opportunities-grid');
        const emptyState = document.getElementById('empty-state');
        
        if (!grid || !emptyState) return;
        
        if (this.filteredOpportunities.length === 0) {
            grid.classList.add('hidden');
            emptyState.classList.remove('hidden');
            return;
        }
        
        emptyState.classList.add('hidden');
        grid.classList.remove('hidden');
        
        grid.innerHTML = '';
        
        this.filteredOpportunities.forEach(opportunity => {
            const card = this.createOpportunityCard(opportunity);
            grid.appendChild(card);
        });
    }
    
    createOpportunityCard(opportunity) {
        const card = document.createElement('div');
        card.className = 'opportunity-card bg-white/90 backdrop-blur-sm rounded-xl p-6 hover:bg-white transition-all cursor-pointer';
        card.onclick = () => this.showOpportunityDetail(opportunity);
        
        // 格式化指标
        const primaryMetric = this.getPrimaryMetric(opportunity);
        const secondaryMetric = this.getSecondaryMetric(opportunity);
        
        // 时间格式化
        const timeAgo = this.getTimeAgo(opportunity.publishedAt);
        
        // 判断是否已保存
        const isSaved = this.isOpportunitySaved(opportunity.id);
        
        card.innerHTML = `
            <div class="flex items-start justify-between mb-4">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center text-white text-sm font-bold">
                        ${(opportunity.creatorName || '?').charAt(0).toUpperCase()}
                    </div>
                    <div>
                        <div class="font-semibold text-gray-800">${opportunity.creatorName || '未知创作者'}</div>
                        <div class="text-sm text-gray-500">${opportunity.creatorHandle || '未知账号'} • ${timeAgo}</div>
                    </div>
                </div>
                <div class="flex gap-2">
                    <button 
                        class="save-btn p-2 rounded-lg transition-all ${isSaved ? 'text-blue-600 bg-blue-50' : 'text-gray-400 hover:text-blue-600 hover:bg-blue-50'}"
                        onclick="event.stopPropagation(); indieNavigator.toggleSaveOpportunity(${opportunity.id})"
                        title="${isSaved ? '取消收藏' : '收藏'}"
                    >
                        <i class="fas ${isSaved ? 'fa-bookmark' : 'fa-bookmark'} ${isSaved ? '' : 'far'}"></i>
                    </button>
                    <span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                        ${this.getCategoryName(opportunity.category)}
                    </span>
                </div>
            </div>
            
            <h3 class="font-bold text-lg text-gray-800 mb-2">${opportunity.projectName}</h3>
            
            <div class="flex gap-2 mb-4">
                <span class="metric-badge">${primaryMetric}</span>
                ${secondaryMetric ? `<span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">${secondaryMetric}</span>` : ''}
            </div>
            
            <p class="text-gray-600 text-sm mb-4 line-clamp-3">${opportunity.description}</p>
            
            <div class="flex items-center justify-between text-sm text-gray-500">
                <div class="flex items-center gap-4">
                    <span><i class="fas fa-heart mr-1"></i>${opportunity.metrics?.likes || 0}</span>
                    <span><i class="fas fa-retweet mr-1"></i>${opportunity.metrics?.retweets || 0}</span>
                </div>
                <div class="flex gap-1">
                    ${(opportunity.tags || []).slice(0, 2).map(tag => 
                        `<span class="px-2 py-1 bg-gray-100 rounded text-xs">${tag}</span>`
                    ).join('')}
                </div>
            </div>
        `;
        
        return card;
    }
    
    getPrimaryMetric(opportunity) {
        // 安全检查 metrics 对象是否存在
        if (!opportunity.metrics) {
            return '新项目';
        }
        
        const metrics = opportunity.metrics;
        if (metrics.mrr) {
            return `$${(metrics.mrr / 1000).toFixed(1)}k MRR`;
        } else if (metrics.arr) {
            return `$${(metrics.arr / 1000).toFixed(1)}k ARR`;
        } else if (metrics.revenue) {
            return `$${(metrics.revenue / 1000).toFixed(1)}k 收入`;
        } else if (metrics.users) {
            return `${(metrics.users / 1000).toFixed(1)}k 用户`;
        }
        return '新项目';
    }
    
    getSecondaryMetric(opportunity) {
        // 安全检查 metrics 对象是否存在
        if (!opportunity.metrics) {
            return null;
        }
        
        const metrics = opportunity.metrics;
        if (metrics.users && (metrics.mrr || metrics.arr || metrics.revenue)) {
            return `${(metrics.users / 1000).toFixed(1)}k 用户`;
        }
        return null;
    }
    
    getCategoryName(category) {
        const categoryMap = {
            'saas': 'SaaS应用',
            'ai': 'AI工具',
            'mobile': '移动应用',
            'extension': '浏览器扩展',
            'other': '其他'
        };
        return categoryMap[category] || '其他';
    }
    
    getTimeAgo(date) {
        const now = new Date();
        const diff = now - new Date(date);
        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        
        if (days === 0) return '今天';
        if (days === 1) return '昨天';
        if (days < 7) return `${days}天前`;
        if (days < 30) return `${Math.floor(days / 7)}周前`;
        return `${Math.floor(days / 30)}个月前`;
    }
    
    showOpportunityDetail(opportunity) {
        const modal = document.getElementById('opportunity-modal');
        const title = document.getElementById('modal-title');
        const content = document.getElementById('modal-content');
        
        if (!modal || !title || !content) return;
        
        title.textContent = opportunity.projectName;
        
        const isSaved = this.isOpportunitySaved(opportunity.id);
        const primaryMetric = this.getPrimaryMetric(opportunity);
        const timeAgo = this.getTimeAgo(opportunity.publishedAt);
        
        content.innerHTML = `
            <div class="space-y-6">
                <!-- 项目基本信息 -->
                <div class="bg-gray-50 rounded-lg p-6">
                    <div class="flex items-start justify-between mb-4">
                        <div class="flex items-center gap-3">
                            <div class="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center text-white text-lg font-bold">
                                ${opportunity.creatorName.charAt(0).toUpperCase()}
                            </div>
                            <div>
                                <div class="font-semibold text-lg">${opportunity.creatorName}</div>
                                <div class="text-gray-500">${opportunity.creatorHandle}</div>
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="metric-badge text-lg">${primaryMetric}</div>
                            <div class="text-sm text-gray-500 mt-1">${timeAgo}发布</div>
                        </div>
                    </div>
                    
                    <p class="text-gray-700 mb-4">${opportunity.description}</p>
                    
                    <div class="flex flex-wrap gap-2">
                        ${opportunity.tags.map(tag => 
                            `<span class="px-3 py-1 bg-blue-100 text-blue-800 rounded-full text-sm">${tag}</span>`
                        ).join('')}
                    </div>
                </div>
                
                <!-- 关键指标 -->
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    ${opportunity.metrics?.mrr ? `
                        <div class="text-center p-4 bg-green-50 rounded-lg">
                            <div class="text-2xl font-bold text-green-600">$${(opportunity.metrics.mrr / 1000).toFixed(1)}k</div>
                            <div class="text-sm text-gray-600">月经常性收入</div>
                        </div>
                    ` : ''}
                    ${opportunity.metrics?.users ? `
                        <div class="text-center p-4 bg-blue-50 rounded-lg">
                            <div class="text-2xl font-bold text-blue-600">${(opportunity.metrics.users / 1000).toFixed(1)}k</div>
                            <div class="text-sm text-gray-600">用户数量</div>
                        </div>
                    ` : ''}
                    <div class="text-center p-4 bg-purple-50 rounded-lg">
                        <div class="text-2xl font-bold text-purple-600">${opportunity.metrics?.likes || 0}</div>
                        <div class="text-sm text-gray-600">点赞数</div>
                    </div>
                    <div class="text-center p-4 bg-orange-50 rounded-lg">
                        <div class="text-2xl font-bold text-orange-600">${opportunity.engagement || 0}</div>
                        <div class="text-sm text-gray-600">总互动</div>
                    </div>
                </div>
                
                <!-- 原始内容 -->
                <div class="bg-gray-50 rounded-lg p-4">
                    <h4 class="font-semibold mb-2">原始分享内容</h4>
                    <p class="text-gray-700 italic">"${opportunity.originalContent}"</p>
                </div>
                
                <!-- 操作按钮 -->
                <div class="flex gap-4">
                    <button 
                        class="flex-1 px-6 py-3 ${isSaved ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'} text-white rounded-lg transition-colors"
                        onclick="${isSaved ? `indieNavigator.removeFromFunnel(${opportunity.id})` : `indieNavigator.addToFunnel(${opportunity.id})`}"
                    >
                        <i class="fas ${isSaved ? 'fa-times' : 'fa-bookmark'} mr-2"></i>
                        ${isSaved ? '从漏斗移除' : '添加到漏斗'}
                    </button>
                    <a 
                        href="${opportunity.platformUrl}" 
                        target="_blank"
                        class="px-6 py-3 bg-gray-600 hover:bg-gray-700 text-white rounded-lg transition-colors"
                    >
                        <i class="fas fa-external-link-alt mr-2"></i>
                        查看原帖
                    </a>
                    ${opportunity.websiteUrl ? `
                        <a 
                            href="${opportunity.websiteUrl}" 
                            target="_blank"
                            class="px-6 py-3 bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors"
                        >
                            <i class="fas fa-globe mr-2"></i>
                            访问网站
                        </a>
                    ` : ''}
                </div>
            </div>
        `;
        
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }
    
    closeOpportunityModal() {
        const modal = document.getElementById('opportunity-modal');
        if (modal) {
            modal.classList.add('hidden');
            document.body.style.overflow = 'auto';
        }
    }
    
    toggleSaveOpportunity(opportunityId) {
        if (this.isOpportunitySaved(opportunityId)) {
            this.removeFromFunnel(opportunityId);
        } else {
            this.addToFunnel(opportunityId);
        }
    }
    
    addToFunnel(opportunityId) {
        const opportunity = this.opportunities.find(o => o.id === opportunityId);
        if (!opportunity) return;
        
        if (!this.myFunnel.includes(opportunityId)) {
            this.myFunnel.push(opportunityId);
            this.saveMyFunnel();
            this.showToast(`"${opportunity.projectName}" 已添加到我的漏斗`, 'success');
            this.updateStats();
            this.refreshOpportunityCards();
        }
    }
    
    removeFromFunnel(opportunityId) {
        const opportunity = this.opportunities.find(o => o.id === opportunityId);
        if (!opportunity) return;
        
        const index = this.myFunnel.indexOf(opportunityId);
        if (index > -1) {
            this.myFunnel.splice(index, 1);
            this.saveMyFunnel();
            this.showToast(`"${opportunity.projectName}" 已从漏斗移除`, 'info');
            this.updateStats();
            this.refreshOpportunityCards();
            
            // 如果模态框打开着，关闭它
            this.closeOpportunityModal();
        }
    }
    
    isOpportunitySaved(opportunityId) {
        return this.myFunnel.includes(opportunityId);
    }
    
    refreshOpportunityCards() {
        // 重新渲染卡片以更新收藏状态
        this.displayOpportunities();
    }
    
    loadMyFunnel() {
        try {
            const saved = localStorage.getItem('indie_navigator_funnel');
            return saved ? JSON.parse(saved) : [];
        } catch (error) {
            console.error('加载漏斗数据失败:', error);
            return [];
        }
    }
    
    saveMyFunnel() {
        try {
            localStorage.setItem('indie_navigator_funnel', JSON.stringify(this.myFunnel));
        } catch (error) {
            console.error('保存漏斗数据失败:', error);
        }
    }
    
    async updateStats() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/indie-navigator/stats`);
            const result = await response.json();
            
            if (result.success) {
                const stats = result.data;
                document.getElementById('total-opportunities').textContent = stats.total_opportunities;
                document.getElementById('high-potential').textContent = stats.high_potential;
                document.getElementById('trending-signals').textContent = stats.trending_signals;
                document.getElementById('my-saved').textContent = this.myFunnel.length;
            } else {
                throw new Error(result.error || '获取统计数据失败');
            }
        } catch (error) {
            console.error('❌ [INDIE_NAVIGATOR] 获取统计数据失败:', error);
            // 回退到基于本地数据的计算
            this.updateStatsLocal();
        }
    }
    
    updateStatsLocal() {
        // 基于本地数据的统计计算（备用方案）
        document.getElementById('total-opportunities').textContent = this.opportunities.length;
        document.getElementById('high-potential').textContent = this.opportunities.filter(o => {
            if (!o.revenue_signal) return false;
            return o.revenue_signal.includes('$15') || o.revenue_signal.includes('$20') || 
                   o.revenue_signal.includes('$12') || parseInt(o.revenue_signal.replace(/[^\d]/g, '')) > 10000;
        }).length;
        document.getElementById('trending-signals').textContent = this.opportunities.filter(o => {
            if (!o.engagement_metrics) return false;
            return (o.engagement_metrics.likes + o.engagement_metrics.retweets) > 300;
        }).length;
        document.getElementById('my-saved').textContent = this.myFunnel.length;
    }
    
    showErrorState(message) {
        this.hideLoadingState();
        const grid = document.getElementById('opportunities-grid');
        if (grid) {
            grid.innerHTML = `
                <div class="col-span-full text-center py-16">
                    <div class="glass-card rounded-xl p-8 max-w-md mx-auto">
                        <i class="fas fa-exclamation-triangle text-4xl text-red-500 mb-4"></i>
                        <h3 class="text-xl font-semibold text-white mb-2">加载失败</h3>
                        <p class="text-white/70 text-sm mb-4">${message}</p>
                        <button 
                            onclick="indieNavigator.refreshOpportunities()" 
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                        >
                            重试
                        </button>
                    </div>
                </div>
            `;
        }
    }
    
    showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        if (!container) return;
        
        const toast = document.createElement('div');
        const iconClass = {
            'success': 'fa-check-circle text-green-500',
            'error': 'fa-times-circle text-red-500',
            'warning': 'fa-exclamation-triangle text-yellow-500',
            'info': 'fa-info-circle text-blue-500'
        }[type] || 'fa-info-circle text-blue-500';
        
        toast.className = 'bg-white rounded-lg shadow-lg p-4 mb-2 flex items-center gap-3 min-w-80 transform translate-x-full';
        toast.innerHTML = `
            <i class="fas ${iconClass}"></i>
            <span class="text-gray-800">${message}</span>
            <button onclick="this.parentElement.remove()" class="ml-auto text-gray-400 hover:text-gray-600">
                <i class="fas fa-times"></i>
            </button>
        `;
        
        container.appendChild(toast);
        
        // 动画显示
        setTimeout(() => {
            toast.style.transform = 'translateX(0)';
            toast.style.transition = 'transform 0.3s ease';
        }, 100);
        
        // 自动消失
        setTimeout(() => {
            toast.style.transform = 'translateX(100%)';
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }
}

// 全局函数
function closeOpportunityModal() {
    if (window.indieNavigator) {
        window.indieNavigator.closeOpportunityModal();
    }
}

// 初始化
let indieNavigator;

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    indieNavigator = new IndieNavigator();
    window.indieNavigator = indieNavigator;
});

// 导出为全局变量以便调试
window.IndieNavigator = IndieNavigator;