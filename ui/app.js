// 품의서 작업 화면. 문구는 /api/init 의 ui(config/ui.json)에서 받는다. 단계는 URL 해시에 둔다.
const S = { init: null, ui: null, error: null, picked: new Set(), set: 0, compare: null, chosen: null, reason: '',
  form: null, preview: null, previewState: 'idle', result: null, status: {}, busy: {} };
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const won = (n) => (n == null ? '' : Number(n).toLocaleString('ko-KR'));
const fmt = (s, o) => String(s).replace(/\{(\w+)\}/g, (_, k) => (o[k] ?? ''));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ICON = {
  ok: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  warn: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5l6 11H2z M8 6.5v3.5 M8 11.5v1" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
};

async function api(path, body) {
  let r;
  try {
    r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {  // 서버가 꺼졌거나 연결이 끊김: 무엇을 하면 되는지 알려 준다
    throw new Error(S.ui ? S.ui.offline : e.message);
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------------------------------------------------------------- 단계와 주소
const keys = () => S.ui.step_keys;
const quotes = () => S.init.quotes;
const byId = (id) => quotes().find((q) => q.id === id);
const picked = () => [...S.picked].map(byId).filter(Boolean);
const reviewDone = () => picked().length > 0 && picked().every((q) => !q.questions.length && !q.hold);
const chosenSaved = () => S.init.selection && S.init.selection.result_file === S.chosen;
const open = (i) => [true, S.picked.size > 0, reviewDone(), reviewDone() && chosenSaved(), reviewDone() && chosenSaved()][i];
function current() {
  const i = Math.max(0, keys().indexOf(location.hash.slice(1)));
  for (let j = i; j >= 0; j--) if (open(j)) return j;
  return 0;
}
const go = (i) => { location.hash = keys()[i]; };
window.addEventListener('hashchange', () => render(true));

function renderTabs() {
  const at = current();
  $('#tabs').innerHTML = S.ui.steps.map((name, i) => open(i)
    ? `<a class="tab" href="#${keys()[i]}" ${i === at ? 'aria-current="step"' : ''}>${esc(name)}</a>`
    : `<span class="tab" aria-disabled="true">${esc(name)}</span>`).join('');
}
const status = (key) => S.status[key] ? `<p class="help ${S.status[key].error ? 'help--error' : ''}" role="status">${esc(S.status[key].text)}</p>` : '<p class="help" role="status"></p>';
const say = (key, text, error = false) => { S.status[key] = { text, error }; };
// 제목은 두 톤: 굵은 단계 이름 + 회색 설명 (Apple 구입 페이지 방식)
const head = (t) => `<div class="head"><h1>${esc(t.heading)}${t.lead ? `${esc(S.ui.title_sep)}<span class="sub">${esc(t.lead)}</span>` : ''}</h1></div>`;

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

// ---------------------------------------------------------------- 1. 견적
function viewQuotes() {
  const t = S.ui.quotes, list = quotes().filter((q) => q.set === S.set);
  const sets = S.init.sets.length < 2 ? '<span></span>' : `<fieldset class="seg"><legend>${esc(t.set_legend)}</legend>${S.init.sets.map((name, i) =>
    `<input type="radio" name="qset" id="qset${i}" value="${i}" ${i === S.set ? 'checked' : ''}><label for="qset${i}">${esc(name)}</label>`).join('')}</fieldset>`;
  const upload = S.init.studio_ready
    ? `<label class="key key--file" ${S.busy.upload ? 'aria-busy="true"' : ''}>${esc(t.upload)}<input type="file" id="qupload" accept=".pdf,.png,.jpg,.jpeg" multiple></label>`
    : '';
  const stat = (q) => q.hold ? `<span class="status status--bad">${ICON.warn}${esc(t.hold)}</span>`
    : q.questions.length ? `<button type="button" class="status status--warn status--btn" data-issues="${esc(q.id)}">${ICON.warn}${esc(fmt(t.issues, { n: q.questions.length }))}</button>`
      : `<span class="status status--ok">${ICON.ok}${esc(t.ok)}</span>`;
  const rows = list.map((q) => `<tr data-id="${esc(q.id)}" class="${S.picked.has(q.id) ? 'is-on' : ''}">
      <td><input type="checkbox" aria-label="${esc(fmt(t.select, { vendor: q.vendor }))}" ${S.picked.has(q.id) ? 'checked' : ''}></td>
      <td class="vendor">${esc(q.vendor)}</td><td class="num">${won(q.total)}</td>
      <td class="hide-sm">${esc(q.quote_date || '')}</td><td class="num hide-sm">${esc(fmt(t.items, { n: q.items.length }))}</td><td>${stat(q)}</td></tr>`).join('');
  const table = list.length
    ? `<div class="ledger-wrap"><table class="ledger"><thead><tr><th><span class="visually-hidden">${esc(S.ui.select_col)}</span></th>${t.cols.map((c, i) => `<th class="${[1, 3].includes(i) ? 'num' : ''} ${[2, 3].includes(i) ? 'hide-sm' : ''}">${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`
    : `<div class="note"><p>${esc(t.empty)}</p><p class="muted">${esc(t.empty_action)}</p></div>`;
  return `${head(t)}<div class="row spread" style="margin-bottom:var(--space-md)">${sets}<div class="row">${upload}</div></div>
    ${S.init.studio_ready ? `<p class="help">${esc(t.upload_hint)}</p>` : `<p class="note">${esc(t.studio_off)}</p>`}
    ${status('upload')}${table}
    <div class="actions"><p class="help">${S.picked.size ? esc(fmt(t.picked, { n: S.picked.size })) : ''}</p>
      <button type="button" class="key key--primary" id="next" ${S.picked.size ? '' : 'disabled'}>${esc(t.next)}</button></div>`;
}
const readB64 = (f) => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.onerror = no; r.readAsDataURL(f); });
async function uploadQuotes(files) {
  const t = S.ui.quotes; S.busy.upload = true;
  for (const [i, f] of [...files].entries()) {
    say('upload', fmt(t.reading, { name: f.name, i: i + 1, n: files.length })); render();
    try {
      const r = await api('/api/quotes/upload', { name: f.name, data: await readB64(f) });
      S.init.quotes = S.init.quotes.filter((q) => q.id !== r.quote.id).concat(r.quote);
      S.picked.add(r.quote.id); S.set = r.quote.set; S.compare = null;
      say('upload', fmt(t.read_done, { name: f.name }));
    } catch (e) { say('upload', fmt(S.ui.error, { msg: e.message }), true); }
  }
  S.busy.upload = false; render();
}
function bindQuotes() {
  $$('input[name="qset"]').forEach((el) => (el.onchange = () => { S.set = Number(el.value); render(); }));
  const up = $('#qupload'); if (up) up.onchange = (e) => uploadQuotes(e.target.files);
  $$('[data-issues]').forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();  // 줄 선택은 바꾸지 않는다
    const t = S.ui.quotes, q = byId(b.dataset.issues);
    popup(fmt(t.issues_title, { vendor: q.vendor, n: q.questions.length }),
      [{ list: q.questions.map((x) => x.message) }, { text: t.issues_help, muted: true }],
      [{ label: t.issues_go, primary: true, run: () => { S.picked.add(q.id); S.compare = null; go(1); } }]);
  }));
  $$('.ledger tbody tr').forEach((tr) => (tr.onclick = (e) => {
    const id = tr.dataset.id; S.picked.has(id) ? S.picked.delete(id) : S.picked.add(id);
    S.compare = null; S.chosen = null; render(); if (e.target.matches('input')) $(`tr[data-id="${CSS.escape(id)}"] input`).focus();
  }));
  $('#next').onclick = () => go(1);
}

// ---------------------------------------------------------------- 2. 검토
function viewReview() {
  const t = S.ui.review;
  const groups = picked().map((q) => {
    const done = !q.questions.length && !q.hold;
    const qs = q.questions.map((x, n) => `<div class="cut question">
        <p>${esc(x.message)}</p>${x.evidence ? `<p class="evidence">${esc(x.evidence)}</p>` : ''}
        <div class="tray" role="group" aria-label="${esc(t.options_label)}">${x.options.map((o, k) => `<button type="button" class="key" data-q="${esc(q.id)}" data-key="${esc(x.key)}" data-k="${k}">${esc(o.label)}</button>`).join('')}</div>
        <div class="row" data-input="${esc(x.key)}" hidden><div class="field" style="flex:1"><label for="in-${n}-${esc(q.id)}">${esc(t.input_label)}</label><input class="input" id="in-${n}-${esc(q.id)}"></div><button type="button" class="key">${esc(t.input_submit)}</button></div>
      </div>`).join('');
    return `<section class="group"><div class="row spread"><h2>${esc(fmt(t.group, { vendor: q.vendor, total: won(q.total) }))}</h2>
        ${done ? `<span class="status status--ok">${ICON.ok}${esc(t.done)}</span>` : ''}${q.hold ? `<span class="status status--bad">${ICON.warn}${esc(S.ui.quotes.hold)}</span>` : ''}</div>
      ${q.answered.length ? `<p class="small muted">${esc(fmt(t.answered, { n: q.answered.length }))}</p>` : ''}
      ${q.studio && q.studio.decision ? `<div class="studio"><p class="small"><span class="studio__tag">${esc(fmt(t.studio_decision, { decision: q.studio.decision }))}</span> <span class="muted">${esc(t.studio_help)}</span></p>${q.studio.note ? `<details class="studio__note"><summary>${esc(q.studio.branch || '')}</summary><p>${esc(q.studio.note)}</p></details>` : ''}</div>` : ''}
      ${q.model_flags.length && t.model_flags ? `<p class="small muted">${esc(fmt(t.model_flags, { flags: q.model_flags.join(', ') }))}</p>` : ''}${qs}</section>`;
  }).join('');
  const clear = picked().every((q) => !q.questions.length);
  return `${head(t)}${clear ? `<p class="note">${esc(t.all_clear)}</p>` : ''}${groups}${status('review')}
    <div class="actions"><button type="button" class="key key--primary" id="next" ${reviewDone() ? '' : 'disabled'}>${esc(t.next)}</button></div>`;
}
async function answer(id, key, value) {
  try {
    const q = await api('/api/answer', { id, key, value });
    S.init.quotes[S.init.quotes.findIndex((x) => x.id === id)] = q; S.compare = null; S.status.review = null; render();
  } catch (e) { say('review', fmt(S.ui.error, { msg: e.message }), true); render(); }
}
function bindReview() {
  $$('.tray .key').forEach((b) => (b.onclick = () => {
    const q = byId(b.dataset.q), x = q.questions.find((y) => y.key === b.dataset.key), o = x.options[Number(b.dataset.k)];
    if (o.value === '__INPUT__') {
      const row = $(`[data-input="${CSS.escape(x.key)}"]`); row.hidden = false; row.querySelector('input').focus();
      row.querySelector('button').onclick = () => answer(q.id, x.key, row.querySelector('input').value);  // 숫자 변환은 서버(harness.coerce)
      return;
    }
    b.setAttribute('aria-busy', 'true'); answer(q.id, x.key, o.value);
  }));
  $('#next').onclick = () => go(2);
}

// ---------------------------------------------------------------- 3. 업체 선정
function viewVendor() {
  const t = S.ui.vendor, c = S.compare;
  if (!c) return `${head(t)}<table class="ledger skeleton" aria-busy="true"><tbody>${'<tr><td><span></span></td><td><span></span></td><td><span></span></td></tr>'.repeat(3)}</tbody></table>`;
  const low = Math.min(...c.quotes.map((q) => q.total || Infinity));
  // 업체마다 고르는 타일 하나: 이름과 합계, 아래에 견적일과 최저가 대비. 추천은 위에 작은 글씨.
  const tiles = c.quotes.map((q, i) => {
    const rec = q.file === c.recommended, ex = c.excluded.includes(q.file), on = q.file === S.chosen;
    const tag = rec ? esc(t.recommended) : ex ? esc(fmt(t.excluded, { why: q.issue_messages.join(', ') })) : '';
    return `<input type="radio" name="vendor" id="v${i}" value="${esc(q.file)}" ${on ? 'checked' : ''}>
      <label for="v${i}" class="tile ${ex ? 'is-off' : ''}">${tag ? `<span class="tag">${tag}</span>` : ''}
        <span class="name">${esc(q.vendor)}</span><span class="price">${won(q.total)}${esc(S.ui.won)}</span>
        <span class="meta">${esc(fmt(t.date, { date: q.quote_date || '' }))}</span>
        <span class="meta meta--right">${q.total === low ? esc(t.same) : esc(fmt(t.diff, { n: won(q.total - low) }))}</span></label>`;
  }).join('');
  const pick = c.quotes.find((q) => q.file === S.chosen) || {};
  const pq = byId(S.chosen);
  const summary = `<aside class="summary" aria-live="polite"><span class="label">${esc(t.summary_title)}</span>
      <span class="vendor">${esc(pick.vendor || '')}</span>
      ${pq ? `<span class="muted small">${esc(fmt(t.summary_items, { n: pq.items.length }))}</span>` : ''}
      <span class="label" style="margin-top:var(--space-md)">${esc(t.summary_total)}</span>
      <span class="total">${won(pick.total)}${esc(S.ui.won)}</span></aside>`;
  const isRec = S.chosen === c.recommended;
  return `${head(t)}${c.quotes.length === 1 ? `<p class="note">${esc(t.single)}</p>` : ''}
    <div class="buy">${summary}<div class="stack">
      <fieldset class="tiles" style="border:0;padding:0;margin:0"><legend class="visually-hidden">${esc(t.heading)}</legend>${tiles}</fieldset>
      <div class="field" style="margin-top:var(--space-md)"><label for="reason">${esc(t.reason_label)}${isRec ? '' : ' ' + esc(S.ui.required)}</label>
        <textarea class="input" id="reason" aria-describedby="reason-help" ${isRec ? '' : 'aria-required="true"'}>${esc(S.reason)}</textarea>
        <p class="help" id="reason-help">${esc(isRec ? fmt(t.reason_auto, { auto: c.auto_reason }) : t.reason_required)}</p></div>
    </div></div>
    ${status('vendor')}
    <div class="actions"><button type="button" class="key key--primary" id="confirm">${esc(t.confirm)}</button>
      <button type="button" class="key" id="next" ${chosenSaved() ? '' : 'disabled'}>${esc(t.next)}</button></div>`;
}
async function loadCompare() {
  const ids = [...S.picked];
  S.compare = await api('/api/compare', { ids });
  if (!S.chosen || !ids.includes(S.chosen)) { S.chosen = S.compare.recommended || ids[0]; S.reason = ''; }
}
function bindVendor() {
  if (!S.compare) return;
  $$('input[name="vendor"]').forEach((el) => (el.onchange = () => {
    S.chosen = el.value; S.reason = $('#reason').value; S.status.vendor = null; render(); $(`#${el.id}`).focus();
  }));
  $('#reason').oninput = (e) => (S.reason = e.target.value);
  $('#confirm').onclick = async (e) => {
    e.target.setAttribute('aria-busy', 'true');
    try {
      S.init.selection = await api('/api/select', { ids: [...S.picked], pick: S.chosen, reason: S.reason });
      say('vendor', fmt(S.ui.vendor.chosen, { vendor: S.init.selection.vendor, reason: S.init.selection.reason }));
    } catch (err) { say('vendor', err.message, true); $('#reason').setAttribute('aria-invalid', 'true'); }
    render(); if (S.status.vendor && S.status.vendor.error) $('#reason').focus();
  };
  $('#next').onclick = () => go(3);
}

// ---------------------------------------------------------------- 4. 구매 정보
function viewCase() {
  const t = S.ui.case, c = S.init.case;
  const field = ([k, label, type]) => type === 'funding'
    ? `<fieldset class="seg"><legend>${esc(label)}</legend>${S.init.funding_sources.map((f, i) => `<input type="radio" name="${k}" id="${k}${i}" value="${esc(f)}" ${c[k] === f ? 'checked' : ''}><label for="${k}${i}">${esc(f)}</label>`).join('')}</fieldset>`
    : `<div class="field"><label for="f-${k}">${esc(label)}</label><input class="input" id="f-${k}" name="${k}" type="${type}" autocomplete="off" value="${esc(c[k] || '')}"></div>`;
  return `${head(t)}<form id="caseform" class="fields">${t.fields.map(field).join('')}</form>${status('case')}
    <div class="actions"><button type="submit" form="caseform" class="key key--primary" id="save">${esc(t.save)}</button></div>`;
}
function bindCase() {
  $('#caseform').onsubmit = async (e) => {
    e.preventDefault(); $('#save').setAttribute('aria-busy', 'true');
    const c = { ...S.init.case };
    S.ui.case.fields.forEach(([k, , type]) => {
      if (type === 'funding') { const r = $(`input[name="${k}"]:checked`); if (r) c[k] = r.value; }
      else { const v = $(`input[name="${k}"]`).value.trim(); if (v) c[k] = v; else delete c[k]; }
    });
    try {
      const r = await api('/api/case', { case: c }); S.init.case = c; S.init.quotes = r.quotes; S.compare = null;
      say('case', S.ui.case.saved); go(reviewDone() ? 4 : 1);
    } catch (err) { say('case', fmt(S.ui.error, { msg: err.message }), true); render(); }
  };
}

// ---------------------------------------------------------------- 5. 품의서
function viewForm() {
  const t = S.ui.form, r = S.result;
  const groups = {}; S.init.forms.forEach((f) => (groups[f.group] = groups[f.group] || []).push(f));
  const list = Object.entries(groups).map(([g, fs]) => fs.map((f, i) => `<input type="radio" name="form" id="fm-${esc(g)}-${i}" value="${esc(f.path)}" ${S.form === f.path ? 'checked' : ''}>
      <label for="fm-${esc(g)}-${i}"><span>${esc(f.name)}</span><span class="small muted">${esc(g)}</span></label>`).join('')).join('');
  let doc, side = '';
  if (r && r.ok) {
    const rep = r.report;
    // 접을 수 있는 묶음: 확인할 것(칸 없음, 값 없음, 처음 보는 칸)은 펼치고 작성 내역은 접어 둔다
    // 묶음마다: 제목과 개수, 무엇을 하면 되는지 한 줄(help), 항목(html이면 그대로), 바로 갈 곳(action)
    const ul = (title, arr, open = true, help = '', action = '', raw = false) => arr.length ? `<details ${open ? 'open' : ''}><summary>${esc(title)} <span class="count">${arr.length}</span></summary>${help ? `<p class="help">${esc(help)}</p>` : ''}<ul>${arr.map((x) => `<li>${raw ? x : esc(x)}</li>`).join('')}</ul>${action}</details>` : '';
    const left = (rep.leftover || []).map((x, i) => `<button type="button" class="linkish" data-left="${i}">${esc(fmt(t.leftover_item, { where: x.where, values: x.values.join(', ') }))}</button>`);
    doc = `<div class="doc__head"><h2>${esc(t.result)}</h2><a class="key key--primary" href="${rep.files.hwpx}" download>${esc(t.download_hwpx)}</a><a class="key" href="${rep.files.pdf}" target="_blank" rel="noopener">${esc(t.download_pdf)}</a></div>
      <iframe title="${esc(t.result_title)}" src="${rep.files.html}"></iframe>`;
    side = `<div class="report">${ul(t.leftover, left, true, t.leftover_help, '', true)}${ul(t.missing, rep.missing.map((m) => m.label), true, t.missing_help, `<a class="key" href="#${keys()[3]}">${esc(t.missing_action)}</a>`)}${ul(t.unplaced, rep.unplaced.map((u) => (u.value ? `${u.what}: ${u.value}` : `${u.what}, ${u.why}`)), true, t.unplaced_help)}${ul(t.unknown, rep.unknown_labels, true, t.unknown_help)}${ul(t.notes, rep.notes, false)}</div>`;
  } else if (r && !r.ok) {
    doc = `<div class="doc__head"><h2>${esc(t.blocked)}</h2></div><div class="report"><ul>${r.blocked.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
  } else if (S.previewState === 'loading') {
    doc = `<div class="doc__head"><h2>${esc(t.preview_title)}</h2></div><div class="doc__empty" aria-busy="true">${esc(S.ui.loading)}</div>`;
  } else if (S.preview) {
    doc = `<div class="doc__head"><h2>${esc(t.preview_title)}</h2></div><iframe title="${esc(t.preview_title)}" src="${S.preview}"></iframe>`;
  } else {
    doc = `<div class="doc__empty">${esc(t.pick_first)}</div>`;
  }
  return `${head(t)}<div class="split"><div class="stack">
      <fieldset class="seg" style="display:block"><legend>${esc(t.legend)}</legend><div class="formlist">${list}</div></fieldset>
      <label class="key key--file">${esc(t.upload)}<input type="file" id="fupload" accept=".hwpx"></label>
      <p class="help">${esc(t.upload_hint)}</p>
      ${status('form')}
      <button type="button" class="key key--primary" id="fill" ${S.form ? '' : 'disabled aria-describedby="fill-why"'} ${S.busy.fill ? 'aria-busy="true"' : ''}>${esc(S.busy.fill ? t.filling : t.fill)}</button>
      ${S.form ? '' : `<p class="help" id="fill-why">${esc(t.fill_disabled)}</p>`}
      ${side}
    </div><section class="cut doc" aria-live="polite">${doc}</section></div>`;
}
async function previewForm() {
  S.previewState = 'loading'; S.result = null; render();
  try { S.preview = (await api('/api/form_preview?form=' + encodeURIComponent(S.form))).url; S.previewState = 'idle'; }
  catch (e) { S.previewState = 'idle'; S.preview = null; say('form', fmt(S.ui.error, { msg: e.message }), true); }
  render();
}
function bindForm() {
  $$('[data-left]').forEach((b) => (b.onclick = () => {
    const t = S.ui.form, x = S.result.report.leftover[Number(b.dataset.left)];
    popup(fmt(t.leftover_title, { where: x.where }),
      [{ text: fmt(t.leftover_what, { kind: x.kind, values: x.values.join(', ') }) }, { text: x.action }, { text: t.leftover_note, muted: true }]);
  }));
  $$('input[name="form"]').forEach((el) => (el.onchange = () => { S.form = el.value; S.status.form = null; previewForm(); }));
  $('#fupload').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const r = await api('/api/upload', { name: f.name, data: await readB64(f) }); S.init.forms = r.forms; S.form = r.path; previewForm(); }
    catch (err) { say('form', fmt(S.ui.error, { msg: err.message }), true); render(); }
  };
  $('#fill').onclick = async () => {
    S.busy.fill = true; render();
    try { S.result = await api('/api/fill', { form: S.form, id: S.init.selection.result_file }); S.status.form = null; }
    catch (err) { say('form', fmt(S.ui.error, { msg: err.message }), true); }
    S.busy.fill = false; render();
  };
}

// ---------------------------------------------------------------- 그리기
// 양식 미리보기(A4 폭)를 미리보기 칸 폭에 맞춰 줄인다. 화면이 좁아도 가로로 잘리지 않게.
function fitFrame(f) {
  try {
    const d = f.contentDocument; if (!d || !d.body) return;
    d.documentElement.style.zoom = '';
    const w = d.documentElement.scrollWidth;
    d.documentElement.style.zoom = String(Math.min(1, (f.clientWidth - 2) / w));
  } catch (e) { /* 다른 출처면 그대로 둔다 */ }
}
const fitFrames = () => $$('.doc iframe').forEach((f) => { f.onload = () => fitFrame(f); fitFrame(f); });
// 상단 바 실제 높이(글꼴, 줄바꿈에 따라 다름)를 CSS 변수로 넘긴다: 견적 목록 칸 높이 계산용
const barH = () => document.documentElement.style.setProperty('--bar-h', `${$('.bar').offsetHeight}px`);
if (window.ResizeObserver) new ResizeObserver(barH).observe(document.querySelector('.bar'));
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { barH(); $$('.doc iframe').forEach(fitFrame); }, 100); });

const VIEWS = [[viewQuotes, bindQuotes], [viewReview, bindReview], [viewVendor, bindVendor], [viewCase, bindCase], [viewForm, bindForm]];
async function render(fromNav = false) {
  if (S.error) {
    $('#main').innerHTML = `<div class="note note--error"><p>${esc(S.error)}</p></div><div class="actions"><button type="button" class="key key--primary" id="retry">${esc(S.ui ? S.ui.retry : '다시 불러오기')}</button></div>`;
    $('#retry').onclick = start; return;
  }
  const at = current();
  if (keys()[at] !== location.hash.slice(1)) { history.replaceState(null, '', '#' + keys()[at]); }
  renderTabs();
  const [view, bind] = VIEWS[at];
  $('#main').classList.toggle('fit', at === 0);
  $('#main').innerHTML = view(); bind(); fitFrames();
  if (at === 2 && !S.compare) {
    try { await loadCompare(); } catch (e) { say('vendor', fmt(S.ui.error, { msg: e.message }), true); S.compare = { quotes: [], excluded: [], recommended: null, auto_reason: '' }; }
    $('#main').innerHTML = view(); bind(); fitFrames();
  }
  if (fromNav) $('#main').focus({ preventScroll: true });
}

async function start() {
  S.error = null; $('#main').innerHTML = '<p class="muted" aria-busy="true">불러오는 중</p>';
  try {
    S.init = await api('/api/init'); S.ui = S.init.ui; document.title = S.ui.title;
    $('#wordmark').textContent = S.ui.wordmark;
    S.set = Math.max(0, S.init.sets.findIndex((_, i) => S.init.quotes.some((q) => q.set === i)));
    if (S.init.selection) { S.chosen = S.init.selection.result_file; }
  } catch (e) { S.error = fmt(S.ui ? S.ui.load_error : '작업 화면을 불러오지 못했습니다: {msg}', { msg: e.message }); }
  render();
}
start();
