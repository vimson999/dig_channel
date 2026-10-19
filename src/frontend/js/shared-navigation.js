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

// Capture while this script is executing; DOMContentLoaded has no currentScript.
const sharedNavigationStylesheet = new URL(
    '../css/shared-navigation.css?v=20261004-2',
    document.currentScript?.src || new URL('js/shared-navigation.js', window.location.href)
).href;

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
        const filename = window.location.pathname.split('/').pop();
        const aliases = {
            '': 'index.html',
            'zsxq-auto-publish.html': 'zsxq-system-sync.html',
            'report-stick-figure-video-workflow-detail.html': 'report-stick-figure-video-workflow.html',
            'watermark': 'watermark.html'
        };
        const href = aliases[filename] || filename;
        const item = this.getMenuStructure().flatMap(section => section.items || [section])
            .find(entry => !entry.external && entry.href === href);
        return item?.id || null;
    }

    getPageTitle() {
        if (this.currentPage === 'dashboard') return '内容情报与生产平台';
        const item = this.getMenuStructure().flatMap(section => section.items || [section])
            .find(entry => entry.id === this.currentPage);
        return item?.name || '内容情报与生产平台';
    }

    getNavigationHTML() {
        const statusBarHTML = document.getElementById('status-bar') ? '' : `
        <!-- 状态提示栏 -->
        <div id="status-bar" class="hidden fixed top-20 right-6 z-[9999] max-w-[min(360px,calc(100vw-2rem))] rounded-full bg-white/95 border border-blue-100 text-blue-700 px-4 py-2.5 text-sm shadow-lg backdrop-blur">
            <div class="flex items-center justify-center whitespace-nowrap">
                <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600 mr-2"></div>
                <span id="status-text">正在加载...</span>
            </div>
        </div>
        `;
        const toastHTML = document.getElementById('toast-container') ? '' :
            '<div id="toast-container" class="fixed top-4 right-4 z-[9999] space-y-2"></div>';
        return `
        <link rel="stylesheet" href="${sharedNavigationStylesheet}">
        ${statusBarHTML}${toastHTML}
        <header class="shared-navigation" role="banner">
            <div class="shared-nav-shell">
                <div class="shared-nav-brand">
                    <span class="shared-nav-logo"><i class="fab fa-youtube" aria-hidden="true"></i></span>
                    <div><strong>Shioa</strong><p title="${this.getPageTitle()}">${this.getPageTitle()}</p></div>
                </div>
                <nav class="shared-nav-desktop" aria-label="主导航">${this.getMenuItems()}</nav>
                <div class="shared-nav-actions">
                    <button type="button" id="mobile-menu-toggle" class="shared-nav-icon shared-nav-mobile-toggle"
                        aria-label="打开导航" aria-haspopup="dialog" aria-controls="mobile-menu-panel" aria-expanded="false">
                        <i class="fas fa-bars" aria-hidden="true"></i>
                    </button>
                    <div class="shared-nav-desktop">${this.getSystemMenu()}</div>
                    <button type="button" id="navigation-help-toggle" class="shared-nav-icon"
                        aria-label="导航帮助" title="导航帮助" aria-haspopup="dialog" aria-controls="navigation-help">
                        <i class="fas fa-question-circle" aria-hidden="true"></i>
                    </button>
                    <div class="shared-nav-disclosure">
                        <button type="button" class="shared-nav-user" data-nav-trigger aria-expanded="false"
                            aria-controls="nav-panel-account" aria-label="用户菜单">
                            <span class="user-avatar">U</span><span class="user-name">用户</span>
                            <i class="fas fa-chevron-down" aria-hidden="true"></i>
                        </button>
                        <div class="shared-nav-popover shared-nav-account" id="nav-panel-account" hidden>
                            <strong class="user-name">用户</strong>
                            <p class="shared-nav-muted">账户功能</p>
                            <button type="button" disabled title="暂未开放">个人资料 · 暂未开放</button>
                            <button type="button" disabled title="暂未开放">账户设置 · 暂未开放</button>
                            <button type="button" class="shared-nav-logout" onclick="logout()">退出登录</button>
                        </div>
                    </div>
                </div>
            </div>
        </header>
        <dialog id="mobile-menu-panel" class="shared-nav-dialog shared-nav-drawer" aria-labelledby="mobile-menu-title">
            <div class="shared-nav-dialog-header"><strong id="mobile-menu-title">全部导航</strong>
                <button type="button" id="mobile-menu-close" class="shared-nav-icon" aria-label="关闭导航">✕</button></div>
            <nav class="shared-nav-mobile" aria-label="移动导航">${this.getMobileMenuItems()}</nav>
        </dialog>
        <dialog id="navigation-help" class="shared-nav-dialog shared-nav-help" aria-labelledby="navigation-help-title">
            <div class="shared-nav-dialog-header"><strong id="navigation-help-title">功能怎么找</strong>
                <button type="button" id="navigation-help-close" class="shared-nav-icon" aria-label="关闭帮助">✕</button></div>
            <div class="shared-nav-help-content">
                <p>所有原有功能和链接均保留，按工作步骤重新分组。</p>
                <dl>
                    <dt>数据与素材</dt><dd>管理频道、视频、收藏，下载和投递素材。</dd>
                    <dt>选题情报</dt><dd>发现爆款、趋势、商机和星球情报。</dd>
                    <dt>内容制作</dt><dd>转写、研报、视频及封面，按产物选择流程。</dd>
                    <dt>发布与任务</dt><dd>发布内容，管理批量任务和星球策略。</dd>
                    <dt>工具箱</dt><dd>文档处理、实验室及旧版工具。</dd>
                    <dt>系统管理</dt><dd>右上角齿轮：任务队列、凭据、API 与历史清理。</dd>
                </dl>
                <p>点击栏目展开；键盘可用 Enter / 空格、Tab、↓ 和 Esc 操作。</p>
                <p class="shared-nav-muted">带“外部”标记的批量任务台需要本机 5005 服务运行。实验室和旧版工具未删除，使用时请留意页面说明。</p>
            </div>
        </dialog>`;
    }

    getMenuStructure() {
        return [
            {
                "id": "dashboard",
                "name": "首页",
                "icon": "fas fa-home",
                "href": "index.html",
                "standalone": true
            },
            {
                "id": "data",
                "name": "数据与素材",
                "icon": "fas fa-database",
                "dropdown": true,
                "items": [
                    {
                        "id": "channels",
                        "name": "频道管理",
                        "icon": "fas fa-tv",
                        "href": "channels.html",
                        "group": "数据管理"
                    },
                    {
                        "id": "videos",
                        "name": "视频库",
                        "icon": "fas fa-play",
                        "href": "videos.html",
                        "group": "数据管理"
                    },
                    {
                        "id": "rankings",
                        "name": "频道排行榜",
                        "icon": "fas fa-ranking-star",
                        "href": "rankings.html",
                        "group": "数据管理"
                    },
                    {
                        "id": "favorites",
                        "name": "我的收藏",
                        "icon": "fas fa-heart",
                        "href": "favorites.html",
                        "group": "数据管理"
                    },
                    {
                        "id": "downloads",
                        "name": "素材下载",
                        "icon": "fas fa-download",
                        "href": "downloads.html",
                        "group": "素材采集"
                    },
                    {
                        "id": "content-pipeline-submit",
                        "name": "网页内容投递",
                        "icon": "fas fa-cloud-arrow-up",
                        "href": "content-pipeline-submit.html",
                        "group": "素材采集"
                    }
                ]
            },
            {
                "id": "insights",
                "name": "选题情报",
                "icon": "fas fa-chart-line",
                "dropdown": true,
                "items": [
                    {
                        "id": "trending",
                        "name": "爆款搜索",
                        "icon": "fas fa-fire",
                        "href": "trending.html",
                        "group": "发现机会"
                    },
                    {
                        "id": "trending-radar",
                        "name": "趋势雷达",
                        "icon": "fas fa-radar-line",
                        "href": "trending-radar.html",
                        "group": "发现机会"
                    },
                    {
                        "id": "indie-navigator",
                        "name": "商机发现",
                        "icon": "fas fa-compass",
                        "href": "indie-navigator.html",
                        "group": "发现机会"
                    },
                    {
                        "id": "bilibili-intelligence",
                        "name": "B站情报台",
                        "icon": "fas fa-satellite-dish",
                        "href": "bilibili-intelligence.html",
                        "group": "发现机会"
                    },
                    {
                        "id": "zsxq-insights",
                        "name": "生财情报台",
                        "icon": "fas fa-chart-simple",
                        "href": "zsxq-insights.html",
                        "group": "星球情报"
                    },
                    {
                        "id": "zsxq-analysis",
                        "name": "星球清单分析",
                        "icon": "fas fa-chart-pie",
                        "href": "zsxq-analysis.html",
                        "group": "星球情报"
                    },
                    {
                        "id": "zsxq-lab",
                        "name": "知识星球内容分析",
                        "icon": "fas fa-circle-nodes",
                        "href": "zsxq-lab.html",
                        "group": "星球情报"
                    },
                    {
                        "id": "zsxq-import",
                        "name": "星球数据导入",
                        "icon": "fas fa-file-import",
                        "href": "zsxq-import.html",
                        "group": "星球情报"
                    }
                ]
            },
            {
                "id": "creation",
                "name": "内容制作",
                "icon": "fas fa-wand-magic-sparkles",
                "dropdown": true,
                "items": [
                    {
                        "id": "audio-transcription",
                        "name": "音频转文字",
                        "icon": "fas fa-file-audio",
                        "href": "audio-transcription.html",
                        "group": "音频与文字"
                    },
                    {
                        "id": "batch-transcription",
                        "name": "批量音频转写",
                        "icon": "fas fa-file-audio",
                        "href": "batch-transcription.html",
                        "group": "音频与文字"
                    },
                    {
                        "id": "tts",
                        "name": "语音合成",
                        "icon": "fas fa-microphone",
                        "href": "tts.html",
                        "group": "音频与文字"
                    },
                    {
                        "id": "report-script-material-workflow-test",
                        "name": "研报基础文案",
                        "icon": "fas fa-file-lines",
                        "href": "report-script-material-workflow-test.html",
                        "group": "研报制作"
                    },
                    {
                        "id": "report-audio-workflow-test",
                        "name": "研报转语音",
                        "icon": "fas fa-file-audio",
                        "href": "report-audio-workflow-test.html",
                        "group": "研报制作"
                    },
                    {
                        "id": "report-video-factory",
                        "name": "研报导图视频工厂",
                        "icon": "fas fa-diagram-project",
                        "href": "report-video-factory.html",
                        "group": "研报制作"
                    },
                    {
                        "id": "report-stick-figure-video-workflow",
                        "name": "研报视觉视频",
                        "icon": "fas fa-person-running",
                        "href": "report-stick-figure-video-workflow.html",
                        "group": "研报制作"
                    },
                    {
                        "id": "report-top10-workflow-test",
                        "name": "研报严选（Top10）",
                        "icon": "fas fa-ranking-star",
                        "href": "report-top10-workflow-test.html",
                        "group": "研报制作"
                    },
                    {
                        "id": "video-auto-generator",
                        "name": "AI 视频合成",
                        "icon": "fas fa-film",
                        "href": "video-auto-generator.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "script-video-workflow",
                        "name": "文案到成片",
                        "icon": "fas fa-clapperboard",
                        "href": "script-video-workflow.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "mp3-video-workflow",
                        "name": "MP3 视频编排",
                        "icon": "fas fa-wave-square",
                        "href": "mp3-video-workflow.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "podcast-visualizer",
                        "name": "播客视觉化",
                        "icon": "fas fa-podcast",
                        "href": "podcast-visualizer.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "video-editor",
                        "name": "高级剪辑",
                        "icon": "fas fa-scissors",
                        "href": "video-editor.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "pdf-scroll-video",
                        "name": "PDF 滚动视频",
                        "icon": "fas fa-file-video",
                        "href": "pdf-scroll-video.html",
                        "group": "视频制作"
                    },
                    {
                        "id": "thumbnail-workbench",
                        "name": "封面工作台",
                        "icon": "fas fa-magic",
                        "href": "thumbnail-workbench.html",
                        "group": "封面设计"
                    },
                    {
                        "id": "finance-cover-generator",
                        "name": "金融封面",
                        "icon": "fas fa-chart-pie",
                        "href": "finance-cover-generator.html",
                        "group": "封面设计"
                    },
                    {
                        "id": "closed-door-card-cover-generator",
                        "name": "闭门会封面",
                        "icon": "fas fa-chart-line",
                        "href": "closed-door-card-cover-generator.html",
                        "group": "封面设计"
                    }
                ]
            },
            {
                "id": "publishing",
                "name": "发布与任务",
                "icon": "fas fa-rocket",
                "dropdown": true,
                "items": [
                    {
                        "id": "publisher",
                        "name": "一键发布台",
                        "icon": "fas fa-rocket",
                        "href": "publisher.html",
                        "group": "发布与调度"
                    },
                    {
                        "id": "bilibili-publisher",
                        "name": "B站发布",
                        "icon": "fab fa-bilibili",
                        "href": "bilibili-publisher.html",
                        "group": "发布与调度"
                    },
                    {
                        "id": "external-batch",
                        "name": "批量任务台",
                        "icon": "fas fa-list-check",
                        "href": "http://127.0.0.1:5005/batch",
                        "external": true,
                        "group": "发布与调度"
                    },
                    {
                        "id": "zsxq-system-sync",
                        "name": "知识星球任务策略",
                        "icon": "fas fa-clock-rotate-left",
                        "href": "zsxq-system-sync.html",
                        "group": "发布与调度"
                    }
                ]
            },
            {
                "id": "tools",
                "name": "工具箱",
                "icon": "fas fa-toolbox",
                "dropdown": true,
                "items": [
                    {
                        "id": "pdf-tools",
                        "name": "PDF 工具平台",
                        "icon": "fas fa-file-shield",
                        "href": "pdf-tools.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "pdf-merge-workflow",
                        "name": "PDF 合并工作台",
                        "icon": "fas fa-object-group",
                        "href": "pdf-merge-workflow.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "document-converter",
                        "name": "文档转换",
                        "icon": "fas fa-file-exchange",
                        "href": "document-converter.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "word-long-image",
                        "name": "Word 转长图",
                        "icon": "fas fa-file-word",
                        "href": "word-long-image.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "mhtml-pdf",
                        "name": "MHTML 转 PDF",
                        "icon": "fas fa-file-pdf",
                        "href": "mhtml-pdf.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "watermark",
                        "name": "PDF 去水印",
                        "icon": "fas fa-tint",
                        "href": "watermark.html",
                        "group": "文档处理"
                    },
                    {
                        "id": "student-id-generator",
                        "name": "学生证生成",
                        "icon": "fas fa-id-card",
                        "href": "student-id-generator.html",
                        "group": "其他工具"
                    },
                    {
                        "id": "doubao-browser-tts-test",
                        "name": "网页朗读测试",
                        "icon": "fas fa-volume-high",
                        "href": "doubao-browser-tts-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "report-visual-plan-test",
                        "name": "研报视觉编排",
                        "icon": "fas fa-wand-magic-sparkles",
                        "href": "report-visual-plan-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "douyin-publish-test",
                        "name": "抖音上传测试",
                        "icon": "fab fa-tiktok",
                        "href": "douyin-publish-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "wechat-channels-publish-test",
                        "name": "视频号投递测试",
                        "icon": "fab fa-weixin",
                        "href": "wechat-channels-publish-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "gemini-test",
                        "name": "Gemini 调试",
                        "icon": "fas fa-robot",
                        "href": "gemini-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "gemini-browser-test",
                        "name": "研报 PDF Gem",
                        "icon": "fas fa-window-restore",
                        "href": "gemini-browser-test.html",
                        "group": "实验室"
                    },
                    {
                        "id": "thumbnail-generator",
                        "name": "通用封面旧版",
                        "icon": "fas fa-image",
                        "href": "thumbnail-generator.html",
                        "group": "旧版工具"
                    },
                    {
                        "id": "thumbnail-generator-classic",
                        "name": "经典封面旧版",
                        "icon": "fas fa-file-image",
                        "href": "thumbnail-generator-classic.html",
                        "group": "旧版工具"
                    }
                ]
            },
            {
                "id": "system",
                "name": "系统管理",
                "icon": "fas fa-cog",
                "dropdown": true,
                "utility": true,
                "items": [
                    {
                        "id": "celery-monitor",
                        "name": "任务队列（Celery）",
                        "icon": "fas fa-gauge-high",
                        "href": "celery-monitor.html",
                        "group": "系统管理"
                    },
                    {
                        "id": "platform-credentials",
                        "name": "平台凭据",
                        "icon": "fas fa-key",
                        "href": "platform-credentials.html",
                        "group": "系统管理"
                    },
                    {
                        "id": "zsxq-history-api-clients",
                        "name": "星球 API 调用方",
                        "icon": "fas fa-network-wired",
                        "href": "zsxq-history-api-clients.html",
                        "group": "系统管理"
                    },
                    {
                        "id": "content-retention-cleanup",
                        "name": "历史文件清理",
                        "icon": "fas fa-broom",
                        "href": "content-retention-cleanup.html",
                        "group": "系统管理"
                    }
                ]
            }
        ];
    }

    getLinkAttributes(item) {
        return item.external ? ' target="_blank" rel="noopener noreferrer" title="外部工具，需要本机服务运行"' : '';
    }

    renderNavLink(item) {
        return `<a href="${item.href}" class="shared-nav-link" ${item.id === this.currentPage ? 'aria-current="page"' : ''}${this.getLinkAttributes(item)}>
            <i class="${item.icon}" aria-hidden="true"></i><span>${item.name}</span>
            ${item.external ? '<small>外部 ↗</small>' : ''}</a>`;
    }

    renderGroups(section) {
        const groups = new Map();
        section.items.forEach(item => {
            if (!groups.has(item.group)) groups.set(item.group, []);
            groups.get(item.group).push(item);
        });
        return [...groups].map(([name, items]) => `<section class="shared-nav-group">
            <h2>${name}</h2>${items.map(item => this.renderNavLink(item)).join('')}</section>`).join('');
    }

    renderDisclosure(section, iconOnly = false) {
        const active = section.items.some(item => item.id === this.currentPage);
        return `<div class="shared-nav-disclosure">
            <button type="button" class="nav-dropdown-trigger ${active ? 'is-current' : ''}"
                data-nav-trigger aria-expanded="false" aria-controls="nav-panel-${section.id}"
                ${iconOnly ? 'aria-label="系统管理" title="系统管理"' : ''}>
                <i class="${section.icon}" aria-hidden="true"></i>${iconOnly ? '' : section.name}
                ${iconOnly ? '' : '<i class="fas fa-chevron-down shared-nav-chevron" aria-hidden="true"></i>'}
            </button>
            <div class="shared-nav-popover ${section.items.length > 7 ? 'shared-nav-wide' : ''}"
                id="nav-panel-${section.id}" hidden>
                <div class="shared-nav-panel-heading"><strong>${section.name}</strong><span>${section.items.length} 个入口</span></div>
                <div class="shared-nav-groups">${this.renderGroups(section)}</div>
            </div></div>`;
    }

    getMenuItems() {
        return this.getMenuStructure().filter(section => !section.utility).map(section =>
            section.standalone ? this.renderNavLink(section) : this.renderDisclosure(section)).join('');
    }

    getSystemMenu() {
        return this.renderDisclosure(this.getMenuStructure().find(section => section.utility), true);
    }

    getMobileMenuItems() {
        return this.getMenuStructure().map(section => section.standalone ? this.renderNavLink(section) :
            `<details class="shared-nav-mobile-section" ${section.items.some(item => item.id === this.currentPage) ? 'open' : ''}>
                <summary><i class="${section.icon}" aria-hidden="true"></i>${section.name}<small>${section.items.length}</small></summary>
                ${this.renderGroups(section)}</details>`).join('');
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
        const triggers = [...document.querySelectorAll('[data-nav-trigger]')];
        let opened = null;
        const closeMenu = (restoreFocus = false) => {
            if (!opened) return;
            const trigger = opened;
            opened = null;
            document.getElementById(trigger.getAttribute('aria-controls')).hidden = true;
            trigger.setAttribute('aria-expanded', 'false');
            if (restoreFocus) trigger.focus();
        };
        const openMenu = (trigger, focusFirst = false) => {
            closeMenu();
            const panel = document.getElementById(trigger.getAttribute('aria-controls'));
            panel.hidden = false;
            // Center on the trigger, then keep the panel inside the viewport.
            // Absolute coordinates are relative to the centered navigation shell.
            const anchor = trigger.getBoundingClientRect();
            const width = panel.getBoundingClientRect().width;
            const origin = panel.offsetParent.getBoundingClientRect().left;
            const gutter = 16;
            const preferredLeft = anchor.left + (anchor.width - width) / 2;
            const left = Math.max(gutter, Math.min(preferredLeft,
                document.documentElement.clientWidth - width - gutter));
            panel.style.left = `${left - origin}px`;
            panel.style.right = 'auto';
            trigger.setAttribute('aria-expanded', 'true');
            opened = trigger;
            if (focusFirst) panel.querySelector('a, button:not([disabled])')?.focus();
        };
        triggers.forEach(trigger => {
            // Native buttons already translate Enter and Space into click.
            trigger.addEventListener('click', () => {
                if (opened === trigger) closeMenu();
                else openMenu(trigger);
            });
            trigger.addEventListener('keydown', event => {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    openMenu(trigger, true);
                }
            });
        });
        const dismissOutside = event => {
            if (!opened) return;
            const panel = document.getElementById(opened.getAttribute('aria-controls'));
            if (!opened.contains(event.target) && !panel.contains(event.target)) closeMenu();
        };
        document.addEventListener('click', dismissOutside);
        document.addEventListener('focusin', dismissOutside);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && opened) {
                event.preventDefault();
                closeMenu(true);
            }
        });

        // Native dialogs supply focus containment, Escape and inert background behavior.
        const bindDialog = (dialogId, triggerId, closeId) => {
            const dialog = document.getElementById(dialogId);
            const trigger = document.getElementById(triggerId);
            const close = document.getElementById(closeId);
            if (!dialog || !trigger || !close) return;
            let previousOverflow = '';
            trigger.addEventListener('click', () => {
                if (dialog.open) return;
                closeMenu();
                previousOverflow = document.body.style.overflow;
                dialog.showModal();
                document.body.style.overflow = 'hidden';
                trigger.setAttribute('aria-expanded', 'true');
                close.focus();
            });
            close.addEventListener('click', () => dialog.close());
            dialog.addEventListener('click', event => {
                if (event.target === dialog) dialog.close();
            });
            dialog.addEventListener('close', () => {
                document.body.style.overflow = previousOverflow;
                trigger.setAttribute('aria-expanded', 'false');
                trigger.focus();
            });
        };
        bindDialog('mobile-menu-panel', 'mobile-menu-toggle', 'mobile-menu-close');
        bindDialog('navigation-help', 'navigation-help-toggle', 'navigation-help-close');
        window.addEventListener('resize', () => {
            closeMenu();
            const mobile = document.getElementById('mobile-menu-panel');
            if (mobile?.open && window.matchMedia('(min-width: 1200px)').matches) mobile.close();
        });
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
