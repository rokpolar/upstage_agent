import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '#g-info' };
const elements = new Map();
globalThis.document = {
  querySelector: (selector) => elements.get(selector) || null,
  querySelectorAll: () => [],
};
const { S, MODES, invalidatePumuiOutput } = await import('../ui/state.js');
await import('../ui/pumui.js');
await import('../ui/gyeolui.js');
const { renderMarkdown } = await import('../ui/markdown.js');
const ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
S.ui = ui;
const sample = '# 검토 결과\n\n**일치** 및 *참고*. [근거](https://example.com)\n\n> 확인 필요\n\n1. 금액\n   - 공급가액\n   - 부가세\n\n| 항목 | 결과 |\n|---|---|\n| 합계 | 일치 |\n\n```json\n{"ok":true}\n```';
const rendered = renderMarkdown(sample);
for (const tag of ['h1', 'strong', 'em', 'a', 'blockquote', 'ol', 'ul', 'table', 'pre', 'code']) {
  assert.match(rendered, new RegExp(`<${tag}(?:>| )`));
}
assert.match(rendered, /class="markdown-table"/);
for (const payload of [
  '<script>alert(1)</script><img src=x onerror=alert(1)>',
  '[x](javascript:alert(1))', '[x](JaVaScRiPt:alert(1))',
  '[x](jav&#x61;script:alert(1))', '[x](vbscript:alert(1))',
  '[x](data:text/html;base64,PHNjcmlwdD4=)', '![image](https://example.com/image.png)',
]) {
  const html = renderMarkdown(payload);
  assert.doesNotMatch(html, /<script|<img|href="(?:javascript|vbscript|data):/i);
}
assert.match(renderMarkdown('<script>alert(1)</script>'), /&lt;script&gt;/);
assert.equal(renderMarkdown(null), '');

const entry = { id: 'g1', set: 0, name: '결의', status: 'done', info_saved: true, questions: [], hold: false,
  amount: 990000, payee: '(주)진주인쇄', base: null, receipts: [], answered: [],
  info: { account: '홍보비', description: '행사 홍보물', payment_method: '계좌이체', attachments: '영수증',
    resolution_date: '2026-11-10', drafter: '담당자', department: '숨긴 부서', bank_account: '숨긴 계좌' },
  hints: { account: '화면에 표시하면 안 되는 설명' },
  studio: { reports: Object.fromEntries(Object.keys(ui.gyeolui.review.report_names).map((key) => [key, sample])),
    draft: sample, others: [{ name: '<추가 단계>', text: sample }], decisions: { comparison_decision: '**일치**' } }
};
S.init = { quotes: [{id: 'q1', fill: {files: {pdf: '/out/old.pdf'}}}], gyeolui: { items: [entry], handoff: {total: 990000} } };
S.g.sel = entry.id;
const review = MODES.gyeolui.views[1][0]();
assert.equal((review.match(/class="markdown-body"/g) || []).length, 6);
assert.match(review, /&lt;추가 단계&gt;/);
assert.match(review, /비교 판정 사유/);
assert.match(review, /비교 보고서/);
assert.match(review, /규정 판정 사유/);
assert.match(review, /<strong>일치<\/strong>/);
assert.match(review, /비교: \*\*일치\*\*|품의서·영수증 비교: \*\*일치\*\*/);
const form = MODES.gyeolui.views[2][0]();
const ordered = ['account', 'description', 'amount', 'payee', 'payment_method', 'attachments', 'resolution_date', 'drafter'];
assert.deepEqual([...form.matchAll(/<input[^>]* name="([^"]+)"/g)].map((m) => m[1]), ordered);
for (const key of ['amount', 'payee']) {
  assert.match(form, new RegExp(`<input[^>]*name="${key}"[^>]* readonly`));
}
assert.doesNotMatch(form, /화면에 표시하면 안 되는 설명|숨긴 부서|숨긴 계좌/);
assert.doesNotMatch(form.match(/<form[\s\S]*?<\/form>/)[0], /class="help"/);
const editable = ordered.filter((key) => !['amount', 'payee'].includes(key));
for (const key of ordered) elements.set(`#gf-${key}`, {value: key === 'description' ? '   ' : entry.info[key] || '변조한 확정값'});
const formNode = {};
elements.set('#ginfo', formNode);
elements.set('#gsave', {setAttribute() {}});
let submitted;
globalThis.fetch = async (url, options) => {
  assert.equal(url, '/api/gyeolui/info');
  submitted = JSON.parse(options.body);
  return {ok: true, json: async () => entry};
};
MODES.gyeolui.views[2][1]();
await formNode.onsubmit({preventDefault() {}});
assert.deepEqual(Object.keys(submitted.info), editable);
assert.equal(submitted.info.description, '');
assert.equal(location.hash, ui.gyeolui.step_keys[3]);

S.result = {ok: true, handoff: {total: 990000}};
entry.fill = {docx: '/out/historical.docx'};
invalidatePumuiOutput();
assert.equal(S.result, null);
assert.equal(S.init.gyeolui.handoff, null);
assert.equal(S.init.quotes[0].fill, null);
assert.equal(entry.fill.docx, '/out/historical.docx');
console.log('gyeolui: safe Markdown, document order, readonly values, six-field payload, and handoff UI invalidation passed');
