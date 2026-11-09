import { S, MODES } from './state.js';
const modeOf = () => (location.hash.startsWith('#g-') && MODES.gyeolui ? 'gyeolui' : 'pumui');
const M = () => MODES[modeOf()];
const keys = () => M().keys();
const open = (i) => M().open(i);
function current() {
  const i = Math.max(0, keys().indexOf(location.hash.slice(1)));
  for (let j = i; j >= 0; j--) if (open(j)) return j;
  return 0;
}
const go = (i) => { location.hash = keys()[i]; };

export { modeOf, M, keys, open, current, go };
