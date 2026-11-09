// 결의서 작업 화면: 증빙 선택 → 검증 → 결의 정보 → 결의서 작성. app.js의 도우미(S, $, api, head, popup, formPanel…)를 그대로 쓴다.
// 문구는 /api/init 의 ui.gyeolui(config/ui.json). 단계 주소는 #g-docs, #g-review, #g-info, #g-form.
S.g = { set: 0, sel: null, files: [], withPumui: true, link: true, form: null, preview: null, previewState: 'idle',
  result: null, busy: {}, saved: new Set(), timer: null, watch: [], view: null };

const GT = () => S.ui.gyeolui;
const gItems = () => S.init.gyeolui.items;
const gById = (id) => gItems().find((x) => x.id === id);
const gSel = () => gById(S.g.sel);
const gDone = (it) => !!it && it.status === 'done';
const gClear = (it) => gDone(it) && !it.questions.length && !it.hold;
const gPut = (it) => { const i = gItems().findIndex((x) => x.id === it.id); if (i < 0) gItems().unshift(it); else gItems()[i] = it; };
const gDecisions = (it) => Object.values((it.studio && it.studio.decisions) || {});

// ---------------------------------------------------------------- 진행 확인 (Studio 결의 워크플로는 몇 분 걸린다)
function gPoll() {
  if (S.g.timer || !gItems().some((x) => x.status === 'running')) return;
  S.g.timer = setInterval(async () => {
    const ids = gItems().filter((x) => x.status === 'running').map((x) => x.id);
    if (!ids.length) { clearInterval(S.g.timer); S.g.timer = null; return; }
    try {
      const r = await api('/api/gyeolui/status?' + ids.map((id) => 'id=' + encodeURIComponent(id)).join('&'));
      const changed = r.items.some((it) => it.status !== 'running');
      r.items.forEach(gPut);
      if (changed) gHoldThenClose();
      // 증빙 선택 화면은 진행 패널과 표만 바꾼다(미리보기를 다시 읽지 않게). 다른 화면은 끝났을 때만 다시 그린다
      if (modeOf() === 'gyeolui' && current() === 0) { if (changed) render(); else gUpdate(); }
      else if (modeOf() === 'gyeolui' && changed) render();
    } catch (e) { /* 다음 차례에 다시 확인 */ }
  }, 1500);
}

// ---------------------------------------------------------------- 진행 패널 (품의 견적서 업로드 패널과 같은 모양)
// 왼쪽 증빙 묶음 미리보기, 오른쪽 Studio 단계 진행(Jobs API)과 읽어 낸 값. 이번 화면에서 요청한 건과 실행 중인 건을 보여 준다.
const GI = () => GT().intake;
const gWatched = () => S.g.watch.map(gById).filter(Boolean);
const gViewed = () => gById(S.g.view) || gWatched().find((x) => x.status === 'running') || gWatched()[gWatched().length - 1];
const gRunState = (it) => (it.status === 'error' ? 'fail' : it.status === 'done' ? 'done' : 'now');
function gNowStage(it) {
  const done = new Set((it.progress || {}).done || []);
  return GI().stages.find(([k]) => !done.has(k)) || GI().stages[GI().stages.length - 1];
}
const gMark = (st) => (st === 'done' ? ICON.ok : st === 'fail' ? ICON.warn : st === 'now' ? '<span class="spin keep-motion" aria-hidden="true"></span>' : '<span class="dot" aria-hidden="true"></span>');
function gSteps(it) {
  const done = new Set(it.status === 'done' ? GI().stages.map(([k]) => k) : (it.progress || {}).done || []), now = gNowStage(it)[0];
  return GI().stages.map(([key, label]) => {
    const st = done.has(key) ? 'done' : key === now ? (it.status === 'error' ? 'fail' : it.status === 'running' ? 'now' : 'wait') : 'wait';
    return `<li class="is-${st}">${gMark(st)}<span>${esc(label)}</span></li>`;
  }).join('');
}
function gFound(it) {
  const t = GI(), f = (it.progress || {}).found || {};
  const val = {
    docs: f.docs ? fmt(t.docs, { n: f.docs.length, list: f.docs.join(', ') }) : null,
    request: f.request, request_amount: f.request_amount != null ? `${won(f.request_amount)}원` : null,
    receipts: f.receipts ? f.receipts.map(([label, amount]) => fmt(t.receipt, { label, amount: won(amount) })).join(', ') : null,
    comparison: f.comparison, policy: f.policy,
  };
  return Object.entries(t.found).filter(([k]) => val[k]).map(([k, label]) => `<div><dt>${esc(label)}</dt><dd>${esc(val[k])}</dd></div>`).join('');
}
function gIntakeTitle(it) {
  const t = GI(), w = gWatched();
  if (w.length > 1) return fmt(t.count, { done: w.filter((x) => x.status !== 'running').length, n: w.length });
  return it.status === 'error' ? t.failed : fmt(it.status === 'done' ? t.done : t.reading, { sec: it.elapsed || 0 });
}
function gIntakeList() {
  const t = GI(), w = gWatched(), v = gViewed();
  if (w.length < 2) return '';
  return w.map((it) => {
    const st = gRunState(it), text = st === 'now' ? fmt(t.row.now, { stage: gNowStage(it)[1] }) : t.row[st];
    return `<li><button type="button" class="intake__row is-${st}" data-gview="${esc(it.id)}" ${it === v ? 'aria-current="true"' : ''}>${gMark(st)}<span class="intake__name">${esc(it.name)}</span><span class="intake__state">${esc(text)}</span></button></li>`;
  }).join('');
}
function gIntakeDoc(it) {
  if (!it.has_source) return '';
  const url = `/api/gyeolui/source?id=${encodeURIComponent(it.id)}`, name = esc(fmt(GI().preview, { name: it.name }));
  return it.source_kind === 'pdf' ? `<iframe src="${url}#toolbar=0&navpanes=0&view=FitH" title="${name}"></iframe>` : `<img src="${url}" alt="${name}">`;
}
const gFiles = (it) => (it.parts && it.parts.length ? it.parts.join(', ') : it.name);
function gIntake() {
  const it = gViewed();
  if (!it) return '';
  const busy = gWatched().some((x) => x.status === 'running');
  return `<section class="intake" aria-live="polite">
    <div class="intake__doc keep-motion ${it.status === 'running' ? 'is-reading' : ''}" id="gintake-doc" data-for="${esc(it.id)}">${gIntakeDoc(it)}</div>
    <div class="intake__info">
      <div class="intake__head"><div><p class="intake__title" id="gintake-title">${esc(gIntakeTitle(it))}</p>
        <p class="intake__file" id="gintake-file">${esc(gFiles(it))}</p></div>
        ${busy ? '' : `<button type="button" class="key" id="gintake-close">${esc(GI().close)}</button>`}</div>
      <ol class="intake__list" id="gintake-list">${gIntakeList()}</ol>
      <ol class="intake__steps" id="gintake-steps">${gSteps(it)}</ol>
      <dl class="intake__found" id="gintake-found">${gFound(it)}</dl>
    </div></section>`;
}
// 실행 중 갱신: 바뀐 부분만 바꾼다(같으면 그대로 둬야 도는 표시가 처음으로 돌아가지 않는다)
function gUpdate() {
  const it = gViewed();
  if (it && $('#gintake-steps')) {
    const doc = $('#gintake-doc');
    if (doc.dataset.for !== it.id) { doc.dataset.for = it.id; doc.innerHTML = gIntakeDoc(it); }
    doc.classList.toggle('is-reading', it.status === 'running');
    setHTML('#gintake-list', gIntakeList()); setHTML('#gintake-steps', gSteps(it)); setHTML('#gintake-found', gFound(it));
    $('#gintake-title').textContent = gIntakeTitle(it); $('#gintake-file').textContent = gFiles(it);
  }
  setHTML('#gtbody', gRows(gItems().filter((x) => x.set === S.g.set)));
  bindGRows();
}
// 모두 끝났으면 잠시 결과를 보여 준 뒤 패널을 접는다(실패가 있으면 그대로 둔다)
function gHoldThenClose() {
  const batch = S.g.watch.slice(), w = gWatched();
  if (!w.length || w.some((x) => x.status !== 'done')) return;
  setTimeout(() => {
    if (S.g.watch.join() === batch.join()) { S.g.watch = []; S.g.view = null; if (modeOf() === 'gyeolui' && current() === 0) render(); }
  }, GI().hold_ms);
}

// ---------------------------------------------------------------- 1. 증빙 선택
function gStatus(it) {
  const t = GT().docs;
  if (it.status === 'running') return `<span class="status status--warn" aria-busy="true">${ICON.warn}${esc(fmt(t.running, { sec: it.elapsed || 0 }))}</span>`;
  if (it.status === 'error') return `<button type="button" class="status status--bad status--btn" data-gerr="${esc(it.id)}">${ICON.warn}${esc(t.failed)}</button>`;
  if (it.hold) return `<span class="status status--bad">${ICON.warn}${esc(t.hold)}</span>`;
  if (it.questions.length) return `<button type="button" class="status status--warn status--btn" data-gissues="${esc(it.id)}">${ICON.warn}${esc(fmt(t.issues, { n: it.questions.length }))}</button>`;
  return `<span class="status status--ok">${ICON.ok}${esc(t.ok)}</span>`;
}
function viewGDocs() {
  const t = GT().docs, gi = S.init.gyeolui, list = gItems().filter((x) => x.set === S.g.set);
  const sets = gi.sets.length < 2 ? '' : `<fieldset class="seg"><legend>${esc(t.set_legend)}</legend>${gi.sets.map((name, i) =>
    `<input type="radio" name="gset" id="gset${i}" value="${i}" ${i === S.g.set ? 'checked' : ''}><label for="gset${i}">${esc(name)}</label>`).join('')}</fieldset>`;
  const h = gi.handoff;
  const handoff = h
    ? `<div class="note gsend__handoff"><p>${esc(fmt(t.handoff, { title: h.title || h.case_id || '', vendor: h.vendor || '', total: won(h.total) }))}</p>
        ${h.has_pdf ? `<label class="check"><input type="checkbox" id="g-with" ${S.g.withPumui ? 'checked' : ''}> ${esc(t.with_pumui)}</label>` : ''}
        <label class="check"><input type="checkbox" id="g-link" ${S.g.link || S.g.withPumui ? 'checked' : ''} ${S.g.withPumui && h.has_pdf ? 'disabled' : ''}> ${esc(t.link)}</label></div>`
    : `<p class="help">${esc(t.no_handoff)}</p>`;
  const picked = S.g.files.length
    ? `<p class="small">${esc(fmt(t.files, { n: S.g.files.length, names: S.g.files.map((f) => f.name).join(', ') }))}</p>` : '';
  const send = gi.ready
    ? `<div class="gsend cut"><div class="row spread"><label class="key key--file">${esc(t.upload)}<input type="file" id="gfiles" accept="${esc(gi.accept.join(','))}" multiple></label>
        <div class="row">${S.g.files.length ? `<button type="button" class="key" id="gclear">${esc(t.clear)}</button>` : ''}
        <button type="button" class="key key--primary" id="gsend" ${S.g.files.length && !S.g.busy.send ? '' : 'disabled'} ${S.g.busy.send ? 'aria-busy="true"' : ''}>${esc(S.g.busy.send ? t.sending : t.send)}</button></div></div>
        <p class="help">${esc(t.upload_hint)}</p>${picked}${handoff}</div>`
    : `<p class="note">${esc(t.studio_off)}</p>`;
  const table = list.length
    ? `<div class="ledger-wrap"><table class="ledger"><thead><tr><th><span class="visually-hidden">${esc(S.ui.select_col)}</span></th>${t.cols.map((c, i) => `<th class="${i === 2 ? 'num' : ''} ${[1, 3].includes(i) ? 'hide-sm' : ''}">${esc(c)}</th>`).join('')}</tr></thead><tbody id="gtbody">${gRows(list)}</tbody></table></div>`
    : `<div class="note"><p>${esc(t.empty)}</p><p class="muted">${esc(t.empty_action)}</p></div>`;
  const running = list.some((x) => x.status === 'running') ? `<p class="help">${esc(t.running_help)}</p>` : '';
  return `${head(t)}${sets}${send}${status('gupload')}${gIntake()}${running}${table}
    <div class="actions"><button type="button" class="key key--primary" id="next" ${gDone(gSel()) ? '' : 'disabled'}>${esc(t.next)}</button></div>`;
}
function gRows(list) {
  const t = GT().docs;
  return list.map((it) => `<tr data-gid="${esc(it.id)}" class="${it.id === S.g.sel ? 'is-on' : ''}">
      <td><input type="radio" name="gpick" aria-label="${esc(fmt(t.select, { name: it.name }))}" ${it.id === S.g.sel ? 'checked' : ''}></td>
      <td><span class="vendor">${esc(it.name)}</span>${it.linked ? ` <span class="small muted">${esc(t.linked)}</span>` : ''}${it.parts ? `<br><span class="small muted">${esc(it.parts.join(', '))}</span>` : ''}</td>
      <td class="hide-sm">${esc(it.payee || '')}</td><td class="num">${it.amount != null ? won(it.amount) : ''}</td>
      <td class="hide-sm small">${esc(gDecisions(it).join(' · '))}</td><td>${gStatus(it)}</td></tr>`).join('');
}
function bindGRows() {
  const t = GT().docs;
  $$('[data-gerr]').forEach((b) => (b.onclick = (e) => {
    e.stopPropagation(); const it = gById(b.dataset.gerr);
    popup(fmt(t.error_title, { name: it.name }), [{ text: it.error || '' }]);
  }));
  $$('[data-gissues]').forEach((b) => (b.onclick = (e) => {
    e.stopPropagation(); const it = gById(b.dataset.gissues);
    popup(fmt(S.ui.quotes.issues_title, { vendor: it.name, n: it.questions.length }), [{ list: it.questions.map((x) => x.message) }],
      [{ label: S.ui.quotes.issues_go, primary: true, run: () => { gPick(it.id); go(1); } }]);
  }));
  $$('#gtbody tr').forEach((tr) => (tr.onclick = () => { gPick(tr.dataset.gid); render(); }));
  const nx = $('#next'); if (nx) nx.disabled = !gDone(gSel());
}
async function gSend() {
  const t = GT().docs, h = S.init.gyeolui.handoff;
  S.g.busy.send = true; say('gupload', t.sending); render();
  try {
    const files = await Promise.all(S.g.files.map(async (f) => ({ name: f.name, data: await readB64(f) })));
    const withPumui = !!(h && h.has_pdf && S.g.withPumui);
    const r = await api('/api/gyeolui/upload', { files, with_pumui: withPumui, link: !!(h && (S.g.link || withPumui)) });
    gPut(r.item); S.g.sel = r.item.id; S.g.set = r.item.set; S.g.files = []; S.g.result = null;
    if (!gWatched().some((x) => x.status === 'running')) S.g.watch = [];  // 새 묶음
    S.g.watch.push(r.item.id); S.g.view = r.item.id;
    say('gupload', fmt(t.sent, { name: r.item.name })); gPoll();
  } catch (e) { say('gupload', fmt(S.ui.error, { msg: e.message }), true); }
  S.g.busy.send = false; render();
}
function bindGDocs() {
  const t = GT().docs;
  $$('input[name="gset"]').forEach((el) => (el.onchange = () => { S.g.set = Number(el.value); render(); }));
  const f = $('#gfiles'); if (f) f.onchange = (e) => { S.g.files = [...e.target.files]; S.status.gupload = null; render(); };
  const c = $('#gclear'); if (c) c.onclick = () => { S.g.files = []; render(); };
  const s = $('#gsend'); if (s) s.onclick = gSend;
  const w = $('#g-with'); if (w) w.onchange = (e) => { S.g.withPumui = e.target.checked; render(); };
  const l = $('#g-link'); if (l) l.onchange = (e) => { S.g.link = e.target.checked; };
  bindGRows();
  const gc = $('#gintake-close'); if (gc) gc.onclick = () => { S.g.watch = []; S.g.view = null; render(); };
  $$('[data-gview]').forEach((b) => (b.onclick = () => { S.g.view = b.dataset.gview; gUpdate(); }));
  $('#next').onclick = () => go(1);
}
function gPick(id) {
  if (S.g.sel !== id) { S.g.sel = id; S.g.result = null; }
}

// ---------------------------------------------------------------- 2. 검증
function viewGReview() {
  const t = GT().review, it = gSel();
  const base = it.base, pay = it.amount, who = it.payee;
  const diff = (v, ref) => (ref != null && v != null && v !== ref ? ` <span class="tag-diff">${esc(t.diff)}</span>` : '');
  const ref = pay != null ? pay : base && base.amount;
  const rows = [];
  if (base) rows.push(`<tr><th scope="row">${esc(t.base_names[base.source] || '')}</th><td>${esc(base.vendor || '')}</td><td class="num">${won(base.amount)}${diff(base.amount, ref)}</td><td>${esc(base.date || '')}</td><td>${esc(base.number || '')}</td></tr>`);
  it.receipts.forEach((rc) => rows.push(`<tr><th scope="row">${esc(rc.label)}</th><td>${esc(rc.vendor)}</td><td class="num">${won(rc.total)}${diff(rc.total, ref)}</td><td>${esc(rc.date)}</td><td>${esc([rc.method, rc.number].filter(Boolean).join(' · '))}</td></tr>`));
  rows.push(`<tr class="is-sum"><th scope="row">${esc(t.decided)}</th><td>${esc(who || t.undecided)}</td><td class="num">${pay != null ? won(pay) : esc(t.undecided)}</td><td></td><td></td></tr>`);
  const cmp = `<section class="group"><h2>${esc(t.compare_title)}</h2><div class="ledger-wrap"><table class="ledger cmp"><thead><tr>${t.compare_cols.map((c, i) => `<th class="${i === 2 ? 'num' : ''}">${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div></section>`;
  const qs = it.questions.map((x, n) => `<div class="cut question">
      <p>${esc(x.message)}</p>${x.evidence ? `<p class="evidence">${esc(x.evidence)}</p>` : ''}
      <div class="tray" role="group" aria-label="${esc(t.options_label)}">${x.options.map((o, k) => `<button type="button" class="key" data-gkey="${esc(x.key)}" data-k="${k}">${esc(o.label)}</button>`).join('')}</div>
      <div class="row" data-input="${esc(x.key)}" hidden><div class="field" style="flex:1"><label for="gin-${n}">${esc(t.input_label)}</label><input class="input" id="gin-${n}"></div><button type="button" class="key">${esc(t.input_submit)}</button></div>
    </div>`).join('');
  const st = it.studio || {};
  const tags = Object.entries(st.decisions || {}).map(([k, v]) => `<span class="studio__tag">${esc((t.decision_names[k] || k) + ': ' + v)}</span>`).join(' ');
  const reports = Object.entries(t.report_names).map(([k, name]) => [name, k === 'draft' ? st.draft : (st.reports || {})[k]])
    .concat((st.others || []).map((o) => [o.name, o.text])).filter(([, v]) => v)
    .map(([name, v]) => `<details class="studio__note"><summary>${esc(name)}</summary><p>${esc(v)}</p></details>`).join('');
  const studio = `<section class="group"><h2>${esc(t.studio_title)}</h2><div class="studio"><p class="small">${tags} <span class="muted">${esc(t.studio_help)}</span></p>
      ${st.categories && st.categories.length ? `<p class="small muted">${esc(fmt(t.categories, { list: st.categories.join(', ') }))}</p>` : ''}${reports}</div></section>`;
  const status_ = gClear(it) ? `<span class="status status--ok">${ICON.ok}${esc(t.done)}</span>` : it.hold ? `<span class="status status--bad">${ICON.warn}${esc(GT().docs.hold)}</span>` : '';
  return `${head(t)}<div class="row spread"><p class="small muted">${esc(it.name)}${it.answered.length ? ' · ' + esc(fmt(t.answered, { n: it.answered.length })) : ''}</p>${status_}</div>
    ${cmp}${!it.questions.length ? `<p class="note" style="margin-top:var(--space-md)">${esc(t.all_clear)}</p>` : `<div class="group">${qs}</div>`}${studio}${status('greview')}
    <div class="actions"><button type="button" class="key key--primary" id="next" ${gClear(it) ? '' : 'disabled'}>${esc(t.next)}</button></div>`;
}
async function gAnswer(key, value) {
  try { gPut(await api('/api/gyeolui/answer', { id: S.g.sel, key, value })); S.g.result = null; S.status.greview = null; }
  catch (e) { say('greview', fmt(S.ui.error, { msg: e.message }), true); }
  render();
}
function bindGReview() {
  $$('.tray .key').forEach((b) => (b.onclick = () => {
    const x = gSel().questions.find((y) => y.key === b.dataset.gkey), o = x.options[Number(b.dataset.k)];
    if (o.value === '__INPUT__') {
      const row = $(`[data-input="${CSS.escape(x.key)}"]`); row.hidden = false; row.querySelector('input').focus();
      row.querySelector('button').onclick = () => gAnswer(x.key, row.querySelector('input').value);  // 숫자 변환은 서버(gyeolui.coerce)
      return;
    }
    b.setAttribute('aria-busy', 'true'); gAnswer(x.key, o.value);
  }));
  $('#next').onclick = () => go(2);
}

// ---------------------------------------------------------------- 3. 결의 정보
function viewGInfo() {
  const t = GT().info, it = gSel(), info = it.info || {}, hints = it.hints || {};
  const field = ([k, label, type]) => {
    const hint = hints[k] ? `<p class="help">${esc(fmt(t.hint, { note: hints[k] }))}</p>` : '';
    return type === 'funding'
      ? `<fieldset class="seg"><legend>${esc(label)}</legend>${S.init.funding_sources.map((f, i) => `<input type="radio" name="${k}" id="g${k}${i}" value="${esc(f)}" ${info[k] === f ? 'checked' : ''}><label for="g${k}${i}">${esc(f)}</label>`).join('')}</fieldset>`
      : `<div class="field"><label for="gf-${k}">${esc(label)}</label><input class="input" id="gf-${k}" name="${k}" type="${type}" autocomplete="off" value="${esc(info[k] || '')}">${hint}</div>`;
  };
  const fixed = `<div class="cut fixed"><div><span class="label">${esc(t.fixed_payee)}</span><span class="vendor">${esc(it.payee || '')}</span></div>
      <div><span class="label">${esc(t.fixed_amount)}</span><span class="total">${won(it.amount)}${esc(S.ui.won)}</span></div><p class="help">${esc(t.fixed_help)}</p></div>`;
  return `${head(t)}${fixed}<form id="ginfo" class="fields" style="margin-top:var(--space-lg)">${t.fields.map(field).join('')}</form>${status('ginfo')}
    <div class="actions"><button type="submit" form="ginfo" class="key key--primary" id="gsave">${esc(t.save)}</button></div>`;
}
function bindGInfo() {
  $('#ginfo').onsubmit = async (e) => {
    e.preventDefault(); $('#gsave').setAttribute('aria-busy', 'true');
    const info = {};
    GT().info.fields.forEach(([k, , type]) => {
      if (type === 'funding') { const r = $(`input[name="${k}"]:checked`); if (r) info[k] = r.value; }
      else { const v = $(`#ginfo input[name="${k}"]`).value.trim(); if (v) info[k] = v; }
    });
    try {
      gPut(await api('/api/gyeolui/info', { id: S.g.sel, info })); S.g.saved.add(S.g.sel); S.g.result = null;
      say('ginfo', GT().info.saved); go(3);
    } catch (err) { say('ginfo', fmt(S.ui.error, { msg: err.message }), true); render(); }
  };
}

// ---------------------------------------------------------------- 4. 결의서 작성 (양식 판은 품의서와 같은 것)
const gPanel = () => ({
  t: GT().form, forms: () => S.init.gyeolui.forms, statusKey: 'gform', missingHref: `#${GT().step_keys[2]}`, setForms: setAllForms,
  fill: () => api('/api/gyeolui/fill', { id: S.g.sel, form: S.g.form }),
});

MODES.gyeolui = {
  keys: () => GT().step_keys, steps: () => GT().steps, wordmark: () => GT().wordmark, title: () => GT().title,
  open: (i) => [true, gDone(gSel()), gClear(gSel()), gClear(gSel()) && S.g.saved.has(S.g.sel)][i],
  views: [[viewGDocs, bindGDocs], [viewGReview, bindGReview], [viewGInfo, bindGInfo],
    [() => formPanel(S.g, gPanel()), () => bindPanel(S.g, gPanel())]],
  // 결의서 단계에 처음 오면 기본 지출결의서 양식을 골라 미리보기를 띄운다
  after: async (at) => {
    const d = S.init.gyeolui.default_form;
    if (at === 3 && !S.g.form && S.init.gyeolui.forms.some((f) => f.path === d)) { S.g.form = d; await previewPanel(S.g, gPanel()); }
  },
  init: () => {
    const gi = S.init.gyeolui;
    S.g.set = Math.max(0, gi.sets.findIndex((_, i) => gi.items.some((x) => x.set === i)));
    S.g.watch = gi.items.filter((x) => x.status === 'running').map((x) => x.id);  // 새로고침해도 실행 중인 건의 진행을 보여 준다
    gPoll();
  },
};
