const S = { init: null, ui: null, error: null, picked: new Set(), set: 0, compare: null, chosen: null, reason: '',
  form: null, preview: null, previewState: 'idle', result: null, status: {}, busy: {} };

const MODES = {};

function invalidatePumuiOutput() {
  S.result = null;
  if (S.init?.gyeolui) S.init.gyeolui.handoff = null;
  for (const quote of S.init?.quotes || []) quote.fill = null;
}

export { S, MODES, invalidatePumuiOutput };
