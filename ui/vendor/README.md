# Markdown parser bundle

- Source: https://github.com/markdown-it/markdown-it (npm `markdown-it@15.0.3`).
- Build: esbuild 0.28.2, browser ESM, ES2020, minified, legal comments retained.
- SHA-256 (`markdown-it.mjs`): `d302987707dc36a0c4b8ea8f89628e7b126b6e4443e498b0c019c107bad001b9`.
- Bundled dependency license notices: `LICENSES.txt`.
- No CDN or Node.js is needed to run the app.

To reproduce, install the pinned markdown-it and esbuild versions in a temporary
folder, create an entry with `export { default } from 'markdown-it';`, and run:

```sh
esbuild entry.mjs --bundle --format=esm --platform=browser --target=es2020 --minify --legal-comments=eof --outfile=markdown-it.mjs
```
