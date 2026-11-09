import { S } from './state.js';
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const won = (n) => (n == null ? '' : Number(n).toLocaleString('ko-KR'));
const fmt = (s, o) => String(s).replace(/\{(\w+)\}/g, (_, k) => (o[k] ?? ''));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ICON = {
  ok: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  warn: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5l6 11H2z M8 6.5v3.5 M8 11.5v1" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
};

const status = (key) => S.status[key] ? `<p class="help ${S.status[key].error ? 'help--error' : ''}" role="status">${esc(S.status[key].text)}</p>` : '<p class="help" role="status"></p>';
const say = (key, text, error = false) => { S.status[key] = { text, error }; };
// 단계 제목과 보조 설명을 나누어 화면과 보조 기술 모두에서 위계를 유지한다.
const head = (t) => `<div class="head"><h1>${esc(t.heading)}</h1>${t.lead ? `<p class="head__description">${esc(t.lead)}</p>` : ''}</div>`;

// ---------------------------------------------------------------- 팝업 (확인 필요 설명)
// body는 [{text, muted}] 문단 목록, actions는 [{label, primary, run}] (run이 없으면 닫기만)
function popup(title, body, actions = []) {
  const d = $('#dlg');
  d.innerHTML = `<h2 id="dlg-title">${esc(title)}</h2>${body.map((b) => b.list
    ? `<ul>${b.list.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
    : `<p class="${b.muted ? 'muted' : ''}">${esc(b.text)}</p>`).join('')}
    <div class="dlg__actions">${actions.map((x, i) => `<button type="button" class="key ${x.primary ? 'key--primary' : ''}" data-i="${i}">${esc(x.label)}</button>`).join('')}
      <button type="button" class="key" data-close>${esc(S.ui.close)}</button></div>`;
  $$('[data-i]', d).forEach((b) => (b.onclick = () => { d.close(); actions[Number(b.dataset.i)].run(); }));
  $('[data-close]', d).onclick = () => d.close();
  d.onclick = (e) => { if (e.target === d) d.close(); };  // 바깥(배경) 누르면 닫기
  if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', '');
  $('[data-close]', d).focus();
}

function setHTML(sel, html) { const el = $(sel); if (el && el.innerHTML !== html) el.innerHTML = html; }  // 같으면 그대로 둬야 도는 표시가 처음으로 돌아가지 않는다

export { $, $$, won, fmt, esc, ICON, status, say, head, popup, setHTML };
