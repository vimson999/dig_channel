const PDF_TOOL_BASE_URL = 'http://127.0.0.1:5005';

const statusEl = document.getElementById('service-status');
const refreshBtn = document.getElementById('refresh-status-btn');

document.addEventListener('DOMContentLoaded', () => {
    refreshBtn?.addEventListener('click', checkPdfToolService);
    checkPdfToolService();
});

async function checkPdfToolService() {
    setServiceStatus('checking');
    try {
        await fetch(PDF_TOOL_BASE_URL, {
            mode: 'no-cors',
            cache: 'no-store',
        });
        setServiceStatus('online');
    } catch (error) {
        setServiceStatus('offline');
    }
}

function setServiceStatus(status) {
    if (!statusEl) return;
    const config = {
        checking: {
            className: 'rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-500',
            html: '<i class="fas fa-circle-notch fa-spin mr-2"></i>检查服务中',
        },
        online: {
            className: 'rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-bold text-emerald-700',
            html: '<i class="fas fa-circle-check mr-2"></i>PDF 服务可访问',
        },
        offline: {
            className: 'rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm font-bold text-red-600',
            html: '<i class="fas fa-circle-xmark mr-2"></i>PDF 服务未启动',
        },
    }[status];
    statusEl.className = config.className;
    statusEl.innerHTML = config.html;
}
