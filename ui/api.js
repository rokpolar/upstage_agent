import { S } from './state.js';
async function api(path, body) {
  let r;
  try {
    r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {  // 서버가 꺼졌거나 연결이 끊김: 무엇을 하면 되는지 알려 준다
    throw new Error(S.ui ? S.ui.offline : e.message);
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const readB64 = (f) => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.onerror = no; r.readAsDataURL(f); });

export { api, readB64 };
