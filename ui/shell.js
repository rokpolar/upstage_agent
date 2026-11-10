import { S, MODES } from './state.js';
import { $, $$, esc, fmt } from './dom.js';
import { api } from './api.js';
import { modeOf, M, keys, open, current, go } from './navigation.js';
import { stepFrame } from './layout.js';
function renderTabs() {
  const at = current(), mode = modeOf();
  $('#modes').innerHTML = Object.entries(MODES).map(([k, m]) =>
    `<a class="mode" href="#${m.keys()[0]}" ${k === mode ? 'aria-current="page"' : ''}>${esc(S.ui.modes[k])}</a>`).join('');
  $('#modes').setAttribute('aria-label', S.ui.modes.label);
  $('#wordmark').textContent = M().wordmark(); $('#wordmark').href = `#${keys()[0]}`; document.title = M().title();
  $('#tabs').innerHTML = M().steps().map((name, i) => open(i)
    ? `<a class="tab" href="#${keys()[i]}" ${i === at ? 'aria-current="step"' : ''}>${esc(name)}</a>`
    : `<span class="tab" aria-disabled="true">${esc(name)}</span>`).join('');
}
function stepNavigation(at) {
  const t = S.ui.navigation;
  if (!at && modeOf() === 'pumui') return '';
  return `<nav class="stepnav" aria-label="${esc(t.label)}">${at ? `<a class="stepnav__back" href="#${keys()[at - 1]}"><span aria-hidden="true">←</span> ${esc(t.back)}</a>` : ''}<a class="stepnav__home" href="#${S.ui.step_keys[0]}">${esc(t.home)}</a></nav>`;
}
function bindFileDrop() {
  const main = $('#main');
  if (main.dataset.drop) return;
  main.dataset.drop = '1';
  const accepts = (e) => current() === 0 && [...(e.dataTransfer?.types || [])].includes('Files')
    && (modeOf() === 'pumui' ? S.init.studio_ready : S.init.gyeolui.ready);
  main.addEventListener('dragover', (e) => {
    if (!accepts(e)) return;
    e.preventDefault(); main.classList.add('is-drop');
  });
  main.addEventListener('dragleave', (e) => {
    if (!main.contains(e.relatedTarget)) main.classList.remove('is-drop');
  });
  main.addEventListener('drop', (e) => {
    main.classList.remove('is-drop');
    if (!accepts(e)) return;
    e.preventDefault(); M().addFiles(e.dataTransfer.files);
  });
}
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

async function render(fromNav = false) {
  if (S.error) {
    $('#main').innerHTML = `<div class="note note--error"><p>${esc(S.error)}</p></div><div class="actions"><button type="button" class="key key--primary" id="retry">${esc(S.ui ? S.ui.retry : '다시 불러오기')}</button></div>`;
    $('#retry').onclick = start; return;
  }
  const at = current();
  if (keys()[at] !== location.hash.slice(1)) { history.replaceState(null, '', '#' + keys()[at]); }
  renderTabs();
  const [view, bind] = M().views[at];
  const isCase = modeOf() === 'pumui' && at === 3;
  const draw = () => {
    const html = view(), navigation = stepNavigation(at);
    const scrolls = fromNav ? [] : ['.stage-body', '.ledger-wrap', '.tiles', '.split > .stack'].map((selector) =>
      [selector, $$(selector).map((el) => [el.scrollTop, el.scrollLeft])]);
    $('#main').innerHTML = isCase ? navigation + html : stepFrame(html, navigation, M().steps()[at]);
    bind(); bindFileDrop(); fitFrames();
    scrolls.forEach(([selector, positions]) => $$(selector).forEach((el, i) => {
      if (positions[i]) [el.scrollTop, el.scrollLeft] = positions[i];
    }));
  };
  $('#main').classList.toggle('work--fixed', !isCase);
  $('#main').classList.toggle('work--case', isCase);
  $('#main').dataset.step = keys()[at];
  $('#main').classList.toggle('fit', at === 0);
  draw();
  if (M().after) await M().after(at, draw);
  if (fromNav) { window.scrollTo?.(0, 0); $('#main').focus({ preventScroll: true }); }
}

async function start() {
  S.error = null; $('#main').innerHTML = '<p class="muted" aria-busy="true">불러오는 중</p>';
  try {
    S.init = await api('/api/init'); S.ui = S.init.ui;
    for (const mode of Object.values(MODES)) if (mode.init) mode.init();
    S.set = Math.max(0, S.init.sets.findIndex((_, i) => S.init.quotes.some((q) => q.set === i)));

  } catch (e) { S.error = fmt(S.ui ? S.ui.load_error : '작업 화면을 불러오지 못했습니다: {msg}', { msg: e.message }); }
  render();
}

window.addEventListener('hashchange', () => { if (S.init) render(true); });

export { render, start, fitFrames };
