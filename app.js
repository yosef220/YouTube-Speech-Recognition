/*
 * חיפוש שיר בקול — Voice Song Search
 * ------------------------------------
 * מצב "אמירת מילים": ממיר דיבור לטקסט (Web Speech API), מוצא את הסרטון המדויק
 *   ביוטיוב דרך שרתי Piped/Invidious ציבוריים (ללא מפתח) ופותח אותו ישירות.
 * מצב "זמזום": מקליט אודיו ושולח ל-AudD (דורש מפתח חינמי) לזיהוי מנגינה.
 *
 * הכל רץ בצד הלקוח בלבד. אין שרת. אין עלות.
 */

'use strict';

/* ---------- Small helpers ---------- */
const $ = (sel) => document.querySelector(sel);
const store = {
  get: (k, d = '') => localStorage.getItem(k) ?? d,
  set: (k, v) => localStorage.setItem(k, v),
};

function setStatus(msg, kind = '') {
  const el = $('#status');
  el.textContent = msg || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
}

/*
 * Public, CORS-enabled YouTube search proxies (no API key required).
 * We try them in order until one responds. Instances come and go, so the list
 * is intentionally generous. Each returns enough to build an exact watch URL.
 */
const PIPED_INSTANCES = [
  'https://pipedapi.kavin.rocks',
  'https://pipedapi.adminforge.de',
  'https://api.piped.private.coffee',
  'https://pipedapi.reallyaweso.me',
  'https://piped-api.lunar.icu',
];
const INVIDIOUS_INSTANCES = [
  'https://invidious.nerdvpn.de',
  'https://inv.nadeko.net',
  'https://invidious.jing.rocks',
  'https://yewtu.be',
];

const WATCH_URL = (id) => `https://www.youtube.com/watch?v=${id}`;

/* Race a fetch against a timeout so a dead instance doesn't hang us. */
async function fetchJSON(url, timeoutMs = 7000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

/* ---------- YouTube search ---------- */

/**
 * Resolve a text query to a list of YouTube videos: [{id, title, author, thumb}].
 * Order of preference:
 *   1. Official YouTube Data API (only if the user supplied a key) — most accurate.
 *   2. Public Piped instances (no key).
 *   3. Public Invidious instances (no key).
 * Throws if every method fails; the caller then falls back to a results page.
 */
async function searchYouTube(query) {
  const ytKey = store.get('ytKey').trim();

  if (ytKey) {
    try { return await searchViaDataApi(query, ytKey); }
    catch (e) { console.warn('YouTube Data API failed:', e); }
  }

  for (const base of PIPED_INSTANCES) {
    try {
      const data = await fetchJSON(`${base}/search?q=${encodeURIComponent(query)}&filter=videos`);
      const items = (data.items || [])
        .filter((it) => it.url && it.url.includes('watch?v='))
        .map((it) => ({
          id: new URLSearchParams(it.url.split('?')[1]).get('v'),
          title: it.title,
          author: it.uploaderName || '',
          thumb: it.thumbnail || '',
        }))
        .filter((it) => it.id);
      if (items.length) return items;
    } catch (e) { /* try next instance */ }
  }

  for (const base of INVIDIOUS_INSTANCES) {
    try {
      const data = await fetchJSON(`${base}/api/v1/search?q=${encodeURIComponent(query)}&type=video`);
      const items = (data || [])
        .filter((it) => it.type === 'video' && it.videoId)
        .map((it) => ({
          id: it.videoId,
          title: it.title,
          author: it.author || '',
          thumb: (it.videoThumbnails && it.videoThumbnails[0] && it.videoThumbnails[0].url) || '',
        }));
      if (items.length) return items;
    } catch (e) { /* try next instance */ }
  }

  throw new Error('no-search-backend');
}

async function searchViaDataApi(query, key) {
  const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=6`
    + `&q=${encodeURIComponent(query)}&key=${key}`;
  const data = await fetchJSON(url);
  return (data.items || []).map((it) => ({
    id: it.id.videoId,
    title: it.snippet.title,
    author: it.snippet.channelTitle,
    thumb: it.snippet.thumbnails?.medium?.url || '',
  }));
}

/* Open the exact video immediately, and also list alternatives to pick from. */
async function findAndOpen(query) {
  if (!query.trim()) { setStatus('לא נקלטו מילים. נסו שוב.', 'error'); return; }
  setStatus('מחפש את השיר…');
  hideResults();

  let items;
  try {
    items = await searchYouTube(query);
  } catch (e) {
    // Last resort: open the YouTube results page so the user still gets somewhere.
    setStatus('לא הצלחתי לאתר סרטון מדויק — פותח תוצאות חיפוש ביוטיוב.', 'error');
    window.open('https://www.youtube.com/results?search_query=' + encodeURIComponent(query), '_blank');
    return;
  }

  if (!items.length) { setStatus('לא נמצאו תוצאות. נסו נוסח אחר.', 'error'); return; }

  const top = items[0];
  setStatus('נמצא: ' + top.title + ' — נפתח ביוטיוב…', 'ok');
  // Open the exact video directly.
  window.open(WATCH_URL(top.id), '_blank');
  renderResults(items);
}

/* ---------- Results rendering ---------- */
function renderResults(items) {
  const list = $('#resultsList');
  list.innerHTML = '';
  items.slice(0, 6).forEach((it, i) => {
    const a = document.createElement('a');
    a.className = 'result-item' + (i === 0 ? ' top' : '');
    a.href = WATCH_URL(it.id);
    a.target = '_blank';
    a.rel = 'noopener';
    a.innerHTML = `
      ${it.thumb ? `<img src="${it.thumb}" alt="" loading="lazy" />` : '<img alt="" />'}
      <div class="meta">
        <div class="title"></div>
        <div class="sub"></div>
      </div>
      ${i === 0 ? '<span class="badge">התאמה מובילה</span>' : ''}`;
    a.querySelector('.title').textContent = it.title;
    a.querySelector('.sub').textContent = it.author;
    list.appendChild(a);
  });
  $('#results').hidden = false;
}
function hideResults() { $('#results').hidden = true; }

/* ---------- Speech recognition ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;

function startListening() {
  if (!SR) {
    setStatus('הדפדפן לא תומך בזיהוי דיבור. השתמשו ב-Chrome או Edge, או הקלידו ידנית.', 'error');
    return;
  }
  if (listening) { stopListening(); return; }

  recognition = new SR();
  recognition.lang = $('#langSelect').value;
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    listening = true;
    $('#micBtn').classList.add('recording');
    $('#micBtn').querySelector('.mic-label').textContent = 'מקשיב… דברו/שירו';
    $('#listenPulse').hidden = false;
    setStatus('מקשיב…');
  };
  recognition.onresult = (ev) => {
    let text = '';
    for (let i = 0; i < ev.results.length; i++) text += ev.results[i][0].transcript;
    $('#transcript').value = text.trim();
  };
  recognition.onerror = (ev) => {
    if (ev.error === 'no-speech') setStatus('לא שמעתי כלום. נסו שוב.', 'error');
    else if (ev.error === 'not-allowed') setStatus('נדרשת הרשאת מיקרופון.', 'error');
    else setStatus('שגיאת זיהוי: ' + ev.error, 'error');
  };
  recognition.onend = () => {
    listening = false;
    $('#micBtn').classList.remove('recording');
    $('#micBtn').querySelector('.mic-label').textContent = 'הקש כדי להקשיב';
    $('#listenPulse').hidden = true;
    const text = $('#transcript').value.trim();
    if (text) findAndOpen(text);        // auto-search when done speaking
  };

  try { recognition.start(); }
  catch (e) { setStatus('לא ניתן להפעיל מיקרופון: ' + e.message, 'error'); }
}
function stopListening() { if (recognition) recognition.stop(); }

/* ---------- Humming recognition (AudD, optional) ---------- */
let mediaRecorder = null;
let recordedChunks = [];
let humBlob = null;

async function startHumming() {
  if (mediaRecorder && mediaRecorder.state === 'recording') { mediaRecorder.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) recordedChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      humBlob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      const url = URL.createObjectURL(humBlob);
      const pb = $('#humPlayback');
      pb.src = url; pb.hidden = false;
      $('#recognizeBtn').hidden = false;
      $('#humBtn').classList.remove('recording');
      $('#humBtn').querySelector('.mic-label').textContent = 'הקש כדי לזמזם';
      $('#humPulse').hidden = true;
      setStatus('הקלטה מוכנה. הקישו "זהה את המנגינה".');
    };
    mediaRecorder.start();
    $('#humBtn').classList.add('recording');
    $('#humBtn').querySelector('.mic-label').textContent = 'מקליט… הקש לעצירה';
    $('#humPulse').hidden = false;
    setStatus('מקליט זמזום…');
  } catch (e) {
    setStatus('אין גישה למיקרופון: ' + e.message, 'error');
  }
}

async function recognizeHum() {
  if (!humBlob) { setStatus('אין הקלטה עדיין.', 'error'); return; }
  const auddKey = store.get('auddKey').trim();
  if (!auddKey) {
    setStatus('זיהוי מנגינה דורש מפתח AudD חינמי — הוסיפו אותו בהגדרות (⚙️). בינתיים נסו "אמירת מילים".', 'error');
    return;
  }
  setStatus('שולח לזיהוי מנגינה…');
  try {
    const form = new FormData();
    form.append('api_token', auddKey);
    form.append('file', humBlob, 'hum.webm');
    form.append('return', 'apple_music,spotify');
    const res = await fetch('https://api.audd.io/', { method: 'POST', body: form });
    const data = await res.json();
    if (data.status === 'success' && data.result) {
      const q = `${data.result.artist} ${data.result.title}`;
      setStatus(`זוהה: ${q}`, 'ok');
      $('#transcript').value = q;
      findAndOpen(q);
    } else {
      setStatus('לא זוהתה מנגינה. נסו לזמזם ברור יותר או לומר את המילים.', 'error');
    }
  } catch (e) {
    setStatus('שגיאה בזיהוי: ' + e.message, 'error');
  }
}

/* ---------- UI wiring ---------- */
function switchMode(mode) {
  document.querySelectorAll('.mode-btn').forEach((b) => b.classList.toggle('is-active', b.dataset.mode === mode));
  $('#panel-speech').hidden = mode !== 'speech';
  $('#panel-hum').hidden = mode !== 'hum';
  setStatus('');
  hideResults();
}

function init() {
  document.querySelectorAll('.mode-btn').forEach((b) => b.addEventListener('click', () => switchMode(b.dataset.mode)));

  $('#micBtn').addEventListener('click', startListening);
  $('#searchBtn').addEventListener('click', () => findAndOpen($('#transcript').value));
  $('#transcript').addEventListener('keydown', (e) => { if (e.key === 'Enter') findAndOpen($('#transcript').value); });

  $('#humBtn').addEventListener('click', startHumming);
  $('#recognizeBtn').addEventListener('click', recognizeHum);

  // Settings
  $('#settingsToggle').addEventListener('click', () => {
    const s = $('#settings');
    s.hidden = !s.hidden;
    $('#ytKey').value = store.get('ytKey');
    $('#auddKey').value = store.get('auddKey');
  });
  $('#saveSettings').addEventListener('click', () => {
    store.set('ytKey', $('#ytKey').value.trim());
    store.set('auddKey', $('#auddKey').value.trim());
    setStatus('ההגדרות נשמרו.', 'ok');
    $('#settings').hidden = true;
  });

  // Remember last used language.
  const savedLang = store.get('lang');
  if (savedLang) $('#langSelect').value = savedLang;
  $('#langSelect').addEventListener('change', () => store.set('lang', $('#langSelect').value));

  if (!SR) setStatus('לתשומת לב: זיהוי הדיבור עובד ב-Chrome / Edge. תמיד אפשר להקליד ידנית.', 'error');
}

document.addEventListener('DOMContentLoaded', init);
