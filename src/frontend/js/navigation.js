// 通用导航组件
// 统一所有页面的导航样式和功能

class NavigationComponent {
    constructor() {
        this.currentPage = this.getCurrentPageName();
        this.isIndexPage = this.currentPage === 'dashboard';
        this.init();
    }
    
    init() {
        this.updateNavigationContent();
        this.setupEventListeners();
        this.setActiveNavigation();
        this.loadUserInfo();
    }
    
    getCurrentPageName() {
        const path = window.location.pathname;
        const filename = path.split('/').pop();
        
        // 根据文件名确定当前页面
        if (filename === 'index.html' || filename === '' || filename === '/') {
            return 'dashboard';
        } else if (filename === 'videos.html') {
            return 'videos';
        } else if (filename === 'trending-radar.html') {
            return 'trending-radar';
        } else if (filename === 'trending.html') {
            return 'trending';
        }
        return 'dashboard'; // 默认
    }
    
    updateNavigationContent() {
        // 更安全的方式：只更新导航菜单内容，不替换整个header
        this.updateNavigationMenu();
        this.updatePageTitle();
        this.injectCommonComponents();
    }
    
    updateNavigationMenu() {
        // 查找导航菜单 - 尝试多种选择器
        let nav = document.querySelector('nav.hidden.md\\:flex');
        if (!nav) {
            nav = document.querySelector('header nav');
        }
        if (!nav) {
            nav = document.querySelector('nav');
        }
        
        if (!nav) {
            console.error('Navigation: 找不到导航菜单');
            return;
        }
        
        // 更新导航菜单内容
        nav.innerHTML = this.getNavigationMenuItems();
    }
    
    updatePageTitle() {
        // 更新页面标题和副标题
        const titleElement = document.querySelector('h1');
        const subtitleElement = document.querySelector('h1 + p');
        
        if (titleElement) {
            titleElement.textContent = 'Shioa';
        }
        
        if (subtitleElement) {
            subtitleElement.textContent = this.getPageSubtitle();
        }
    }
    
    getNavigationHTML() {
        return `
    <!-- 状态提示栏 -->
    <div id="status-bar" class="hidden fixed top-0 left-0 right-0 z-50 bg-blue-500 text-white p-3 text-center">
        <div class="flex items-center justify-center">
            <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2"></div>
            <span id="status-text">正在加载...</span>
        </div>
    </div>

    <!-- Toast 容器 -->
    <div id="toast-container" class="fixed top-4 right-4 z-[9999] space-y-2"></div>

    <!-- 主导航栏 -->
    <header class="bg-white/80 backdrop-blur-lg border-b border-gray-200/50 sticky top-0 z-40">
        <div class="max-w-[1440px] mx-auto px-6 py-4">
            <div class="flex items-center justify-between">
                <!-- Logo 和标题 -->
                <div class="flex items-center gap-4">
                    <div class="w-10 h-10 bg-gradient-to-br from-red-500 to-red-600 rounded-xl flex items-center justify-center shadow-lg">
                        <i class="fab fa-youtube text-white text-xl"></i>
                    </div>
                    <div>
                        <h1 class="text-xl font-bold text-gray-800">Shioa</h1>
                        <p class="text-sm text-gray-500">${this.getPageSubtitle()}</p>
                    </div>
                </div>
                
                <!-- 导航菜单 -->
                <nav class="hidden md:flex items-center gap-6">
                    ${this.getNavigationMenuItems()}
                </nav>
                
                <!-- 移动端菜单按钮 -->
                <button class="md:hidden p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors" onclick="toggleMobileMenu()">
                    <i class="fas fa-bars"></i>
                </button>
                
                <!-- 工具栏 -->
                <div class="flex items-center gap-3">
                    <!-- 搜索框 -->
                    <div class="relative hidden lg:block">
                        <input type="text" id="global-search" placeholder="搜索..." 
                               class="w-64 pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:border-primary-500 focus:ring-2 focus:ring-primary-200 focus:outline-none text-sm">
                        <i class="fas fa-search absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400"></i>
                    </div>
                    
                    <!-- 刷新按钮 -->
                    <button onclick="refreshCurrentPage()" class="p-2 text-gray-500 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-colors" title="刷新数据">
                        <i class="fas fa-sync-alt"></i>
                    </button>
                    
                    <!-- 设置按钮 -->
                    <button class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors" title="设置">
                        <i class="fas fa-cog"></i>
                    </button>
                    
                    <!-- 帮助按钮 -->
                    <button class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors" title="帮助">
                        <i class="fas fa-question-circle"></i>
                    </button>
                    
                    <!-- 用户下拉菜单 -->
                    <div class="relative group">
                        <button class="flex items-center gap-2 p-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">
                            <div class="w-8 h-8 bg-gradient-to-br from-blue-400 to-blue-600 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-medium user-avatar">U</span>
                            </div>
                            <span class="hidden md:block text-sm font-medium user-name">用户</span>
                            <i class="fas fa-chevron-down text-xs text-gray-400"></i>
                        </button>
                        
                        <!-- 下拉菜单 -->
                        <div class="absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-lg border border-gray-200 invisible group-hover:visible opacity-0 group-hover:opacity-100 transition-all duration-200 z-50">
                            <div class="py-2">
                                <div class="px-4 py-2 border-b border-gray-100">
                                    <p class="text-sm font-medium text-gray-900 user-name">用户</p>
                                    <p class="text-xs text-gray-500">管理员</p>
                                </div>
                                <a href="#" class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors">
                                    <i class="fas fa-user mr-2"></i>个人资料
                                </a>
                                <a href="#" class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors">
                                    <i class="fas fa-cog mr-2"></i>账户设置
                                </a>
                                <hr class="my-1">
                                <button onclick="logout()" class="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors">
                                    <i class="fas fa-sign-out-alt mr-2"></i>退出登录
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </header>`;
    }
    
    getPageSubtitle() {
        const subtitles = {
            'dashboard': '智能数据分析平台',
            'videos': '视频管理中心',
            'trending-radar': '全网热榜实时监控',
            'trending': '爆款视频搜索'
        };
        return subtitles[this.currentPage] || '数据分析平台';
    }
    
    getNavigationMenuItems() {
        const menuItems = [
            {
                id: 'dashboard',
                name: '首页',
                icon: 'fas fa-chart-line',
                href: 'index.html',
                onClick: this.isIndexPage ? 'switchPage(\'dashboard\')' : null
            },
            {
                id: 'channels',
                name: '频道管理',
                icon: 'fas fa-tv',
                href: 'index.html#channels',
                onClick: this.isIndexPage ? 'switchPage(\'channels\')' : null
            },
            {
                id: 'videos',
                name: '视频管理',
                icon: 'fas fa-play',
                href: 'videos.html'
            },
            {
                id: 'trending-radar',
                name: '趋势雷达',
                icon: 'fas fa-radar-line',
                href: 'trending-radar.html'
            },
            {
                id: 'trending',
                name: '爆款视频',
                icon: 'fas fa-fire',
                href: 'trending.html'
            }
        ];
        
        return menuItems.map(item => {
            const isActive = this.isActiveMenuItem(item.id);
            const activeClass = isActive 
                ? 'text-primary-600 font-medium bg-primary-50 border border-primary-200' 
                : 'text-gray-600 hover:text-primary-600 hover:bg-gray-50';
            
            // 对于主页，使用onclick而不是href
            const linkAttrs = this.isIndexPage && item.onClick
                ? `href="#${item.id}" onclick="${item.onClick}"`
                : `href="${item.href}"`;
            
            return `
                <a ${linkAttrs} class="nav-link ${activeClass} px-3 py-2 rounded-lg transition-colors">
                    <i class="${item.icon} mr-2"></i>${item.name}
                </a>
            `;
        }).join('');
    }
    
    isActiveMenuItem(itemId) {
        // 特殊处理频道管理（在首页中）
        if (itemId === 'channels' && this.currentPage === 'dashboard') {
            return window.location.hash === '#channels';
        }
        return itemId === this.currentPage;
    }
    
    injectCommonComponents() {
        // 确保Toast容器存在
        if (!document.getElementById('toast-container')) {
            const toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'fixed top-4 right-4 z-[9999] space-y-2';
            document.body.appendChild(toastContainer);
        }
        
        // 确保状态栏存在
        if (!document.getElementById('status-bar')) {
            const statusBar = document.createElement('div');
            statusBar.id = 'status-bar';
            statusBar.className = 'hidden fixed top-0 left-0 right-0 z-50 bg-blue-500 text-white p-3 text-center';
            statusBar.innerHTML = `
                <div class="flex items-center justify-center">
                    <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2"></div>
                    <span id="status-text">正在加载...</span>
                </div>
            `;
            document.body.insertBefore(statusBar, document.body.firstChild);
        }
    }
    
    setupEventListeners() {
        // 全局搜索功能
        const globalSearch = document.getElementById('global-search');
        if (globalSearch) {
            globalSearch.addEventListener('input', (e) => {
                this.handleGlobalSearch(e.target.value);
            });
        }
        
        // 监听hash变化（用于单页应用导航）
        window.addEventListener('hashchange', () => {
            this.setActiveNavigation();
        });
    }
    
    setActiveNavigation() {
        // 重新设置导航高亮
        const navLinks = document.querySelectorAll('.nav-link');
        navLinks.forEach(link => {
            link.className = link.className.replace(/text-primary-600|font-medium|bg-primary-50|border-primary-200/g, '');
            if (!link.className.includes('text-gray-600')) {
                link.className += ' text-gray-600 hover:text-primary-600 hover:bg-gray-50';
            }
        });
        
        // 设置当前页面高亮
        const currentNavItem = this.getCurrentNavItem();
        if (currentNavItem) {
            currentNavItem.className = currentNavItem.className.replace(/text-gray-600|hover:text-primary-600|hover:bg-gray-50/g, '');
            currentNavItem.className += ' text-primary-600 font-medium bg-primary-50 border border-primary-200';
        }
    }
    
    getCurrentNavItem() {
        if (this.currentPage === 'dashboard' && window.location.hash === '#channels') {
            return document.querySelector('a[href="index.html#channels"]');
        }
        
        const navSelectors = {
            'dashboard': 'a[href="index.html"]',
            'videos': 'a[href="videos.html"]',
            'trending-radar': 'a[href="trending-radar.html"]',
            'trending': 'a[href="trending.html"]'
        };
        
        const selector = navSelectors[this.currentPage];
        return selector ? document.querySelector(selector) : null;
    }
    
    loadUserInfo() {
        // 加载用户信息
        if (window.displayUserInfo && typeof window.displayUserInfo === 'function') {
            window.displayUserInfo();
        } else if (window.api && window.api.getCurrentUser) {
            const user = window.api.getCurrentUser();
            if (user) {
                this.updateUserDisplay(user);
            }
        }
    }
    
    updateUserDisplay(user) {
        const userNameElements = document.querySelectorAll('.user-name');
        userNameElements.forEach(element => {
            element.textContent = user.display_name || user.username;
        });

        const userAvatarElements = document.querySelectorAll('.user-avatar');
        userAvatarElements.forEach(element => {
            const firstLetter = (user.display_name || user.username).charAt(0).toUpperCase();
            element.textContent = firstLetter;
        });
    }
    
    handleGlobalSearch(query) {
        // 全局搜索功能 - 可以根据当前页面执行不同的搜索逻辑
        if (!query.trim()) return;
        
        console.log(`全局搜索: ${query}`);
        
        // 根据当前页面执行搜索
        if (this.currentPage === 'videos' && window.videoManager) {
            // 视频页面搜索
            const searchInput = document.getElementById('search-videos');
            if (searchInput) {
                searchInput.value = query;
                searchInput.dispatchEvent(new Event('input'));
            }
        } else if (this.currentPage === 'trending-radar' && window.trendingRadar) {
            // 趋势雷达搜索
            const searchInput = document.getElementById('search-trends');
            if (searchInput) {
                searchInput.value = query;
                searchInput.dispatchEvent(new Event('input'));
            }
        }
        // 可以添加其他页面的搜索逻辑
    }
}

// 全局函数定义
window.refreshCurrentPage = function() {
    if (window.location.pathname.includes('videos.html') && window.videoManager) {
        window.videoManager.loadVideos();
    } else if (window.location.pathname.includes('trending-radar.html') && window.trendingRadar) {
        window.trendingRadar.refreshAllData();
    } else {
        // 其他页面的刷新逻辑
        window.location.reload();
    }
};

window.toggleMobileMenu = function() {
    // 移动端菜单切换逻辑
    console.log('移动端菜单切换');
    // TODO: 实现移动端菜单
};

// 在DOM加载完成后自动初始化导航
document.addEventListener('DOMContentLoaded', () => {
    // 延迟一点初始化，确保其他脚本先加载
    setTimeout(() => {
        window.navigationComponent = new NavigationComponent();
    }, 100);
});