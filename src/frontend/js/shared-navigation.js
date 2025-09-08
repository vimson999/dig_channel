// 统一导航组件 - 真正的复用组件
// 一处修改，各处生效

class SharedNavigation {
    constructor() {
        this.currentPage = this.getCurrentPageName();
        this.navigationHTML = null;
        this.init();
    }
    
    init() {
        this.renderNavigation();
        this.setupEventListeners();
        this.loadUserInfo();
    }
    
    getCurrentPageName() {
        const path = window.location.pathname;
        const filename = path.split('/').pop();
        
        if (filename === 'index.html' || filename === '' || filename === '/') {
            return 'dashboard';
        } else if (filename === 'videos.html') {
            return 'videos';
        } else if (filename === 'trending-radar.html') {
            return 'trending-radar';
        } else if (filename === 'trending.html') {
            return 'trending';
        } else if (filename === 'document-converter.html') {
            return 'document-converter';
        } else if (filename === 'tts.html') {
            return 'tts';
        } else if (filename === 'mhtml-pdf.html') {
            return 'mhtml-pdf';
        } else if (filename === 'student-id-generator.html') {
            return 'student-id-generator';
        } else if (filename === 'indie-navigator.html') {
            return 'indie-navigator';
        } else if (filename === 'favorites.html') {
            return 'favorites';
        }
        return 'dashboard';
    }
    
    getPageTitle() {
        const titles = {
            'dashboard': '智能数据分析平台',
            'videos': '视频管理中心',
            'trending-radar': '趋势雷达监控',
            'trending': '爆款视频搜索',
            'document-converter': '文档转换工具',
            'tts': 'AI语音合成服务',
            'mhtml-pdf': 'MHTML转PDF工具',
            'student-id-generator': '学生证生成器',
            'indie-navigator': '商机发现平台',
            'favorites': '我的收藏'
        };
        return titles[this.currentPage] || '智能数据分析平台';
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
        <header class="bg-white/80 backdrop-blur-lg border-b border-gray-200/50 sticky top-0 z-40" role="banner">
            <div class="max-w-[1440px] mx-auto px-6 py-4">
                <div class="flex items-center justify-between">
                    <!-- Logo 和标题 -->
                    <div class="flex items-center gap-4">
                        <div class="w-10 h-10 bg-gradient-to-br from-red-500 to-red-600 rounded-xl flex items-center justify-center shadow-lg">
                            <i class="fab fa-youtube text-white text-xl"></i>
                        </div>
                        <div>
                            <h1 class="text-xl font-bold text-gray-800">Shioa</h1>
                            <p class="text-sm text-gray-500">${this.getPageTitle()}</p>
                        </div>
                    </div>
                    
                    <!-- 导航菜单 -->
                    <nav class="hidden md:flex items-center gap-6" role="navigation" aria-label="主导航">
                        ${this.getMenuItems()}
                    </nav>
                    
                    <!-- 用户操作区 -->
                    <div class="flex items-center gap-3">
                        <button class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors" title="设置">
                            <i class="fas fa-cog"></i>
                        </button>
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
    
    getMenuItems() {
        const menuItems = [
            {
                id: 'dashboard',
                name: '首页',
                icon: 'fas fa-chart-line',
                href: 'index.html'
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
            },
            {
                id: 'favorites',
                name: '我的收藏',
                icon: 'fas fa-heart',
                href: 'favorites.html'
            },
            {
                id: 'document-converter',
                name: '文档转换',
                icon: 'fas fa-file-exchange',
                href: 'document-converter.html'
            },
            {
                id: 'tts',
                name: '文本转语音',
                icon: 'fas fa-microphone',
                href: 'tts.html'
            },
            {
                id: 'mhtml-pdf',
                name: 'MHTML转PDF',
                icon: 'fas fa-file-pdf',
                href: 'mhtml-pdf.html'
            },
            {
                id: 'student-id-generator',
                name: '学生证生成器',
                icon: 'fas fa-id-card',
                href: 'student-id-generator.html'
            },
            {
                id: 'indie-navigator',
                name: '商机发现',
                icon: 'fas fa-compass',
                href: 'indie-navigator.html'
            }
        ];
        
        return menuItems.map(item => {
            const isActive = item.id === this.currentPage;
            const activeClass = isActive 
                ? 'text-primary-600 font-medium bg-primary-50 border border-primary-200' 
                : 'text-gray-600 hover:text-primary-600 hover:bg-gray-50';
            
            const ariaCurrent = isActive ? ' aria-current="page"' : '';
            
            return `
                <a href="${item.href}" class="nav-link ${activeClass} px-3 py-2 rounded-lg transition-colors"${ariaCurrent}>
                    <i class="${item.icon} mr-2" aria-hidden="true"></i>${item.name}
                </a>
            `;
        }).join('');
    }
    
    renderNavigation() {
        // 在页面开头插入导航HTML
        document.body.insertAdjacentHTML('afterbegin', this.getNavigationHTML());
        console.log('✅ [SHARED_NAV] 统一导航组件已渲染');
    }
    
    setupEventListeners() {
        // 设置事件监听器
        console.log('🔧 [SHARED_NAV] 事件监听器已设置');
    }
    
    loadUserInfo() {
        // 加载用户信息 - 与现有API集成
        if (window.api && window.api.getCurrentUser) {
            try {
                const user = window.api.getCurrentUser();
                if (user) {
                    this.updateUserDisplay(user);
                }
            } catch (error) {
                console.warn('🔑 [SHARED_NAV] 用户信息加载失败:', error);
            }
        }
    }
    
    updateUserDisplay(user) {
        const userNameElements = document.querySelectorAll('.user-name');
        userNameElements.forEach(element => {
            element.textContent = user.display_name || user.username || '用户';
        });

        const userAvatarElements = document.querySelectorAll('.user-avatar');
        userAvatarElements.forEach(element => {
            const firstLetter = (user.display_name || user.username || 'U').charAt(0).toUpperCase();
            element.textContent = firstLetter;
        });
    }
    
    // 静态方法：添加新菜单项（未来扩展用）
    static addMenuItem(menuItem) {
        console.log('📋 [SHARED_NAV] 添加菜单项:', menuItem);
        // 实现动态添加菜单项的逻辑
    }
    
    // 静态方法：更新页面标题
    static updatePageTitle(title) {
        const subtitleElement = document.querySelector('header p');
        if (subtitleElement) {
            subtitleElement.textContent = title;
        }
    }
}

// 全局函数
window.logout = function() {
    if (window.api && window.api.logout) {
        window.api.logout();
    } else {
        alert('退出登录功能');
    }
};

// 自动初始化
document.addEventListener('DOMContentLoaded', () => {
    // 只有在页面没有导航的情况下才创建
    if (!document.querySelector('header')) {
        window.sharedNavigation = new SharedNavigation();
    }
});

// 导出为全局变量
window.SharedNavigation = SharedNavigation;