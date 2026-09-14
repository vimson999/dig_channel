(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.SearchableCombobox = api.SearchableCombobox;
        root.VideoPageUtils = api;
    }
})(typeof window !== 'undefined' ? window : null, function () {
    function normalizeSearchText(value) {
        return String(value || '')
            .normalize('NFKC')
            .toLocaleLowerCase()
            .trim()
            .replace(/\s+/g, ' ');
    }

    function filterSearchableOptions(options, query) {
        const terms = normalizeSearchText(query).split(' ').filter(Boolean);
        if (!terms.length) return options.slice();

        return options.filter((option) => {
            const haystack = normalizeSearchText(`${option.label || ''} ${option.keywords || ''}`);
            return terms.every((term) => haystack.includes(term));
        });
    }

    function updateRecentOptionValues(values, selectedValue, limit = 5) {
        const normalizedValue = String(selectedValue || '');
        const uniqueValues = Array.from(new Set(
            (values || []).map((value) => String(value || '')).filter(Boolean),
        ));
        if (!normalizedValue) return uniqueValues.slice(0, limit);
        return [normalizedValue, ...uniqueValues.filter((value) => value !== normalizedValue)]
            .slice(0, limit);
    }

    function buildVisibleOptionSet(options, query, config = {}) {
        const normalizedQuery = normalizeSearchText(query);
        const matches = filterSearchableOptions(options, query);

        if (!normalizedQuery && config.showAll) {
            return {
                items: matches,
                total: matches.length,
                hiddenCount: 0,
            };
        }

        if (!normalizedQuery && config.idleMode === 'recent') {
            const defaultItem = options.find((item) => !item.value);
            const optionByValue = new Map(options.map((item) => [String(item.value), item]));
            const maxRecentItems = config.maxRecentItems || 5;
            const recentItems = updateRecentOptionValues(
                config.recentValues,
                config.selectedValue,
                maxRecentItems,
            )
                .map((value) => optionByValue.get(value))
                .filter(Boolean);
            const recentValueSet = new Set(recentItems.map((item) => String(item.value)));
            const fallbackItems = options
                .map((item, index) => ({item, index}))
                .filter(({item}) => item.value && !recentValueSet.has(String(item.value)))
                .sort((left, right) => {
                    const popularityDifference = Number(right.item.popularity || 0)
                        - Number(left.item.popularity || 0);
                    return popularityDifference || left.index - right.index;
                })
                .slice(0, Math.max(0, maxRecentItems - recentItems.length))
                .map(({item}) => item);
            const items = [defaultItem, ...recentItems, ...fallbackItems].filter(Boolean);
            return {
                items,
                total: matches.length,
                hiddenCount: Math.max(0, matches.length - items.length),
            };
        }

        const maxResults = Number.isFinite(config.maxResults)
            ? Math.max(1, config.maxResults)
            : matches.length;
        const items = matches.slice(0, maxResults);
        return {
            items,
            total: matches.length,
            hiddenCount: Math.max(0, matches.length - items.length),
        };
    }

    function createLatestRequestGate() {
        let current = 0;
        return {
            next() {
                current += 1;
                return current;
            },
            isCurrent(requestId) {
                return requestId === current;
            },
        };
    }

    function buildFavoritePayload(cardData, detailData) {
        const data = {...(cardData || {}), ...(detailData || {})};
        return {
            ...data,
            channel_title: data.channel_name || data.channel_title || '',
            channel_id: data.channel_id || '',
            channel_subscriber_count: data.channel_subscriber_count || 0,
            channel_video_count: data.channel_video_count || 0,
            channel_view_count: data.channel_view_count || 0,
            channel_thumbnail_url: data.channel_thumbnail_url || '',
            channel_published_at: data.channel_published_at || null,
        };
    }

    class SearchableCombobox {
        constructor(select, options = {}) {
            if (!select) throw new Error('SearchableCombobox requires a select element');

            this.select = select;
            this.options = options;
            this.items = [];
            this.filteredItems = [];
            this.activeIndex = -1;
            this.isOpen = false;
            this.showAllOptions = false;
            this.uid = `${select.id || 'searchable-select'}-combobox`;
            this.build();
            this.bind();
            this.refresh();
        }

        build() {
            const wrapper = document.createElement('div');
            wrapper.className = 'searchable-combobox';

            const trigger = document.createElement('button');
            trigger.type = 'button';
            trigger.className = 'searchable-combobox__trigger';
            trigger.setAttribute('aria-haspopup', 'listbox');
            trigger.setAttribute('aria-expanded', 'false');
            trigger.setAttribute('aria-controls', `${this.uid}-listbox`);

            const value = document.createElement('span');
            value.className = 'searchable-combobox__value';

            const chevron = document.createElement('i');
            chevron.className = 'fas fa-chevron-down searchable-combobox__chevron';
            chevron.setAttribute('aria-hidden', 'true');

            trigger.append(value, chevron);

            const panel = document.createElement('div');
            panel.className = 'searchable-combobox__panel';
            panel.hidden = true;

            const searchWrap = document.createElement('div');
            searchWrap.className = 'searchable-combobox__search-wrap';
            const searchIcon = document.createElement('i');
            searchIcon.className = 'fas fa-search';
            searchIcon.setAttribute('aria-hidden', 'true');
            const search = document.createElement('input');
            search.type = 'search';
            search.className = 'searchable-combobox__search';
            search.placeholder = this.options.searchPlaceholder || '搜索选项';
            search.autocomplete = 'off';
            search.setAttribute('aria-label', this.options.searchLabel || search.placeholder);
            search.setAttribute('aria-controls', `${this.uid}-listbox`);
            searchWrap.append(searchIcon, search);

            const list = document.createElement('div');
            list.id = `${this.uid}-listbox`;
            list.className = 'searchable-combobox__list';
            list.setAttribute('role', 'listbox');
            list.setAttribute('aria-label', this.select.getAttribute('aria-label') || this.options.searchLabel || '选项');

            const empty = document.createElement('div');
            empty.className = 'searchable-combobox__empty';
            empty.textContent = this.options.emptyText || '没有匹配结果';
            empty.hidden = true;

            const summary = document.createElement('div');
            summary.className = 'searchable-combobox__summary';
            summary.setAttribute('aria-live', 'polite');
            summary.hidden = true;

            const summaryText = document.createElement('span');
            summaryText.className = 'searchable-combobox__summary-text';

            const summaryAction = document.createElement('button');
            summaryAction.type = 'button';
            summaryAction.className = 'searchable-combobox__summary-action';
            summaryAction.hidden = true;
            summary.append(summaryText, summaryAction);

            panel.append(searchWrap, list, empty, summary);
            wrapper.append(trigger, panel);
            this.select.insertAdjacentElement('afterend', wrapper);
            this.select.classList.add('searchable-combobox__native');
            this.select.setAttribute('aria-hidden', 'true');
            this.select.tabIndex = -1;

            this.wrapper = wrapper;
            this.trigger = trigger;
            this.value = value;
            this.panel = panel;
            this.search = search;
            this.list = list;
            this.empty = empty;
            this.summary = summary;
            this.summaryText = summaryText;
            this.summaryAction = summaryAction;
        }

        bind() {
            this.trigger.addEventListener('click', () => this.toggle());
            this.trigger.addEventListener('keydown', (event) => {
                if (['Enter', ' ', 'ArrowDown'].includes(event.key)) {
                    event.preventDefault();
                    this.open();
                }
            });
            this.search.addEventListener('input', () => {
                this.render(this.search.value);
            });
            this.search.addEventListener('keydown', (event) => this.handleSearchKeydown(event));
            this.summaryAction.addEventListener('click', () => {
                this.showAllOptions = !this.showAllOptions;
                this.render('');
                this.list.scrollTop = 0;
                this.search.focus();
            });
            document.addEventListener('pointerdown', (event) => {
                if (this.isOpen && !this.wrapper.contains(event.target)) this.close();
            });
        }

        refresh() {
            this.items = Array.from(this.select.options).map((option) => ({
                value: option.value,
                label: option.textContent.trim(),
                keywords: option.dataset.category || '',
                popularity: Number(option.dataset.videoCount || 0),
                disabled: option.disabled,
            }));
            const selected = this.select.options[this.select.selectedIndex] || this.select.options[0];
            this.value.textContent = selected ? selected.textContent.trim() : (this.options.placeholder || '请选择');
            this.trigger.disabled = this.select.disabled;
            this.render(this.isOpen ? this.search.value : '');
        }

        render(query) {
            const recentValues = this.loadRecentValues();
            const visibleSet = buildVisibleOptionSet(this.items, query, {
                idleMode: this.options.idleMode,
                maxRecentItems: this.options.maxRecentItems,
                maxResults: this.options.maxResults,
                recentValues,
                selectedValue: this.select.value,
                showAll: this.showAllOptions,
            });
            this.filteredItems = visibleSet.items;
            this.list.replaceChildren();
            this.empty.hidden = this.filteredItems.length > 0;
            this.updateSummary(query, visibleSet);
            const selectedIndex = this.filteredItems.findIndex(
                (item) => !item.disabled && item.value === this.select.value,
            );
            this.activeIndex = selectedIndex >= 0
                ? selectedIndex
                : this.filteredItems.findIndex((item) => !item.disabled);

            this.filteredItems.forEach((item, index) => {
                const option = document.createElement('button');
                option.type = 'button';
                option.id = `${this.uid}-option-${index}`;
                option.className = 'searchable-combobox__option';
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', String(item.value === this.select.value));
                option.disabled = item.disabled;
                option.tabIndex = -1;

                const label = document.createElement('span');
                label.textContent = item.label;
                option.appendChild(label);
                if (item.value === this.select.value) {
                    const check = document.createElement('i');
                    check.className = 'fas fa-check';
                    check.setAttribute('aria-hidden', 'true');
                    option.appendChild(check);
                }
                option.addEventListener('click', () => this.selectItem(item));
                option.addEventListener('pointermove', () => this.setActiveIndex(index));
                this.list.appendChild(option);
            });
            this.updateActiveOption();
        }

        loadRecentValues() {
            if (!this.options.recentStorageKey || typeof localStorage === 'undefined') return [];
            try {
                const values = JSON.parse(localStorage.getItem(this.options.recentStorageKey) || '[]');
                return Array.isArray(values) ? values : [];
            } catch (_) {
                return [];
            }
        }

        rememberRecentValue(value) {
            if (!value || !this.options.recentStorageKey || typeof localStorage === 'undefined') return;
            const values = updateRecentOptionValues(
                this.loadRecentValues(),
                value,
                this.options.maxRecentItems || 5,
            );
            try {
                localStorage.setItem(this.options.recentStorageKey, JSON.stringify(values));
            } catch (_) {
                // Storage may be unavailable in private or restricted browser contexts.
            }
        }

        updateSummary(query, visibleSet) {
            const hasQuery = Boolean(normalizeSearchText(query));
            const isBrowseMode = !hasQuery && this.options.idleMode === 'recent';
            const totalChannels = this.items.filter((item) => item.value).length;

            if (isBrowseMode && totalChannels > (this.options.maxRecentItems || 5)) {
                this.summary.hidden = false;
                this.summaryText.hidden = true;
                this.summaryText.textContent = '';
                this.summaryAction.hidden = false;
                this.summaryAction.textContent = this.showAllOptions
                    ? '收起为常用频道'
                    : `查看全部频道（${totalChannels}）`;
                return;
            }

            this.summaryAction.hidden = true;
            this.summaryAction.textContent = '';
            this.summaryText.hidden = false;
            if (visibleSet.hiddenCount <= 0) {
                this.summary.hidden = true;
                this.summaryText.textContent = '';
                return;
            }
            this.summary.hidden = false;
            if (hasQuery) {
                this.summaryText.textContent = `另有 ${visibleSet.hiddenCount} 个结果，请继续输入缩小范围`;
            } else {
                this.summaryText.textContent = this.options.idleHint
                    || `输入名称搜索其余 ${visibleSet.hiddenCount} 个选项`;
            }
        }

        handleSearchKeydown(event) {
            if (event.key === 'Tab') {
                this.close();
                return;
            }
            if (event.key === 'Escape') {
                event.preventDefault();
                this.close({restoreFocus: true});
                return;
            }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter'].includes(event.key)) return;
            event.preventDefault();

            if (event.key === 'Enter') {
                const item = this.filteredItems[this.activeIndex];
                if (item && !item.disabled) this.selectItem(item);
                return;
            }

            const enabledIndexes = this.filteredItems
                .map((item, index) => item.disabled ? -1 : index)
                .filter((index) => index >= 0);
            if (!enabledIndexes.length) return;
            if (event.key === 'Home') this.activeIndex = enabledIndexes[0];
            if (event.key === 'End') this.activeIndex = enabledIndexes[enabledIndexes.length - 1];
            if (event.key === 'ArrowDown') {
                const current = enabledIndexes.indexOf(this.activeIndex);
                this.activeIndex = enabledIndexes[(current + 1) % enabledIndexes.length];
            }
            if (event.key === 'ArrowUp') {
                const current = enabledIndexes.indexOf(this.activeIndex);
                this.activeIndex = enabledIndexes[(current - 1 + enabledIndexes.length) % enabledIndexes.length];
            }
            this.updateActiveOption({scroll: true});
        }

        setActiveIndex(index) {
            if (!this.filteredItems[index] || this.filteredItems[index].disabled) return;
            this.activeIndex = index;
            this.updateActiveOption();
        }

        updateActiveOption(options = {}) {
            const nodes = Array.from(this.list.children);
            nodes.forEach((node, index) => node.classList.toggle('is-active', index === this.activeIndex));
            const active = nodes[this.activeIndex];
            if (active) {
                this.search.setAttribute('aria-activedescendant', active.id);
                if (options.scroll) active.scrollIntoView({block: 'nearest'});
            } else {
                this.search.removeAttribute('aria-activedescendant');
            }
        }

        selectItem(item) {
            if (!item || item.disabled) return;
            this.select.value = item.value;
            this.rememberRecentValue(item.value);
            this.refresh();
            this.select.dispatchEvent(new Event('change', {bubbles: true}));
            this.close({restoreFocus: true});
        }

        toggle() {
            if (this.isOpen) this.close();
            else this.open();
        }

        open() {
            if (this.trigger.disabled || this.isOpen) return;
            document.querySelectorAll('.searchable-combobox.is-open').forEach((node) => {
                if (node !== this.wrapper) node.querySelector('.searchable-combobox__trigger')?.click();
            });
            this.isOpen = true;
            this.showAllOptions = false;
            this.wrapper.classList.add('is-open');
            this.panel.hidden = false;
            this.trigger.setAttribute('aria-expanded', 'true');
            this.search.value = '';
            this.render('');
            this.search.focus();
        }

        close(options = {}) {
            if (!this.isOpen) return;
            this.isOpen = false;
            this.showAllOptions = false;
            this.wrapper.classList.remove('is-open');
            this.panel.hidden = true;
            this.trigger.setAttribute('aria-expanded', 'false');
            this.search.removeAttribute('aria-activedescendant');
            if (options.restoreFocus) this.trigger.focus();
        }
    }

    return {
        SearchableCombobox,
        buildVisibleOptionSet,
        buildFavoritePayload,
        createLatestRequestGate,
        filterSearchableOptions,
        updateRecentOptionValues,
    };
});
