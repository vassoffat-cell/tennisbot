/**
 * FootBot — Telegram-бот на Google Apps Script. Версия 5 (ТЗ-006, 14.11.2026): меню «⭐ Ставка дня» и «📊 Аналитика».
 *
 *   ⭐ Ставка дня → 🔄 Ставки на сутки (облако заново присылает все ставки дня, старые удаляет)
 *                 → 🗂 История (год → месяц → дата: ставки дня этой даты; другая дата — прежняя удаляется)
 *   📊 Аналитика  → год → раздел (📈 Отчёт · 👥 Каперы · ⭐ Ставки дня · 🧠 Самообучение · 🔎 Аудит · 🏆 Турниры)
 *                 → срез (📅 День · 🗓 Неделя · 🗓 Месяц · Σ Год) → период → готовый отчёт
 *   Меню — одно сообщение-«экран» (правится на месте). 15 минут без нажатий: экран и показанное удаляются,
 *   остаётся ОДНО сообщение — последний показанный отчёт (функция tick, триггер раз в 5 минут — setupMenu()).
 *   Скриншоты 1xBet и файлы данных → папка Google Диска «footbot_inbox» → облако.
 * Тексты отчётов готовит облако: лист archive (ключ «вид|раздел|срез|период», текст — JSON-список HTML-блоков),
 * турниры — лист reports (kind = event). Облако на нажатия меню не запускается (кроме «Ставки на сутки»).
 *
 * Свойства скрипта: BOT_TOKEN, ALLOWED_USERS, SA_EMAIL, WEBAPP_URL, (необязательно) SHEET_ID, GH_TOKEN.
 */

const PROPS = PropertiesService.getScriptProperties();
const BOT = { name: 'FootBot', folder: 'footbot_inbox', workflow: 'football.yml', repo: 'vassoffat-cell/tennisbot' };
const SPORTS = [{ id: 'fb', icon: '⚽', ru: 'Футбол' }];
const B = { stats: '📊 Аналитика', pick: '⭐ Ставка дня' };
const MENU = { keyboard: [[{ text: B.pick }, { text: B.stats }]], resize_keyboard: true, is_persistent: true };
const SEC = [['rep', '📈 Отчёт'], ['cap', '👥 Каперы'], ['dp', '⭐ Ставки дня'], ['learn', '🧠 Самообучение'], ['audit', '🔎 Аудит']];
const CUT = [['d', '📅 День'], ['w', '🗓 Неделя'], ['m', '🗓 Месяц'], ['y', 'Σ Год']];
const MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const IDLE_MS = 15 * 60 * 1000;

// ============================================================ Telegram
function tg_(method, payload) {
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + PROPS.getProperty('BOT_TOKEN') + '/' + method, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true
  });
  let res;
  try { res = JSON.parse(r.getContentText()); } catch (e) { res = { ok: false, description: r.getContentText().slice(0, 200) }; }
  if (!res.ok && method !== 'deleteMessage' && method !== 'deleteMessages' && String(res.description || '').indexOf('not modified') < 0) {
    log_('ошибка ' + method, res.description || res);
  }
  return res;
}

function log_(action, info) {
  try {
    const ss = ss_();
    const sh = ss.getSheetByName('bot_log') || ss.insertSheet('bot_log');
    if (sh.getLastRow() === 0) sh.appendRow(['время', 'действие', 'результат']);
    sh.appendRow([Utilities.formatDate(new Date(), 'Asia/Tashkent', 'dd.MM HH:mm:ss'), action,
                  String(typeof info === 'string' ? info : JSON.stringify(info)).slice(0, 500)]);
    if (sh.getLastRow() > 320) sh.deleteRows(2, sh.getLastRow() - 300);
  } catch (e) { console.error(e); }
}

function esc_(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function send_(chat, text, markup) {
  // text — строка или массив готовых блоков; блоки упаковываются в сообщения ≤ 3800 знаков, блок не режется
  const blocks = Array.isArray(text) ? text : [String(text)];
  const msgs = []; let cur = '';
  blocks.forEach(function (b) {
    if (cur && cur.length + b.length + 2 > 3800) { msgs.push(cur); cur = ''; }
    cur = cur ? cur + '\n\n' + b : b;
  });
  if (cur) msgs.push(cur);
  const ids = [];
  msgs.forEach(function (m, i) {
    const p = { chat_id: chat, text: m, parse_mode: 'HTML', disable_web_page_preview: true };
    if (i === msgs.length - 1) p.reply_markup = markup || MENU;
    const r = tg_('sendMessage', p);
    if (r && r.ok) ids.push(r.result.message_id);
  });
  return ids;
}

function del_(chat, ids) {
  ids = (ids || []).filter(function (x) { return x; });
  if (ids.length) tg_('deleteMessages', { chat_id: chat, message_ids: ids });
}

function allowed_(id) {
  return String(PROPS.getProperty('ALLOWED_USERS') || '').split(',').map(function (s) { return s.trim(); })
    .indexOf(String(id)) >= 0;
}

// ============================================================ облако (GitHub Actions)
function dispatch_(inputs) {
  const token = PROPS.getProperty('GH_TOKEN');
  if (!token) return false;
  const cache = CacheService.getScriptCache();
  const tag = 'dispatch' + (inputs ? JSON.stringify(inputs) : '');
  if (cache.get(tag)) return true;                           // не чаще раза в 40 с
  cache.put(tag, '1', 40);
  const body = { ref: 'main' };
  if (inputs) body.inputs = inputs;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + BOT.repo + '/actions/workflows/' + BOT.workflow + '/dispatches', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(body), muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' }
  });
  if (r.getResponseCode() !== 204) { log_('ошибка запуска облака', r.getResponseCode() + ' ' + r.getContentText().slice(0, 200)); return false; }
  return true;
}

// ============================================================ вход
function doPost(e) {
  try {
    const u = JSON.parse(e.postData.contents);
    const cache = CacheService.getScriptCache();
    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    const seen = cache.get('u' + u.update_id);
    if (!seen) cache.put('u' + u.update_id, '1', 21600);
    lock.releaseLock();
    if (seen) return HtmlService.createHtmlOutput('ok');
    if (u.callback_query) { log_('кнопка', u.callback_query.data); onCallback_(u.callback_query); }
    else if (u.message) {
      const m = u.message;
      log_('сообщение', m.text || (m.photo ? 'скриншот' : '') || (m.document && m.document.file_name) || '?');
      onMessage_(m);
    }
  } catch (err) {
    log_('сбой', String(err && err.stack || err));
  }
  return HtmlService.createHtmlOutput('ok');
}

function doGet() { return ContentService.createTextOutput(BOT.name + ' работает'); }

function onMessage_(m) {
  const chat = m.chat.id;
  if (!allowed_(m.from && m.from.id)) return;
  if (m.photo && m.photo.length) return onShot_(chat, m, m.photo[m.photo.length - 1].file_id);
  if (m.document && /^image\//.test(String(m.document.mime_type || ''))) return onShot_(chat, m, m.document.file_id);
  if (m.document) return onFile_(chat, m.document);
  const t = String(m.text || '').trim();
  if (t === B.pick) { tg_('deleteMessage', { chat_id: chat, message_id: m.message_id }); return screen_(chat, 'P', true); }
  if (t === B.stats) { tg_('deleteMessage', { chat_id: chat, message_id: m.message_id }); return screen_(chat, 'A', true); }
  send_(chat, '⚽ <b>' + BOT.name + '</b>\n⭐ Ставка дня — ставки на сутки и история; 📊 Аналитика — отчёты за день, неделю, месяц, год. Кнопки — внизу.');
}

// ============================================================ состояние экрана (на чат)
function st_(chat) { try { return JSON.parse(PROPS.getProperty('ui_' + chat) || '{}'); } catch (e) { return {}; } }
function save_(chat, s) { s.last = Date.now(); PROPS.setProperty('ui_' + chat, JSON.stringify(s)); }

function screen_(chat, path, fresh) {
  const v = view_(path);
  const s = st_(chat);
  if (fresh && s.scr) { del_(chat, [s.scr]); s.scr = null; }
  let ok = false;
  if (s.scr) {
    const r = tg_('editMessageText', { chat_id: chat, message_id: s.scr, text: v.text, parse_mode: 'HTML', reply_markup: { inline_keyboard: v.kb } });
    ok = r.ok || String(r.description || '').indexOf('not modified') >= 0;
  }
  if (!ok) {
    const r = tg_('sendMessage', { chat_id: chat, text: v.text, parse_mode: 'HTML', reply_markup: { inline_keyboard: v.kb } });
    if (r.ok) s.scr = r.result.message_id;
  }
  save_(chat, s);
}

function show_(chat, key, title) {
  const s = st_(chat);
  del_(chat, s.shown);                                      // другая дата / отчёт — прежний удаляется
  const blocks = text_(key);
  s.shown = blocks ? send_(chat, blocks, { inline_keyboard: [] }) : send_(chat, 'За этот период данных нет.', { inline_keyboard: [] });
  s.lastKey = blocks ? key : '';
  save_(chat, s);
}

function onCallback_(q) {
  const chat = q.message.chat.id;
  tg_('answerCallbackQuery', { callback_query_id: q.id });
  if (!allowed_(q.from.id)) return;
  const d = String(q.data || '');
  if (d === 'PN') {                                         // 🔄 ставки на сутки — облако
    const ok = dispatch_({ resend_picks: '1' });
    const s = st_(chat);
    if (s.scr) tg_('editMessageText', { chat_id: chat, message_id: s.scr, parse_mode: 'HTML',
      text: ok ? '⏳ Присылаю заново все ставки дня — 1–2 минуты. Старые сообщения удалю.' : '⚠️ Облако не запущено (нет GH_TOKEN) — ставки придут с ближайшим прогоном (до 2 ч).',
      reply_markup: { inline_keyboard: [[{ text: '◀ Назад', callback_data: 'P' }]] } });
    save_(chat, s);
    return;
  }
  if (d.indexOf('S|') === 0) return show_(chat, d.slice(2));
  if (d.indexOf('E|') === 0) {                              // 🏆 турнир (лист reports)
    const rep = table_('reports').filter(function (r) { return r.kind === 'event'; });
    const r = rep[+d.slice(2)];
    const s = st_(chat); del_(chat, s.shown);
    s.shown = r ? send_(chat, [String(r.text)], { inline_keyboard: [] }) : [];
    s.lastKey = ''; s.lastText = r ? String(r.text) : '';
    save_(chat, s);
    return;
  }
  screen_(chat, d, false);
}

// ============================================================ экраны
function keys_() {
  const sh = ss_().getSheetByName('archive');
  if (!sh || sh.getLastRow() < 2) return [];
  return sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues().map(function (r) { return String(r[0]); });
}

function text_(key) {
  const sh = ss_().getSheetByName('archive');
  if (!sh || sh.getLastRow() < 2) return null;
  const ks = sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues();
  for (let i = ks.length - 1; i >= 0; i--) {
    if (String(ks[i][0]) === key) {
      try { return JSON.parse(String(sh.getRange(i + 2, 7).getValue())); } catch (e) { return [String(sh.getRange(i + 2, 7).getValue())]; }
    }
  }
  return null;
}

function grid_(items, per) {
  const rows = [];
  for (let i = 0; i < items.length; i += per) rows.push(items.slice(i, i + per));
  return rows;
}

function sp_(id) { return SPORTS.filter(function (x) { return x.id === id; })[0] || SPORTS[0]; }
function back_(path) { return [{ text: '◀ Назад', callback_data: path }]; }

function view_(path) {
  const p = path.split('|');
  const ks = keys_();
  // ⭐ Ставка дня
  if (p[0] === 'P') return { text: '⭐ <b>Ставка дня</b>', kb: [[{ text: '🔄 Ставки на сутки', callback_data: 'PN' }, { text: '🗂 История', callback_data: 'H' }]] };
  if (p[0] === 'H' || p[0] === 'A') {
    const isH = p[0] === 'H';
    const sec = isH ? 'hist' : null;
    const root = isH ? 'P' : null;
    if (p.length === 1) {                                   // выбор вида спорта (если их несколько)
      if (SPORTS.length === 1) return view_(p[0] + '|' + SPORTS[0].id);
      return { text: (isH ? '🗂 <b>История ставок дня</b>' : '📊 <b>Аналитика</b>') + ' — вид спорта:',
               kb: [SPORTS.map(function (x) { return { text: x.icon + ' ' + x.ru, callback_data: p[0] + '|' + x.id }; })]
                 .concat(isH ? [back_('P')] : []) };
    }
    const s = sp_(p[1]);
    const mine = ks.filter(function (k) { return k.indexOf(s.id + '|') === 0; });
    const years = uniq_(mine.map(function (k) { return k.split('|')[3].slice(0, 4); }));
    const up1 = SPORTS.length > 1 ? p[0] : (isH ? 'P' : null);
    if (p.length === 2) {                                   // год
      if (!years.length) return { text: s.icon + ' Данных пока нет.', kb: up1 ? [back_(up1)] : [] };
      if (years.length === 1) return view_(path + '|' + years[0]);
      return { text: s.icon + ' ' + (isH ? '🗂 История' : '📊 Аналитика') + ' — год:',
               kb: grid_(years.map(function (y) { return { text: y, callback_data: path + '|' + y }; }), 4).concat(up1 ? [back_(up1)] : []) };
    }
    const y = p[2];
    const upY = years.length > 1 ? p.slice(0, 2).join('|') : up1;
    if (isH) return dates_(s, mine, 'hist', y, p, upY);
    if (p.length === 3) {                                   // раздел
      const secs = SEC.filter(function (x) { return mine.some(function (k) { return k.split('|')[1] === x[0] && k.split('|')[3].indexOf(y) === 0; }); });
      const kb = grid_(secs.map(function (x) { return { text: x[1], callback_data: path + '|' + x[0] }; }), 2);
      if (s.id !== 'hk') kb.push([{ text: '🏆 Турниры', callback_data: path + '|ev' }]);
      if (upY) kb.push(back_(upY));
      return { text: s.icon + ' <b>' + s.ru + ' · ' + y + '</b> — раздел:', kb: kb };
    }
    const sc = p[3];
    if (sc === 'ev') {
      const rep = table_('reports').filter(function (r) { return r.kind === 'event'; });
      const kb = rep.slice(0, 40).map(function (r, i) { return [{ text: String(r.title).slice(0, 50), callback_data: 'E|' + i }]; });
      kb.push(back_(p.slice(0, 3).join('|')));
      return { text: '🏆 <b>Турниры</b>', kb: kb };
    }
    const secName = (SEC.filter(function (x) { return x[0] === sc; })[0] || ['', sc])[1];
    if (p.length === 4) {                                   // срез
      const cuts = CUT.filter(function (c) { return mine.some(function (k) { const a = k.split('|'); return a[1] === sc && a[2] === c[0] && a[3].indexOf(y) === 0; }); });
      return { text: s.icon + ' <b>' + secName + ' · ' + y + '</b> — срез:',
               kb: [cuts.map(function (c) { return { text: c[1], callback_data: c[0] === 'y' ? 'S|' + s.id + '|' + sc + '|y|' + y : path + '|' + c[0] }; })]
                 .concat([back_(p.slice(0, 3).join('|'))]) };
    }
    const cut = p[4];
    const own = mine.filter(function (k) { const a = k.split('|'); return a[1] === sc && a[2] === cut && a[3].indexOf(y) === 0; })
                    .map(function (k) { return k.split('|')[3]; });
    const up = p.slice(0, 4).join('|');
    if (cut === 'd') return dates_(s, mine, sc, y, p.slice(0, 5).concat(p.slice(5)), up, true);
    if (cut === 'w') {
      const per = uniq_(own).reverse().slice(0, 30);
      return { text: s.icon + ' <b>' + secName + '</b> — неделя:',
               kb: grid_(per.map(function (m0) { return { text: wk_(m0), callback_data: 'S|' + s.id + '|' + sc + '|w|' + m0 }; }), 2).concat([back_(up)]) };
    }
    if (cut === 'm') {
      const per = uniq_(own).reverse();
      return { text: s.icon + ' <b>' + secName + '</b> — месяц:',
               kb: grid_(per.map(function (m) { return { text: MON[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4), callback_data: 'S|' + s.id + '|' + sc + '|m|' + m }; }), 4).concat([back_(up)]) };
    }
  }
  return view_('P');
}

function dates_(s, mine, sec, y, p, up, analytics) {
  // История (H|sp|год|месяц) и срез «День» аналитики (A|sp|год|раздел|d|месяц): месяц → даты
  const base = analytics ? p.slice(0, 5).join('|') : p.slice(0, 3).join('|');
  const mIdx = analytics ? 5 : 3;
  const ds = uniq_(mine.filter(function (k) { const a = k.split('|'); return a[1] === sec && a[2] === 'd' && a[3].indexOf(y) === 0; })
                       .map(function (k) { return k.split('|')[3]; }));
  if (!ds.length) return { text: s.icon + ' Данных пока нет.', kb: up ? [back_(up)] : [] };
  const months = uniq_(ds.map(function (d) { return d.slice(0, 7); }));
  const title = analytics ? '<b>' + (SEC.filter(function (x) { return x[0] === sec; })[0] || ['', ''])[1] + '</b> · ' : '🗂 <b>История ставок дня</b> · ';
  if (p.length <= mIdx) {
    if (months.length === 1) return dates_(s, mine, sec, y, p.slice(0, mIdx).concat([months[0]]), up, analytics);
    return { text: s.icon + ' ' + title + y + ' — месяц:',
             kb: grid_(months.reverse().map(function (m) { return { text: MON[+m.slice(5, 7) - 1], callback_data: base + '|' + m }; }), 4).concat(up ? [back_(up)] : []) };
  }
  const m = p[mIdx];
  const days = ds.filter(function (d) { return d.indexOf(m) === 0; });
  const upM = months.length > 1 ? base : up;
  return { text: s.icon + ' ' + title + MON[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4) + ' — дата:',
           kb: grid_(days.map(function (d) { return { text: d.slice(8, 10), callback_data: 'S|' + s.id + '|' + sec + '|d|' + d }; }), 7)
                 .concat(upM ? [back_(upM)] : []) };
}

function uniq_(a) { return a.filter(function (x, i) { return x && a.indexOf(x) === i; }).sort(); }
function wk_(m0) {
  const d = new Date(m0 + 'T00:00:00Z'); const e = new Date(d.getTime() + 6 * 864e5);
  const f = function (x) { return ('0' + x.getUTCDate()).slice(-2) + '.' + ('0' + (x.getUTCMonth() + 1)).slice(-2); };
  return f(d) + '–' + f(e);
}

// ============================================================ 15 минут без нажатий → одно сообщение
function tick() {
  const all = PROPS.getProperties();
  Object.keys(all).forEach(function (k) {
    if (k.indexOf('ui_') !== 0) return;
    let s; try { s = JSON.parse(all[k]); } catch (e) { return; }
    if (!s.last || Date.now() - s.last < IDLE_MS || (!s.scr && !(s.shown || []).length)) return;
    const chat = k.slice(3);
    del_(chat, [s.scr].concat(s.shown || []));
    const blocks = s.lastKey ? text_(s.lastKey) : (s.lastText ? [s.lastText] : null);
    if (blocks) send_(chat, blocks);                        // одно сообщение (длинный отчёт — по лимиту Telegram)
    PROPS.setProperty(k, JSON.stringify({}));
  });
}

// ============================================================ скриншоты и файлы → Google Диск
function folder_() {
  const it = DriveApp.getFoldersByName(BOT.folder);
  return it.hasNext() ? it.next() : DriveApp.createFolder(BOT.folder);
}

function fetchBlob_(fileId, name) {
  const f = tg_('getFile', { file_id: fileId });
  if (!f.ok) return null;
  const url = 'https://api.telegram.org/file/bot' + PROPS.getProperty('BOT_TOKEN') + '/' + f.result.file_path;
  return UrlFetchApp.fetch(url).getBlob().setName(Utilities.formatDate(new Date(), 'Asia/Tashkent', 'yyyyMMdd_HHmmss') + '_' + name);
}

function onShot_(chat, m, fileId) {
  const blob = fetchBlob_(fileId, 'shot.jpg');
  if (!blob) return send_(chat, 'Не удалось получить скриншот от Telegram — отправьте ещё раз.');
  const file = folder_().createFile(blob.setName('shot_' + blob.getName()));
  const reply = m.reply_to_message ? String(m.reply_to_message.text || m.reply_to_message.caption || '').slice(0, 1500) : '';
  file.setDescription(JSON.stringify({ reply: reply, caption: String(m.caption || '').slice(0, 300) }));
  const ok = dispatch_();
  send_(chat, '📸 Скриншот получен — сравню кф со ставкой дня (' + (ok ? '1–2 минуты' : 'до 2 часов') + ').');
}

function onFile_(chat, doc) {
  const name = String(doc.file_name || 'file');
  const blob = fetchBlob_(doc.file_id, name);
  if (!blob) return send_(chat, 'Не удалось получить файл от Telegram (больше 20 МБ?) — отправьте ещё раз.');
  folder_().createFile(blob);
  const ok = dispatch_();
  send_(chat, '📥 Файл <b>' + esc_(name) + '</b> получен — облако проверит содержимое (' + (ok ? '1–2 минуты' : 'до 2 часов') + ').');
}

// ============================================================ данные таблицы
function ss_() {
  const id = PROPS.getProperty('SHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActive();
}

function table_(name) {
  const sh = ss_().getSheetByName(name);
  if (!sh || sh.getLastRow() < 2) return [];
  const v = sh.getDataRange().getValues();
  const h = v[0];
  return v.slice(1).map(function (r) { const o = {}; h.forEach(function (k, i) { o[k] = r[i]; }); return o; });
}

// ============================================================ настройка (запускать вручную из редактора)
function setup() {
  const sa = PROPS.getProperty('SA_EMAIL');
  if (!sa) throw new Error('Добавьте свойство SA_EMAIL (client_email из service_account.json)');
  const f = folder_();
  f.addEditor(sa);
  Logger.log('Папка ' + BOT.folder + ' готова и открыта для ' + sa + ': ' + f.getUrl());
}

// ТЗ-006: один раз после вставки кода — триггер tick раз в 5 минут (схлопывание меню через 15 минут) и новая клавиатура.
function setupMenu() {
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === 'tick') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(5).create();
  if (BOT.workflow === 'football.yml') {                     // Г0: страховка каперов — только в FootBot (один бот на оба)
    ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === 'capTick') ScriptApp.deleteTrigger(t); });
    ScriptApp.newTrigger('capTick').timeBased().everyMinutes(10).create();   // 16.11 аудит: раз в час давало перерыв до ≈ 125 мин
  }
  String(PROPS.getProperty('ALLOWED_USERS') || '').split(',').forEach(function (u) {
    u = u.trim();
    if (u) tg_('sendMessage', { chat_id: u, text: '✅ Меню обновлено: ⭐ Ставка дня · 📊 Аналитика', reply_markup: MENU });
  });
  Logger.log('Триггер tick (5 мин)' + (BOT.workflow === 'football.yml' ? ' и capTick (10 мин)' : '') + ' установлен, клавиатура отправлена');
}

// ============================================================ Г0 (15.11): страховка ежечасной проверки каперов
// GitHub иногда пропускает плановый запуск каперов (15.11 ночью — перерыв 97 мин). Раз в час (триггер — setupMenu, только FootBot):
// если прогона cappers.yml не было больше 65 минут — запустить его. Минуты GitHub тратятся только на пропущенные часы.
function capTick() {
  const token = PROPS.getProperty('GH_TOKEN');
  if (!token) return;
  const h = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' };
  const api = 'https://api.github.com/repos/' + BOT.repo + '/actions/workflows/cappers.yml/';
  const r = UrlFetchApp.fetch(api + 'runs?per_page=3', { headers: h, muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) { log_('каперы: проверка запусков', r.getResponseCode()); return; }
  const runs = JSON.parse(r.getContentText()).workflow_runs || [];
  const last = runs.length ? new Date(runs[0].created_at).getTime() : 0;
  if (Date.now() - last >= 62 * 60 * 1000) {          // 16.11: проверка каждые 10 мин → перерыв ≤ ≈ 75 мин
    const d = UrlFetchApp.fetch(api + 'dispatches', { method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ ref: 'main' }), headers: h, muteHttpExceptions: true });
    log_('каперы: страховочный запуск', d.getResponseCode() === 204 ? 'запущено' : d.getResponseCode() + ' ' + d.getContentText().slice(0, 200));
  }
  keyTick_(h);
}

// 16.11 (HANDOFF з.7, A35): ключевые часы — минута в минуту. GitHub запускает плановые прогоны с опозданием (медиана 36–46 мин,
// p90 52–56 мин). Раз в 10 мин (тот же триггер capTick): если после начала ключевого часа прогона бота ещё нет — запустить сразу.
// Окно: со 2-й минуты до 3 ч после начала. Плановый прогон, пришедший позже, пропустит шлюз (полный прогон < 35 мин назад).
const KEY_SLOTS = [['daily.yml', 8, 0], ['daily.yml', 11, 0], ['daily.yml', 12, 0],   // 🎾1 · отчёт тенниса · 🏒 отчёт и 🏒1
                   ['football.yml', 8, 0], ['football.yml', 11, 30]];                 // ⚽1 · отчёт футбола
function keyTick_(h) {
  const loc = Utilities.formatDate(new Date(), 'Asia/Tashkent', 'HH:mm').split(':');
  const nowMin = Number(loc[0]) * 60 + Number(loc[1]);
  KEY_SLOTS.forEach(function (s) {
    const ago = nowMin - (s[1] * 60 + s[2]);            // минут после начала ключевого часа
    if (ago < 2 || ago > 180) return;
    const since = new Date(Date.now() - ago * 60 * 1000).toISOString().slice(0, 19) + 'Z';
    const api = 'https://api.github.com/repos/' + BOT.repo + '/actions/workflows/' + s[0] + '/';
    const r = UrlFetchApp.fetch(api + 'runs?per_page=5&created=%3E%3D' + since, { headers: h, muteHttpExceptions: true });
    if (r.getResponseCode() !== 200) { log_('ключевой час: проверка ' + s[0], r.getResponseCode()); return; }
    if ((JSON.parse(r.getContentText()).workflow_runs || []).length) return;   // прогон после начала часа уже есть / в очереди
    const d = UrlFetchApp.fetch(api + 'dispatches', { method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ ref: 'main' }), headers: h, muteHttpExceptions: true });
    log_('ключевой час ' + s[1] + ':' + ('0' + s[2]).slice(-2) + ' · ' + s[0], d.getResponseCode() === 204 ? 'запущено' : d.getResponseCode());
  });
}

function setWebhook() {
  const url = PROPS.getProperty('WEBAPP_URL');
  if (!url) throw new Error('Добавьте свойство WEBAPP_URL (адрес веб-приложения, оканчивается на /exec)');
  const r = tg_('setWebhook', { url: url, allowed_updates: ['message', 'callback_query'], drop_pending_updates: true });
  Logger.log(JSON.stringify(r));
  tg_('setMyCommands', { commands: [{ command: 'start', description: 'Меню' }] });
}

function checkWebhook() { Logger.log(JSON.stringify(tg_('getWebhookInfo', {}))); }
function testDispatch() { CacheService.getScriptCache().remove('dispatch'); Logger.log(dispatch_() ? 'облако запущено' : 'не запущено — см. лист bot_log'); }
