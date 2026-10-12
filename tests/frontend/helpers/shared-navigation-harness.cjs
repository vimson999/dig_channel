const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function navigationHarness(pathname = '/index.html', scriptSrc = 'http://127.0.0.1:8888/js/shared-navigation.js') {
    const elements = new Map(), events = {}, windowEvents = {};
    const document = {
        body: { style: { overflow: '' } }, documentElement: { clientWidth: 1280 },
        activeElement: null, currentScript: { src: scriptSrc },
        getElementById: id => elements.get(id) || null,
        querySelectorAll: () => [],
        addEventListener(type, handler) { (events[type] ||= []).push(handler); }
    };
    function element(id) {
        const attrs = {}, handlers = {};
        const el = { id, hidden: true, open: false, children: [], dataset: {}, style: {},
            getBoundingClientRect: () => ({ left: 400, width: 120 }),
            offsetParent: { getBoundingClientRect: () => ({ left: 0 }) },
            setAttribute: (key, value) => { attrs[key] = String(value); },
            getAttribute: key => attrs[key] ?? null,
            addEventListener(type, handler) { (handlers[type] ||= []).push(handler); },
            fire(type, event = {}) { for (const fn of handlers[type] || []) fn({ target: el, ...event }); },
            focus() { document.activeElement = el; },
            querySelector: () => el.children[0] || null,
            querySelectorAll: () => el.children,
            contains: target => target === el || el.children.includes(target),
            showModal() { el.open = true; }, close() { el.open = false; }
        };
        elements.set(id, el);
        return el;
    }
    const context = vm.createContext({ document, URL, console: { log() {}, warn() {}, error() {} } });
    context.window = context;
    context.location = { pathname, protocol: 'file:', href: `file:///frontend${pathname}` };
    context.addEventListener = (type, fn) => { (windowEvents[type] ||= []).push(fn); };
    context.matchMedia = () => ({ matches: false });
    const source = fs.readFileSync(path.join(__dirname, '../../../src/frontend/js/shared-navigation.js'), 'utf8');
    vm.runInContext(source, context);
    const nav = Object.create(context.SharedNavigation.prototype);
    nav.currentPage = nav.getCurrentPageName();
    return { nav, context, document, element, elements, events, windowEvents };
}
module.exports = { navigationHarness };
