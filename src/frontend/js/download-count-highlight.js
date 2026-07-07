(function () {
    const DEFAULT_CLASS = 'border-slate-200 bg-slate-100 text-slate-500';

    function badgeClass(value) {
        const count = Number(value);
        if (!Number.isFinite(count)) return DEFAULT_CLASS;
        if (count >= 45) return 'border-rose-300 bg-rose-100 text-rose-800 ring-1 ring-inset ring-rose-200 shadow-sm';
        if (count >= 40) return 'border-orange-300 bg-orange-100 text-orange-800 ring-1 ring-inset ring-orange-200';
        if (count >= 35) return 'border-amber-300 bg-amber-100 text-amber-800';
        if (count >= 30) return 'border-emerald-300 bg-emerald-100 text-emerald-800';
        return DEFAULT_CLASS;
    }

    function badgeText(value) {
        const count = Number(value);
        return Number.isFinite(count) ? `下载 ${count} 次` : '下载 -';
    }

    window.DownloadCountHighlight = {
        badgeClass,
        badgeText,
    };
})();
