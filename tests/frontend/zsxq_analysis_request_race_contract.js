const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
  path.join(projectRoot, 'src/frontend/js/zsxq-analysis.js'),
  'utf8',
);
const instrumented = source.replace(
  /\}\)\(\);\s*$/,
  'globalThis.__zsxqTest = { state, elements, loadHistory, loadCurrentItems };\n})();',
);

const makeElement = () => ({
  addEventListener() {},
  appendChild() {},
  classList: { add() {}, remove() {}, toggle() {} },
  disabled: false,
  innerHTML: '',
  textContent: '',
  querySelector() { return makeElement(); },
  querySelectorAll() { return []; },
});
const pendingFetches = [];
const context = {
  console,
  document: {
    addEventListener() {},
    createElement: makeElement,
    querySelectorAll() { return []; },
  },
  fetch: (url) => new Promise((resolve) => pendingFetches.push({ url, resolve })),
  Intl,
  URLSearchParams,
  encodeURIComponent,
  localStorage: { getItem: () => null },
  window: { API_BASE_URL: '/api/v1' },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(instrumented, context, { filename: 'zsxq-analysis.js' });

const api = context.__zsxqTest;
for (const id of [
  'analysis-history-list',
  'history-count',
  'history-pagination-label',
  'history-prev-button',
  'history-next-button',
  'load-items-button',
  'analysis-status',
]) {
  api.elements[id] = makeElement();
}

const response = (ok, payload, status = ok ? 200 : 500) => ({
  ok,
  status,
  headers: { get: () => 'application/json' },
  json: async () => payload,
});

async function main() {
  api.state.kind = 'pdf';
  api.state.historyPage = 1;
  const olderHistory = api.loadHistory();
  api.state.kind = 'audio';
  api.state.kind = 'pdf';
  api.state.historyPage = 2;
  const newerHistory = api.loadHistory();

  pendingFetches[1].resolve(response(true, {
    success: true,
    data: { items: [], pagination: { page: 2, pages: 2, total: 22 } },
  }));
  await newerHistory;
  pendingFetches[0].resolve(response(true, {
    success: true,
    data: { items: [], pagination: { page: 1, pages: 2, total: 11 } },
  }));
  await olderHistory;
  assert.equal(
    api.state.historyPagination.page,
    2,
    'an older PDF response must not overwrite a newer PDF page after an ABA tab switch',
  );

  api.state.kind = 'pdf';
  api.state.currentRunByKind.set('pdf', { run: { id: 'pdf-old' } });
  const staleItems = api.loadCurrentItems();
  const itemRequest = pendingFetches[2];
  api.state.currentRunByKind.set('pdf', { run: { id: 'pdf-new' } });
  api.elements['analysis-status'].textContent = 'new run untouched';
  api.elements['load-items-button'].textContent = 'new run button';

  itemRequest.resolve(response(false, {
    success: false,
    error: { message: 'old request failed' },
  }));
  await staleItems;
  assert.equal(
    api.elements['analysis-status'].textContent,
    'new run untouched',
    'a stale item failure must not overwrite the active run status',
  );
  assert.equal(
    api.elements['load-items-button'].textContent,
    'new run button',
    'a stale item failure must not overwrite the active run button',
  );

  console.log('zsxq request race contract: ok');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
