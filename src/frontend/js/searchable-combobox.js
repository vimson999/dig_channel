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

            panel.append(searchWrap, list, empty);
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
            document.addEventListener('pointerdown', (event) => {
                if (this.isOpen && !this.wrapper.contains(event.target)) this.close();
            });
        }

        refresh() {
            this.items = Array.from(this.select.options).map((option) => ({
                value: option.value,
                label: option.textContent.trim(),
                keywords: option.dataset.category || '',
                disabled: option.disabled,
            }));
            const selected = this.select.options[this.select.selectedIndex] || this.select.options[0];
            this.value.textContent = selected ? selected.textContent.trim() : (this.options.placeholder || '请选择');
            this.trigger.disabled = this.select.disabled;
            this.render(this.isOpen ? this.search.value : '');
        }

        render(query) {
            this.filteredItems = filterSearchableOptions(this.items, query);
            this.list.replaceChildren();
            this.empty.hidden = this.filteredItems.length > 0;
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
            this.wrapper.classList.remove('is-open');
            this.panel.hidden = true;
            this.trigger.setAttribute('aria-expanded', 'false');
            this.search.removeAttribute('aria-activedescendant');
            if (options.restoreFocus) this.trigger.focus();
        }
    }

    return {
        SearchableCombobox,
        buildFavoritePayload,
        createLatestRequestGate,
        filterSearchableOptions,
    };
});
