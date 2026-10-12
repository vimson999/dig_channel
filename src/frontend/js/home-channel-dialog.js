// Shared lifecycle for the homepage's chart/table channel picker.
(function () {
    let trigger, onClose, previousOverflow, generation = 0;
    const modal = () => document.getElementById('channelFilterModal');
    const focusable = () => Array.from(modal().querySelectorAll('button, input, select, a[href], [tabindex="0"]'))
        .filter(element => !element.disabled && element.getClientRects().length);
    const dialog = {
        open(context, close) {
            generation++;
            trigger = document.activeElement;
            onClose = close;
            previousOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            document.getElementById('channelFilterDescription').textContent = `选择要在${context}中显示的频道`;
            modal().classList.remove('hidden');
            document.getElementById('channelFilterContent').classList.remove('scale-95', 'opacity-0');
            document.getElementById('channelFilterContent').focus();
            document.getElementById('channelFilterList').innerHTML = '<div class="p-6 text-center text-slate-500">正在加载频道...</div>';
            dialog.setBusy(true);
            return generation;
        },
        isActive(token) { return token === generation && !modal().classList.contains('hidden'); },
        setBusy(busy) {
            modal().querySelectorAll('button').forEach(button => {
                if (!button.getAttribute('onclick')?.includes('closeChannelFilterModal')) button.disabled = busy;
            });
        },
        focus() {
            if (!modal().classList.contains('hidden')) document.getElementById('channelFilterSearch').focus();
        },
        close() {
            generation++;
            dialog.setBusy(false);
            modal().classList.add('hidden');
            document.body.style.overflow = previousOverflow || '';
            trigger?.focus();
            onClose = null;
        },
    };
    document.addEventListener('keydown', event => {
        if (!modal() || modal().classList.contains('hidden')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            onClose?.();
        } else if (event.key === 'Tab') {
            const items = focusable();
            const first = items[0], last = items[items.length - 1];
            if (!first) { event.preventDefault(); return; }
            if (!items.includes(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
                event.preventDefault(); (event.shiftKey ? last : first).focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first.focus();
            }
        }
    });
    document.addEventListener('click', event => {
        if (event.target === modal()) onClose?.();
    });
    window.homeChannelDialog = dialog;
})();
