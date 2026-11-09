// Business modules register explicitly before the application starts.
import './pumui.js';
import './gyeolui.js';
import { start } from './shell.js';
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
else start();
