// 统一导航组件 - 真正的复用组件
// 一处修改，各处生效

// 所有使用统一导航的业务页面都先经过同一套登录校验。
// 登录页和注册页保持公开，避免每个页面重复维护一份认证逻辑。
const pageAccessGuard = (() => {
    const pathname = window.location.pathname;
    const filename = pathname.split('/').pop().toLowerCase();
    const publicPages = new Set(['login.html', 'login_enhanced.html', 'register.html']);

    // 后端的封面/视频渲染器会直接加载本地 HTML；它不属于对外页面访问。
    if (window.location.protocol === 'file:' || publicPages.has(filename)) {
        return Promise.resolve(true);
    }

    const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
    const showLocalBackendOfflineNotice = () => {
        window.localBackendUnavailable = true;

        const renderNotice = () => {
            if (document.getElementById('local-backend-offline-notice')) return;

            const notice = document.createElement('div');
            notice.id = 'local-backend-offline-notice';
            notice.setAttribute('role', 'status');
            notice.style.cssText = [
                'position:fixed',
                'left:50%',
                'bottom:24px',
                'transform:translateX(-50%)',
                'z-index:10000',
                'max-width:calc(100vw - 32px)',
                'padding:10px 16px',
                'border:1px solid #f59e0b',
                'border-radius:10px',
                'background:#fffbeb',
                'color:#92400e',
                'font:14px/1.5 system-ui,sans-serif',
                'box-shadow:0 8px 24px rgba(15,23,42,.14)'
            ].join(';');
            notice.textContent = '本地后端暂时无法连接，页面已继续打开；恢复后刷新即可。';
            document.body.appendChild(notice);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', renderNotice, {once: true});
        } else {
            renderNotice();
        }
    };
    const buildLoginUrl = (reason = '') => {
        const loginUrl = new URL('/login.html', window.location.origin);
        const returnPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        loginUrl.searchParams.set('next', returnPath || '/');
        if (reason) {
            loginUrl.searchParams.set('reason', reason);
        }
        return loginUrl.toString();
    };

    const redirectToLogin = (reason = '') => {
        if (isLocal) {
            window.location.replace(buildLoginUrl(reason));
        } else {
            document.documentElement.innerHTML = '<head><title>会话已失效</title></head><body style="font-family:system-ui;padding:3rem"><h1>会话已失效</h1><p>请使用管理员保存的私有登录入口重新登录。</p></body>';
        }
        return false;
    };

    const token = localStorage.getItem('access_token');

    // 校验完成前不初始化导航，避免失效 token 看到用户态页面。
    const apiBaseUrl = (window.API_BASE_URL || (
        ['localhost', '127.0.0.1'].includes(window.location.hostname)
            ? 'http://localhost:5001/api/v1'
            : '/api/v1'
    )).replace(/\/$/, '');

    return fetch(`${apiBaseUrl}/auth/verify`, {
        method: 'GET',
        credentials: 'include',
        headers: Object.assign(
            {'Accept': 'application/json'},
            token ? {'Authorization': `Bearer ${token}`} : {}
        )
    }).then(async response => {
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload?.success || !payload?.data?.user) {
            localStorage.removeItem('access_token');
            localStorage.removeItem('user_info');
            return redirectToLogin('session_expired');
        }

        localStorage.setItem('user_info', JSON.stringify(payload.data.user));
        return true;
    }).catch(error => {
        console.error('🔐 [SHARED_NAV] 登录状态校验失败:', error);
        // 网络异常时保留 token：本地免登录环境继续打开页面，线上仍按未验证处理。
        if (isLocal) {
            showLocalBackendOfflineNotice();
            return true;
        }
        return redirectToLogin('verification_failed');
    });
})();

window.pageAccessGuard = pageAccessGuard;

class SharedNavigation {
    constructor() {
        this.currentPage = this.getCurrentPageName();
        this.navigationHTML = null;
        this.init();
    }
    
    init() {
        this.renderNavigation();
        this.renderFooter();
        this.setupEventListeners();
        this.loadUserInfo();
    }
    
    getCurrentPageName() {
        const path = window.location.pathname;
        const filename = path.split('/').pop();
        
        if (filename === 'index.html' || filename === '' || filename === '/') {
            return 'dashboard';
        } else if (filename === 'channels.html') {
            return 'channels';
        } else if (filename === 'videos.html') {
            return 'videos';
        } else if (filename === 'rankings.html') {
            return 'rankings';
        } else if (filename === 'trending-radar.html') {
            return 'trending-radar';
        } else if (filename === 'trending.html') {
            return 'trending';
        } else if (filename === 'zsxq-lab.html') {
            return 'zsxq-lab';
        } else if (filename === 'zsxq-insights.html') {
            return 'zsxq-insights';
        } else if (filename === 'zsxq-analysis.html') {
            return 'zsxq-analysis';
        } else if (filename === 'bilibili-intelligence.html') {
            return 'bilibili-intelligence';
        } else if (filename === 'zsxq-auto-publish.html') {
            return 'zsxq-system-sync';
        } else if (filename === 'document-converter.html') {
            return 'document-converter';
        } else if (filename === 'word-long-image.html') {
            return 'word-long-image';
        } else if (filename === 'pdf-scroll-video.html') {
            return 'pdf-scroll-video';
        } else if (filename === 'tts.html') {
            return 'tts';
        } else if (filename === 'doubao-browser-tts-test.html') {
            return 'doubao-browser-tts-test';
        } else if (filename === 'douyin-publish-test.html') {
            return 'douyin-publish-test';
        } else if (filename === 'wechat-channels-publish-test.html') {
            return 'wechat-channels-publish-test';
        } else if (filename === 'mhtml-pdf.html') {
            return 'mhtml-pdf';
        } else if (filename === 'pdf-tools.html') {
            return 'pdf-tools';
        } else if (filename === 'pdf-merge-workflow.html') {
            return 'pdf-merge-workflow';
        } else if (filename === 'report-video-factory.html') {
            return 'report-video-factory';
        } else if (filename === 'student-id-generator.html') {
            return 'student-id-generator';
        } else if (filename === 'indie-navigator.html') {
            return 'indie-navigator';
        } else if (filename === 'favorites.html') {
            return 'favorites';
        } else if (filename === 'watermark.html' || path.includes('/watermark')) {
            return 'watermark';
        } else if (filename === 'bilibili-publisher.html') {
            return 'bilibili-publisher';
        } else if (filename === 'gemini-test.html') {
            return 'gemini-test';
        } else if (filename === 'gemini-browser-test.html') {
            return 'gemini-browser-test';
        } else if (filename === 'report-audio-workflow-test.html') {
            return 'report-audio-workflow-test';
        } else if (filename === 'report-script-material-workflow-test.html') {
            return 'report-script-material-workflow-test';
        } else if (filename === 'report-stick-figure-video-workflow.html' || filename === 'report-stick-figure-video-workflow-detail.html') {
            return 'report-stick-figure-video-workflow';
        } else if (filename === 'script-video-workflow.html') {
            return 'script-video-workflow';
        } else if (filename === 'report-visual-plan-test.html') {
            return 'report-visual-plan-test';
        } else if (filename === 'report-top10-workflow-test.html') {
            return 'report-top10-workflow-test';
        } else if (filename === 'mp3-video-workflow.html') {
            return 'mp3-video-workflow';
        } else if (filename === 'thumbnail-generator.html') {
            return 'thumbnail-generator';
        } else if (filename === 'finance-cover-generator.html') {
            return 'finance-cover-generator';
        } else if (filename === 'closed-door-card-cover-generator.html') {
            return 'closed-door-card-cover-generator';
        } else if (filename === 'zsxq-system-sync.html') {
            return 'zsxq-system-sync';
        } else if (filename === 'thumbnail-workbench.html') {
            return 'thumbnail-workbench';
        } else if (filename === 'thumbnail-generator-classic.html') {
            return 'thumbnail-generator-classic';
        } else if (filename === 'video-auto-generator.html') {
            return 'video-auto-generator';
        } else if (filename === 'podcast-visualizer.html') {
            return 'podcast-visualizer';
        } else if (filename === 'publisher.html') {
            return 'publisher';
        } else if (filename === 'video-editor.html') {
            return 'video-editor';
        } else if (filename === 'audio-transcription.html') {
            return 'audio-transcription';
        } else if (filename === 'batch-transcription.html') {
            return 'batch-transcription';
        } else if (filename === 'downloads.html') {
            return 'downloads';
        } else if (filename === 'content-retention-cleanup.html') {
            return 'content-retention-cleanup';
        } else if (filename === 'content-pipeline-submit.html') {
            return 'content-pipeline-submit';
        } else if (filename === 'platform-credentials.html') {
            return 'platform-credentials';
        } else if (filename === 'zsxq-history-api-clients.html') {
            return 'zsxq-history-api-clients';
        }
        return 'dashboard';
    }
    
    getPageTitle() {
        const titles = {
            'dashboard': '智能数据分析平台',
            'channels': '频道管理',
            'videos': '视频管理中心',
            'rankings': '频道完整排行榜',
            'trending-radar': '趋势雷达监控',
            'trending': '爆款视频搜索',
            'zsxq-lab': '知识星球内容分析',
            'zsxq-insights': '生财情报台',
            'zsxq-analysis': '知识星球清单分析',
            'bilibili-intelligence': 'B站内容情报台',
            'zsxq-auto-publish': '知识星球任务策略',
            'document-converter': '文档转换工具',
            'word-long-image': 'Word 转长图',
            'pdf-scroll-video': 'PDF 滚动视频',
            'tts': '豆包语音 V3',
            'doubao-browser-tts-test': '豆包网页朗读测试台',
            'douyin-publish-test': '抖音上传测试台',
            'wechat-channels-publish-test': '视频号投递测试台',
            'mhtml-pdf': 'MHTML转PDF工具',
            'pdf-tools': 'PDF 工具平台',
            'pdf-merge-workflow': 'PDF 合并工作台',
            'report-video-factory': '研报导图视频工厂',
            'watermark': 'PDF智能去水印',
            'student-id-generator': '学生证生成器',
            'indie-navigator': '商机发现平台',
            'favorites': '我的收藏',
            'bilibili-publisher': 'Bilibili 发布工作台',
            'gemini-test': 'Gemini AI 测试台',
            'gemini-browser-test': '研报 PDF Gem 测试台',
            'report-audio-workflow-test': '研报转语音编排台',
            'report-script-material-workflow-test': '研报基础文案物料台',
            'report-stick-figure-video-workflow': '研报 PDF 转火柴人视频',
            'script-video-workflow': '文案到成片',
            'report-visual-plan-test': '研报视觉编排测试台',
            'report-top10-workflow-test': '今日研报 Top10 编排台',
            'mp3-video-workflow': 'MP3 视频编排台',
            'thumbnail-workbench': '听见资本封面工作台',
            'thumbnail-generator': '研报封面生成器',
            'finance-cover-generator': '金融类视频封面生成器',
            'closed-door-card-cover-generator': '内资闭门会封面生成器',
            'zsxq-system-sync': '知识星球任务策略',
            'thumbnail-generator-classic': '经典研报封面生成器',
            'video-auto-generator': 'AI 视频自动生成',
            'podcast-visualizer': '播客视觉化',
            'publisher': '一键智能发布台',
            'external-batch': '批量任务台',
            'video-editor': '高级剪辑工作台',
            'audio-transcription': '音频转文字',
            'batch-transcription': '批量音频转文案',
            'downloads': '素材下载中心',
            'content-retention-cleanup': '历史文件清理',
            'content-pipeline-submit': '网页内容投递',
            'platform-credentials': '平台凭据管理',
            'zsxq-history-api-clients': '知识星球 API 调用方'
        };
        return titles[this.currentPage] || '智能数据分析平台';
    }
    
    getNavigationHTML() {
        const hasStatusBar = Boolean(document.getElementById('status-bar'));
        const hasToastContainer = Boolean(document.getElementById('toast-container'));
        const statusBarHTML = hasStatusBar ? '' : `
        <!-- 状态提示栏 -->
        <div id="status-bar" class="hidden fixed top-20 right-6 z-[9999] max-w-[min(360px,calc(100vw-2rem))] rounded-full bg-white/95 border border-blue-100 text-blue-700 px-4 py-2.5 text-sm shadow-lg backdrop-blur">
            <div class="flex items-center justify-center whitespace-nowrap">
                <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600 mr-2"></div>
                <span id="status-text">正在加载...</span>
            </div>
        </div>
        `;
        const toastContainerHTML = hasToastContainer ? '' : `
        <!-- Toast 容器 -->
        <div id="toast-container" class="fixed top-4 right-4 z-[9999] space-y-2"></div>
        `;

        return `
        ${statusBarHTML}
        ${toastContainerHTML}

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
                    
                    <!-- 桌面导航菜单 -->
                    <nav class="hidden md:flex items-center gap-3" role="navigation" aria-label="主导航">
                        ${this.getMenuItems()}
                    </nav>
                    
                    <!-- 移动端菜单按钮 -->
                    <button class="md:hidden p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors" id="mobile-menu-toggle">
                        <i class="fas fa-bars text-xl"></i>
                    </button>
                    
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
        </header>
        
        <!-- 移动端导航菜单面板 -->
        <div id="mobile-menu-panel" class="md:hidden fixed inset-0 bg-black bg-opacity-50 z-50 hidden">
            <div class="bg-white h-full w-80 transform -translate-x-full transition-transform duration-300" id="mobile-menu-content">
                <div class="p-6">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3">
                            <div class="w-8 h-8 bg-gradient-to-br from-red-500 to-red-600 rounded-lg flex items-center justify-center">
                                <i class="fab fa-youtube text-white text-sm"></i>
                            </div>
                            <span class="font-bold text-gray-800">Shioa</span>
                        </div>
                        <button class="p-2 text-gray-500 hover:text-gray-700 rounded-lg" id="mobile-menu-close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <nav class="space-y-2">
                        ${this.getMobileMenuItems()}
                    </nav>
                </div>
            </div>
        </div>`;
    }
    
    getMenuStructure() {
        return [
            {
                id: 'dashboard',
                name: '首页',
                icon: 'fas fa-home',
                href: 'index.html',
                standalone: true
            },
            {
                id: 'data',
                name: '数据中心',
                icon: 'fas fa-database',
                dropdown: true,
                items: [
                    {
                        id: 'channels',
                        name: '频道管理',
                        icon: 'fas fa-tv',
                        href: 'channels.html'
                    },
                    {
                        id: 'videos',
                        name: '视频库',
                        icon: 'fas fa-play',
                        href: 'videos.html'
                    },
                    {
                        id: 'rankings',
                        name: '完整排行榜',
                        icon: 'fas fa-ranking-star',
                        href: 'rankings.html'
                    },
                    {
                        id: 'favorites',
                        name: '我的收藏',
                        icon: 'fas fa-heart',
                        href: 'favorites.html'
                    }
                ]
            },
            {
                id: 'insights',
                name: '选题洞察',
                icon: 'fas fa-chart-line',
                dropdown: true,
                items: [
                    {
                        id: 'trending',
                        name: '爆款搜索',
                        icon: 'fas fa-fire',
                        href: 'trending.html'
                    },
                    {
                        id: 'trending-radar',
                        name: '趋势雷达',
                        icon: 'fas fa-radar-line',
                        href: 'trending-radar.html'
                    },
                    {
                        id: 'indie-navigator',
                        name: '商机发现',
                        icon: 'fas fa-compass',
                        href: 'indie-navigator.html'
                    },
                    {
                        id: 'zsxq-insights',
                        name: '生财情报台',
                        icon: 'fas fa-chart-simple',
                        href: 'zsxq-insights.html'
                    },
                    {
                        id: 'zsxq-analysis',
                        name: '清单分析与历史',
                        icon: 'fas fa-chart-pie',
                        href: 'zsxq-analysis.html'
                    },
                    {
                        id: 'bilibili-intelligence',
                        name: 'B站情报台',
                        icon: 'fas fa-satellite-dish',
                        href: 'bilibili-intelligence.html'
                    },
                    {
                        id: 'zsxq-lab',
                        name: '知识星球内容分析',
                        icon: 'fas fa-circle-nodes',
                        href: 'zsxq-lab.html'
                    },
                    {
                        id: 'zsxq-system-sync',
                        name: '知识星球任务策略',
                        icon: 'fas fa-clock-rotate-left',
                        href: 'zsxq-system-sync.html'
                    },
                ]
            },
            {
                id: 'production',
                name: '生产发布',
                icon: 'fas fa-rocket',
                dropdown: true,
                items: [
                    {
                        id: 'publisher',
                        name: '一键发布台',
                        icon: 'fas fa-rocket',
                        href: 'publisher.html'
                    },
                    {
                        id: 'video-editor',
                        name: '高级剪辑',
                        icon: 'fas fa-scissors',
                        href: 'video-editor.html'
                    },
                    {
                        id: 'external-batch',
                        name: '批量任务台',
                        icon: 'fas fa-list-check',
                        href: 'http://127.0.0.1:5005/batch',
                        external: true
                    },
                    {
                        id: 'bilibili-publisher',
                        name: 'B站发布',
                        icon: 'fab fa-bilibili',
                        href: 'bilibili-publisher.html'
                    },
                    {
                        id: 'downloads',
                        name: '素材下载',
                        icon: 'fas fa-download',
                        href: 'downloads.html'
                    }
                ]
            },
            {
                id: 'ai-creation',
                name: 'AI创作',
                icon: 'fas fa-wand-magic-sparkles',
                dropdown: true,
                items: [
                    {
                        id: 'audio-transcription',
                        name: '音频转文字',
                        icon: 'fas fa-file-audio',
                        href: 'audio-transcription.html'
                    },
                    {
                        id: 'batch-transcription',
                        name: '音频转文案',
                        icon: 'fas fa-file-audio',
                        href: 'batch-transcription.html'
                    },
                    {
                        id: 'video-auto-generator',
                        name: 'AI 视频合成',
                        icon: 'fas fa-film',
                        href: 'video-auto-generator.html'
                    },
                    {
                        id: 'podcast-visualizer',
                        name: '播客视觉化',
                        icon: 'fas fa-podcast',
                        href: 'podcast-visualizer.html'
                    },
                    {
                        id: 'report-video-factory',
                        name: '研报导图视频工厂',
                        icon: 'fas fa-diagram-project',
                        href: 'report-video-factory.html'
                    },
                    {
                        id: 'report-audio-workflow-test',
                        name: '研报转语音',
                        icon: 'fas fa-file-audio',
                        href: 'report-audio-workflow-test.html'
                    },
                    {
                        id: 'report-script-material-workflow-test',
                        name: '研报基础文案',
                        icon: 'fas fa-file-lines',
                        href: 'report-script-material-workflow-test.html'
                    },
                    {
                        id: 'report-stick-figure-video-workflow',
                        name: '研报转火柴人视频',
                        icon: 'fas fa-person-running',
                        href: 'report-stick-figure-video-workflow.html'
                    },
                    {
                        id: 'script-video-workflow',
                        name: '文案到成片',
                        icon: 'fas fa-clapperboard',
                        href: 'script-video-workflow.html'
                    },
                    {
                        id: 'report-visual-plan-test',
                        name: '研报视觉编排',
                        icon: 'fas fa-wand-magic-sparkles',
                        href: 'report-visual-plan-test.html'
                    },
                    {
                        id: 'report-top10-workflow-test',
                        name: '研报 Top10',
                        icon: 'fas fa-ranking-star',
                        href: 'report-top10-workflow-test.html'
                    },
                    {
                        id: 'mp3-video-workflow',
                        name: 'MP3 视频编排',
                        icon: 'fas fa-wave-square',
                        href: 'mp3-video-workflow.html'
                    },
                    {
                        id: 'tts',
                        name: '语音合成',
                        icon: 'fas fa-microphone',
                        href: 'tts.html'
                    },
                    {
                        id: 'doubao-browser-tts-test',
                        name: '网页朗读测试',
                        icon: 'fas fa-volume-high',
                        href: 'doubao-browser-tts-test.html'
                    },
                    {
                        id: 'thumbnail-workbench',
                        name: '封面工作台',
                        icon: 'fas fa-magic',
                        href: 'thumbnail-workbench.html'
                    },
                    {
                        id: 'finance-cover-generator',
                        name: '金融封面',
                        icon: 'fas fa-chart-pie',
                        href: 'finance-cover-generator.html'
                    },
                    {
                        id: 'closed-door-card-cover-generator',
                        name: '闭门会封面',
                        icon: 'fas fa-chart-line',
                        href: 'closed-door-card-cover-generator.html'
                    }
                ]
            },
            {
                id: 'tools',
                name: '工具箱',
                icon: 'fas fa-toolbox',
                dropdown: true,
                items: [
                    {
                        id: 'pdf-tools',
                        name: 'PDF 工具平台',
                        icon: 'fas fa-file-shield',
                        href: 'pdf-tools.html'
                    },
                    {
                        id: 'pdf-merge-workflow',
                        name: 'PDF 合并工作台',
                        icon: 'fas fa-object-group',
                        href: 'pdf-merge-workflow.html'
                    },
                    {
                        id: 'celery-monitor',
                        name: 'Celery 控制台',
                        icon: 'fas fa-gauge-high',
                        href: 'celery-monitor.html'
                    },
                    {
                        id: 'content-retention-cleanup',
                        name: '历史文件清理',
                        icon: 'fas fa-broom',
                        href: 'content-retention-cleanup.html'
                    },
                    {
                        id: 'content-pipeline-submit',
                        name: '网页内容投递',
                        icon: 'fas fa-cloud-arrow-up',
                        href: 'content-pipeline-submit.html'
                    },
                    {
                        id: 'platform-credentials',
                        name: '平台凭据',
                        icon: 'fas fa-key',
                        href: 'platform-credentials.html'
                    },
                    {
                        id: 'zsxq-history-api-clients',
                        name: '星球 API 调用方',
                        icon: 'fas fa-network-wired',
                        href: 'zsxq-history-api-clients.html'
                    },
                    {
                        id: 'douyin-publish-test',
                        name: '抖音上传测试',
                        icon: 'fab fa-tiktok',
                        href: 'douyin-publish-test.html'
                    },
                    {
                        id: 'wechat-channels-publish-test',
                        name: '视频号投递测试',
                        icon: 'fab fa-weixin',
                        href: 'wechat-channels-publish-test.html'
                    },
                    {
                        id: 'document-converter',
                        name: '文档转换',
                        icon: 'fas fa-file-exchange',
                        href: 'document-converter.html'
                    },
                    {
                        id: 'word-long-image',
                        name: 'Word 转长图',
                        icon: 'fas fa-file-word',
                        href: 'word-long-image.html'
                    },
                    {
                        id: 'pdf-scroll-video',
                        name: 'PDF 滚动视频',
                        icon: 'fas fa-file-video',
                        href: 'pdf-scroll-video.html'
                    },
                    {
                        id: 'mhtml-pdf',
                        name: 'MHTML 转 PDF',
                        icon: 'fas fa-file-pdf',
                        href: 'mhtml-pdf.html'
                    },
                    {
                        id: 'watermark',
                        name: 'PDF 去水印',
                        icon: 'fas fa-tint',
                        href: 'watermark.html'
                    },
                    {
                        id: 'student-id-generator',
                        name: '学生证生成',
                        icon: 'fas fa-id-card',
                        href: 'student-id-generator.html'
                    },
                    {
                        id: 'thumbnail-generator',
                        name: '通用封面旧版',
                        icon: 'fas fa-image',
                        href: 'thumbnail-generator.html'
                    },
                    {
                        id: 'thumbnail-generator-classic',
                        name: '经典封面旧版',
                        icon: 'fas fa-file-image',
                        href: 'thumbnail-generator-classic.html'
                    },
                    {
                        id: 'gemini-test',
                        name: 'Gemini 调试',
                        icon: 'fas fa-robot',
                        href: 'gemini-test.html'
                    },
                    {
                        id: 'gemini-browser-test',
                        name: '研报 PDF Gem',
                        icon: 'fas fa-window-restore',
                        href: 'gemini-browser-test.html'
                    }
                ]
            }
        ];
    }

    getLinkAttributes(item) {
        if (!item.external) {
            return '';
        }

        const title = `${item.name}（外部工具）`;
        return ` target="_blank" rel="noopener noreferrer" title="${title}"`;
    }

    getExternalIndicator(item) {
        if (!item.external) {
            return '';
        }

        return '<i class="fas fa-arrow-up-right-from-square ml-2 text-[10px] opacity-60"></i>';
    }
    
    getMenuItems() {
        const menuStructure = this.getMenuStructure();
        
        return menuStructure.map(section => {
            if (section.standalone) {
                // 独立菜单项
                const isActive = section.id === this.currentPage;
                const activeClass = isActive 
                    ? 'text-primary-600 font-medium bg-primary-50 border border-primary-200' 
                    : 'text-gray-600 hover:text-primary-600 hover:bg-gray-50';
                
                return `
                    <a href="${section.href}" class="nav-link ${activeClass} px-3 py-2 rounded-lg transition-colors"${this.getLinkAttributes(section)}>
                        <i class="${section.icon} mr-2"></i>${section.name}${this.getExternalIndicator(section)}
                    </a>
                `;
            } else if (section.dropdown) {
                // 下拉菜单组
                const hasActiveChild = section.items.some(item => item.id === this.currentPage);
                const groupActiveClass = hasActiveChild 
                    ? 'text-primary-600 font-medium' 
                    : 'text-gray-600';
                
                const dropdownItems = section.items.map(item => {
                    const isActive = item.id === this.currentPage;
                    const itemActiveClass = isActive ? 'bg-primary-50 text-primary-600' : 'text-gray-700 hover:bg-gray-50';
                    
                    return `
                        <a href="${item.href}" class="block px-4 py-2 text-sm ${itemActiveClass} transition-colors rounded-md"${this.getLinkAttributes(item)}>
                            <i class="${item.icon} mr-2"></i>${item.name}${this.getExternalIndicator(item)}
                        </a>
                    `;
                }).join('');
                
                return `
                    <div class="relative group">
                        <button class="nav-dropdown-trigger ${groupActiveClass} hover:text-primary-600 px-3 py-2 rounded-lg transition-colors flex items-center">
                            <i class="${section.icon} mr-2"></i>${section.name}
                            <i class="fas fa-chevron-down ml-1 text-xs transition-transform group-hover:rotate-180"></i>
                        </button>
                        <div class="absolute top-full left-0 mt-1 w-56 bg-white rounded-lg shadow-lg border border-gray-200 invisible group-hover:visible opacity-0 group-hover:opacity-100 transition-all duration-200 z-50">
                            <div class="py-2">
                                ${dropdownItems}
                            </div>
                        </div>
                    </div>
                `;
            }
        }).join('');
    }
    
    getMobileMenuItems() {
        const menuStructure = this.getMenuStructure();
        
        return menuStructure.map(section => {
            if (section.standalone) {
                const isActive = section.id === this.currentPage;
                const activeClass = isActive ? 'bg-primary-50 text-primary-600' : 'text-gray-700 hover:bg-gray-50';
                
                return `
                    <a href="${section.href}" class="block px-4 py-3 rounded-lg ${activeClass} transition-colors"${this.getLinkAttributes(section)}>
                        <i class="${section.icon} mr-3"></i>${section.name}${this.getExternalIndicator(section)}
                    </a>
                `;
            } else if (section.dropdown) {
                const hasActiveChild = section.items.some(item => item.id === this.currentPage);
                const sectionItems = section.items.map(item => {
                    const isActive = item.id === this.currentPage;
                    const itemActiveClass = isActive ? 'bg-primary-50 text-primary-600' : 'text-gray-600 hover:bg-gray-50';
                    
                    return `
                        <a href="${item.href}" class="block px-6 py-2 text-sm rounded-md ${itemActiveClass} transition-colors"${this.getLinkAttributes(item)}>
                            <i class="${item.icon} mr-2"></i>${item.name}${this.getExternalIndicator(item)}
                        </a>
                    `;
                }).join('');
                
                return `
                    <div class="space-y-1">
                        <div class="px-4 py-2 text-sm font-medium text-gray-500 uppercase tracking-wider">
                            <i class="${section.icon} mr-2"></i>${section.name}
                        </div>
                        <div class="space-y-1">
                            ${sectionItems}
                        </div>
                    </div>
                `;
            }
        }).join('');
    }
    
    renderNavigation() {
        // 在页面开头插入导航HTML
        document.body.insertAdjacentHTML('afterbegin', this.getNavigationHTML());
        console.log('✅ [SHARED_NAV] 统一导航组件已渲染');
    }

    getFooterHTML() {
        return `
        <footer class="mt-12 border-t border-gray-200 bg-white" role="contentinfo">
            <div class="mx-auto flex max-w-[1440px] flex-col gap-2 px-6 py-6 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
                <span>© ${new Date().getFullYear()} Shioa 内容生产平台</span>
                <span>任务可追踪 · 凭据加密存储 · 发布状态如实记录</span>
            </div>
        </footer>`;
    }

    renderFooter() {
        let container = document.getElementById('footer-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'footer-container';
            document.body.appendChild(container);
        }
        if (container.dataset.sharedFooterMounted === '1') return;
        container.dataset.sharedFooterMounted = '1';
        container.innerHTML = this.getFooterHTML();
    }
    
    setupEventListeners() {
        // 移动端菜单切换
        const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
        const mobileMenuPanel = document.getElementById('mobile-menu-panel');
        const mobileMenuContent = document.getElementById('mobile-menu-content');
        const mobileMenuClose = document.getElementById('mobile-menu-close');
        
        if (mobileMenuToggle && mobileMenuPanel && mobileMenuContent) {
            // 打开移动端菜单
            mobileMenuToggle.addEventListener('click', () => {
                mobileMenuPanel.classList.remove('hidden');
                setTimeout(() => {
                    mobileMenuContent.classList.remove('-translate-x-full');
                }, 10);
            });
            
            // 关闭移动端菜单
            const closeMobileMenu = () => {
                mobileMenuContent.classList.add('-translate-x-full');
                setTimeout(() => {
                    mobileMenuPanel.classList.add('hidden');
                }, 300);
            };
            
            if (mobileMenuClose) {
                mobileMenuClose.addEventListener('click', closeMobileMenu);
            }
            
            // 点击背景关闭菜单
            mobileMenuPanel.addEventListener('click', (e) => {
                if (e.target === mobileMenuPanel) {
                    closeMobileMenu();
                }
            });
            
            // ESC键关闭菜单
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && !mobileMenuPanel.classList.contains('hidden')) {
                    closeMobileMenu();
                }
            });
        }
        
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
document.addEventListener('DOMContentLoaded', async () => {
    const canAccessPage = await pageAccessGuard;
    if (!canAccessPage) {
        return;
    }

    // 只把真正的站点 banner 当作已挂载导航；业务卡片和弹窗也会使用 header 标签。
    if (!document.querySelector('header[role="banner"]')) {
        window.sharedNavigation = new SharedNavigation();
    }
});

// 导出为全局变量
window.SharedNavigation = SharedNavigation;
