import { esc } from './dom.js';

// Keep stage navigation and actions outside the scrollable work area.
function stepFrame(html, navigation, label) {
  const heading = html.match(/^<header class="head">[\s\S]*?<\/header>/)?.[0] || '';
  let content = html.slice(heading.length);
  const actionAt = content.lastIndexOf('<div class="actions');
  const actions = actionAt < 0 ? '' : content.slice(actionAt);
  if (actionAt >= 0) content = content.slice(0, actionAt);
  return `<div class="stage-heading">${navigation}${heading}</div><div class="stage-body" role="region" aria-label="${esc(label)}" tabindex="0">${content}</div>${actions}`;
}

export { stepFrame };
