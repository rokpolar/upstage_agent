import MarkdownIt from './vendor/markdown-it.mjs';

// Keep model output as text until MarkdownIt renders it. Raw HTML stays escaped;
// the built-in URL validation rejects executable link schemes.
const markdown = new MarkdownIt({ html: false, linkify: false, typographer: false });
markdown.disable('image');
const tableOpen = markdown.renderer.rules.table_open;
const tableClose = markdown.renderer.rules.table_close;
markdown.renderer.rules.table_open = (tokens, idx, options, env, self) =>
  '<div class="markdown-table">' + (tableOpen ? tableOpen(tokens, idx, options, env, self) : self.renderToken(tokens, idx, options));
markdown.renderer.rules.table_close = (tokens, idx, options, env, self) =>
  (tableClose ? tableClose(tokens, idx, options, env, self) : self.renderToken(tokens, idx, options)) + '</div>';

export const renderMarkdown = (text) => markdown.render(String(text ?? ''));
