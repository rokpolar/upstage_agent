import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Load the real ES module graph with a minimal host, without browser globals leaking
// between business modules. Browser rendering is checked separately with Playwright.
globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '#quotes' };
globalThis.document = { querySelector() { return null; } };
const { S, MODES } = await import('../ui/state.js');
await import('../ui/pumui.js');
await import('../ui/gyeolui.js');
const { stageList } = await import('../ui/progress.js');
const ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
S.ui = ui;
S.init = {
  ui, quotes: [{ id: 'q1', questions: [], hold: false, fill: { form: '/app/forms/test.hwpx', files: { pdf: '/out/saved.pdf' } } }],
  forms: [{ path: 'forms/test.hwpx' }], selection: { result_file: 'q1', comparison: [{ file: 'q1' }] },
  gyeolui: {
    items: [{ id: 'g1', status: 'done', info_saved: true, questions: [], hold: false, fill: { pdf: '/out/expense.pdf' } }],
    handoff: { total: 100 }, default_form: 'forms/expense/default.hwpx', forms: [], sets: ['업로드']
  }
};
MODES.pumui.init();
assert.equal(S.chosen, 'q1');
assert.deepEqual([...S.picked], ['q1']);
assert.equal(S.result.report.files.pdf, '/out/saved.pdf');
assert.equal(S.form, 'forms/test.hwpx');
assert.equal(MODES.pumui.open(4), true);
MODES.gyeolui.init();
assert.equal(S.g.sel, 'g1');
assert.equal(S.g.result.report.files.pdf, '/out/expense.pdf');
assert.equal(MODES.gyeolui.open(3), true);
S.init.gyeolui.items[0].info_saved = false;
assert.equal(MODES.gyeolui.open(3), false);
const stages = [['parse', '인식'], ['classify', '분류'], ['extract', '<추출>']];
const markup = stageList(stages, (key) => key === 'parse' ? 'done' : key === 'classify' ? 'now' : 'wait');
assert.match(markup, /is-done/);
assert.match(markup, /spin keep-motion/);
assert.match(markup, /&lt;추출&gt;/);

// An unchanged progress tick must not replace the preview iframe.
const doc = { dataset: { for: 'running' }, classList: { toggle() {} }, innerHTML: 'existing iframe' };
const elements = new Map([['#gintake-doc', doc], ['#gintake-steps', { innerHTML: '' }],
  ['#gintake-title', {}], ['#gintake-file', {}]]);
document.querySelector = (selector) => elements.get(selector) || null;
document.querySelectorAll = () => [];
S.g.sel = null;
S.g.watch = ['running'];
S.g.view = 'running';
S.init.gyeolui.items = [{ id: 'running', status: 'running', set: 0, has_source: true, source_kind: 'pdf',
  name: '증빙', parts: ['증빙.pdf'], elapsed: 2, progress: { done: ['upload'], found: {} } }];
S.g.set = 0;
location.hash = '#g-docs';
let tick;
globalThis.setInterval = (callback) => { tick = callback; return 1; };
globalThis.fetch = async () => ({ ok: true, json: async () => ({ items: S.init.gyeolui.items }) });
MODES.gyeolui.init();
assert.deepEqual(S.g.watch, ['running']);
await tick();
assert.equal(doc.innerHTML, 'existing iframe');
assert.match(elements.get('#gintake-steps').innerHTML, /파일 전송/);

// Completion closes the panel after the configured delay, even on another tab.
location.hash = '#quotes';
let finish;
globalThis.setTimeout = (callback, delay) => { assert.equal(delay, ui.gyeolui.intake.hold_ms); finish = callback; return 2; };
globalThis.clearInterval = () => {};
globalThis.fetch = async () => ({ ok: true, json: async () => ({ items: [{...S.init.gyeolui.items[0], status: 'done'}] }) });
await tick();
assert.equal(typeof finish, 'function');
finish();
assert.deepEqual(S.g.watch, []);

// A failed operation stays visible and renders its failed stage.
S.init.gyeolui.items[0].status = 'error';
S.init.gyeolui.items[0].error = '모의 실패';
S.g.watch = ['running'];
S.g.view = 'running';
location.hash = '#g-docs';
const failure = MODES.gyeolui.views[0][0]();
assert.match(failure, /is-fail/);
console.log('ES modules: restore, stage gates, progress, iframe retention, completion and failure passed');
