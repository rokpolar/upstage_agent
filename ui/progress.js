import { esc, ICON } from './dom.js';
export const stageMark = (state) => state === 'done' ? ICON.ok : state === 'fail' ? ICON.warn : state === 'now' ? '<span class="spin keep-motion" aria-hidden="true"></span>' : '<span class="dot" aria-hidden="true"></span>';
export const stageList = (stages, stateOf) => stages.map(([key, label]) => {
  const state = stateOf(key);
  return `<li class="is-${state}">${stageMark(state)}<span>${esc(label)}</span></li>`;
}).join('');
