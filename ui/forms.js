import { S } from './state.js';
import { $, $$, esc, fmt, popup, say, head, status } from './dom.js';
import { api, readB64 } from './api.js';
import { render } from './shell.js';
function formPanel(st, o) {
  const t = o.t, r = st.result;
  const groups = {}; o.forms().forEach((f) => (groups[f.group] = groups[f.group] || []).push(f));
  const list = Object.entries(groups).map(([g, fs]) => fs.map((f, i) => `<input type="radio" name="form" id="fm-${esc(g)}-${i}" value="${esc(f.path)}" ${st.form === f.path ? 'checked' : ''}>
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
    side = `${o.extra ? o.extra(r) : ''}<div class="report">${ul(t.leftover, left, true, t.leftover_help, '', true)}${ul(t.missing, rep.missing.map((m) => m.label), true, t.missing_help, `<a class="key" href="${o.missingHref}">${esc(t.missing_action)}</a>`)}${ul(t.unplaced, rep.unplaced.map((u) => (u.value ? `${u.what}: ${u.value}` : `${u.what}, ${u.why}`)), true, t.unplaced_help)}${ul(t.unknown, rep.unknown_labels, true, t.unknown_help)}${ul(t.notes, rep.notes, false)}</div>`;
  } else if (r && !r.ok) {
    doc = `<div class="doc__head"><h2>${esc(t.blocked)}</h2></div><div class="report"><ul>${r.blocked.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
  } else if (st.previewState === 'loading') {
    doc = `<div class="doc__head"><h2>${esc(t.preview_title)}</h2></div><div class="doc__empty" aria-busy="true">${esc(S.ui.loading)}</div>`;
  } else if (st.preview) {
    doc = `<div class="doc__head"><h2>${esc(t.preview_title)}</h2></div><iframe title="${esc(t.preview_title)}" src="${st.preview}"></iframe>`;
  } else {
    doc = `<div class="doc__empty">${esc(t.pick_first)}</div>`;
  }
  return `${head(t)}<div class="split"><div class="stack">
      <fieldset class="seg" style="display:block"><legend>${esc(t.legend)}</legend><div class="formlist">${list}</div></fieldset>
      <label class="key key--file">${esc(t.upload)}<input type="file" id="fupload" accept=".hwpx"></label>
      <p class="help">${esc(t.upload_hint)}</p>
      ${status(o.statusKey)}
      <button type="button" class="key key--primary" id="fill" ${st.form ? '' : 'disabled aria-describedby="fill-why"'} ${st.busy.fill ? 'aria-busy="true"' : ''}>${esc(st.busy.fill ? t.filling : t.fill)}</button>
      ${st.form ? '' : `<p class="help" id="fill-why">${esc(t.fill_disabled)}</p>`}
      ${side}
    </div><section class="cut doc" aria-live="polite">${doc}</section></div>`;
}
async function previewPanel(st, o) {
  st.previewState = 'loading'; st.result = null; render();
  try { st.preview = (await api('/api/form_preview?form=' + encodeURIComponent(st.form))).url; st.previewState = 'idle'; }
  catch (e) { st.previewState = 'idle'; st.preview = null; say(o.statusKey, fmt(S.ui.error, { msg: e.message }), true); }
  render();
}
function bindPanel(st, o) {
  $$('[data-left]').forEach((b) => (b.onclick = () => {
    const t = o.t, x = st.result.report.leftover[Number(b.dataset.left)];
    popup(fmt(t.leftover_title, { where: x.where }),
      [{ text: fmt(t.leftover_what, { kind: x.kind, values: x.values.join(', ') }) }, { text: x.action }, { text: t.leftover_note, muted: true }]);
  }));
  $$('input[name="form"]').forEach((el) => (el.onchange = () => { st.form = el.value; S.status[o.statusKey] = null; previewPanel(st, o); }));
  $('#fupload').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const r = await api('/api/upload', { name: f.name, data: await readB64(f) }); o.setForms(r); st.form = r.path; previewPanel(st, o); }
    catch (err) { say(o.statusKey, fmt(S.ui.error, { msg: err.message }), true); render(); }
  };
  $('#fill').onclick = async () => {
    st.busy.fill = true; render();
    try { st.result = await o.fill(); S.status[o.statusKey] = null; if (o.done) o.done(st.result); }
    catch (err) { say(o.statusKey, fmt(S.ui.error, { msg: err.message }), true); }
    st.busy.fill = false; render();
  };
}
const setAllForms = (r) => { S.init.forms = r.forms; if (S.init.gyeolui) S.init.gyeolui.forms = r.gyeolui_forms; };

export { formPanel, previewPanel, bindPanel, setAllForms };
