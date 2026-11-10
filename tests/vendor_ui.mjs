import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '#vendor' };
const elements = new Map();
globalThis.document = {
  querySelector: (selector) => elements.get(selector) || null,
  querySelectorAll: () => [],
};
const { S, MODES } = await import('../ui/state.js');
await import('../ui/pumui.js');
S.ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
const quotes = [
  { id: 'q1', file: 'q1', vendor: '업체 A', total: 110000, items: [], questions: [], hold: false },
  { id: 'q2', file: 'q2', vendor: '업체 B', total: 121000, items: [], questions: [], hold: false },
];
S.init = { quotes, forms: [], selection: null };
MODES.pumui.init();
S.picked = new Set(['q1', 'q2']);
const auto = '비교 견적 중 최저가 업체를 선정합니다.';
let submitted;
globalThis.fetch = async (url, options) => {
  if (url === '/api/compare') return { ok: true, json: async () => ({
    quotes, recommended: 'q1', auto_reason: auto, excluded: [], warnings: [],
  }) };
  assert.equal(url, '/api/select');
  submitted = JSON.parse(options.body);
  return { ok: true, json: async () => ({ ...submitted, result_file: submitted.pick,
    vendor: quotes.find((q) => q.id === submitted.pick).vendor,
    comparison: submitted.ids.map((id) => ({ file: id })),
  }) };
};

// The generated reason is an editable value rather than hidden helper text.
await MODES.pumui.after(2, () => {});
assert.equal(S.chosen, 'q1');
assert.equal(S.reason, auto);
const view = MODES.pumui.views[2][0]();
assert.match(view, new RegExp(`<textarea[^>]*id="reason"[^>]*>${auto}</textarea>`));
assert.doesNotMatch(view, /id="next"/);
assert(view.indexOf('class="summary"') < view.indexOf('class="vendor-workspace"'));
S.status.vendor = { text: '선정 사유가 반복 표시되면 안 됩니다.', error: false };
assert.doesNotMatch(MODES.pumui.views[2][0](), /선정 사유가 반복 표시되면 안 됩니다/);
S.status.vendor = null;
const input = { value: '   ' };
const button = { setAttribute() {} };
elements.set('#reason', input);
elements.set('#confirm', button);
MODES.pumui.views[2][1]();

// Clearing the input still sends the actual default reason in the save request.
await button.onclick({ target: button });
assert.equal(submitted.reason, auto);
assert.equal(S.init.selection.reason, auto);
assert.equal(S.vendorReasons.get('q1'), auto);
assert.equal(location.hash, S.ui.step_keys[3]);

// Read the final textarea value on confirmation, including edits without an input event.
S.reason = '이전 상태';
input.value = '  납기와 유지보수 조건을 확인했습니다.  ';
await button.onclick({ target: button });
assert.equal(submitted.reason, '납기와 유지보수 조건을 확인했습니다.');
assert.equal(S.reason, submitted.reason);

// A different vendor uses its own reason, and its saved reason is restored on init.
S.chosen = 'q2';
input.value = '기존 장비와의 호환성을 기준으로 선정했습니다.';
await button.onclick({ target: button });
assert.equal(submitted.pick, 'q2');
assert.equal(S.vendorReasons.get('q1'), '납기와 유지보수 조건을 확인했습니다.');
assert.equal(S.vendorReasons.get('q2'), input.value);
const savedReason = input.value;
MODES.pumui.init();
assert.equal(S.chosen, 'q2');
assert.equal(S.reason, savedReason);
console.log('vendor: visible default reason, blank fallback payload, final edits, independent vendor drafts and saved reason restoration passed');
