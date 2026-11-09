import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '#case' };
const elements = new Map();
globalThis.document = {
  querySelector: (selector) => elements.get(selector) || null,
  querySelectorAll: () => [],
};
const { S, MODES } = await import('../ui/state.js');
const { head } = await import('../ui/dom.js');
await import('../ui/pumui.js');
const ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
S.ui = ui;
S.init = {
  case: { title: '저장된 제목', purpose: '저장된 목적', funding_source: '자부담' },
  funding_sources: ['보조금', '자부담'],
  quotes: [{ id: 'q1', questions: [], hold: false }],
};
S.picked = new Set(['q1']);

// All original fields remain editable exactly once in the grouped form.
const initial = MODES.pumui.views[3][0]();
const names = [...initial.matchAll(/<(?:input|textarea)[^>]* name="([^"]+)"/g)].map((m) => m[1]);
assert.deepEqual([...new Set(names)].sort(), ui.case.fields.map(([key]) => key).sort());
for (const [key, , type] of ui.case.fields) {
  assert.equal(names.filter((name) => name === key).length, type === 'funding' ? 2 : 1);
}
assert.equal((initial.match(/class="form-section"/g) || []).length, 3);
assert.match(initial, /<textarea[^>]* name="purpose"/);
assert.match(head({ heading: '<제목>', lead: '설명 & 근거' }), /<h1>&lt;제목&gt;<\/h1><p class="head__description">설명 &amp; 근거<\/p>/);

const formNode = {};
elements.set('#caseform', formNode);
elements.set('#save', { setAttribute() {} });
for (const [key, , type] of ui.case.fields) {
  elements.set(type === 'funding' ? `input[name="${key}"]:checked` : `[name="${key}"]`, {
    value: key === 'title' ? '' : key === 'purpose' ? '  여러 줄\n구매 목적 <확인>  ' : type === 'funding' ? '보조금' : '입력값',
  });
}
MODES.pumui.views[3][1]();
formNode.oninput();

// Navigating away and back uses the unsaved values, including an intentionally cleared title.
location.hash = '#vendor';
location.hash = '#case';
const restored = MODES.pumui.views[3][0]();
assert.match(restored, /name="title"[^>]* value=""/);
assert.match(restored, /여러 줄\n구매 목적 &lt;확인&gt;/);
assert.match(restored, /value="보조금" checked/);
assert.equal(S.init.case.title, '저장된 제목');

let submitted;
globalThis.fetch = async (url, options) => {
  assert.equal(url, '/api/case');
  submitted = JSON.parse(options.body);
  return { ok: true, json: async () => ({ quotes: S.init.quotes }) };
};
await formNode.onsubmit({ preventDefault() {} });
assert.equal(submitted.case.purpose, '여러 줄\n구매 목적 <확인>');
assert.equal(submitted.case.funding_source, '보조금');
assert.equal(Object.hasOwn(submitted.case, 'title'), false);
assert.equal(S.caseDraft, null);
assert.equal(S.init.case.purpose, submitted.case.purpose);
assert.equal(location.hash, ui.step_keys[4]);
console.log('pumui: all fields, grouped layout, safe headings, draft restoration, multiline submission and empty title passed');
