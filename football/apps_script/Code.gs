/**
 * FootBot — футбольный Telegram-бот с кнопками на Google Apps Script (бесплатно, круглосуточно в вашем аккаунте Google).
 *
 *   • кнопки: прогноз на 24 ч, аналитика (всё время / месяц / период / турнир), отчёт для Claude, очистка чата;
 *   • принимает файлы football-data (.xlsx) и кладёт их в папку Google Диска «footbot_inbox» —
 *     облако (GitHub Actions) забирает их при ближайшем запуске (до 2 часов).
 * Данные — листы auto_forecasts и auto_lines таблицы «Football» (их заполняет облако).
 *
 * Свойства скрипта (Настройки проекта → Свойства скрипта):
 *   BOT_TOKEN, ALLOWED_USERS, SA_EMAIL, WEBAPP_URL, (необязательно) SHEET_ID
 */

const PROPS = PropertiesService.getScriptProperties();
const FOLDER_NAME = 'footbot_inbox';
const MARKET_RU = { '1X2': 'Исход', DC: 'Двойной шанс', AH: 'Фора', TOT: 'Тотал', IT1: 'Инд. тотал 1', IT2: 'Инд. тотал 2', BTTS: 'Обе забьют' };
const SRC_RU = { market: 'рынок', model: 'модель' };
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const B = {
  list: '⚽ Прогноз 24 ч', stats: '📊 Аналитика', month: '📅 За месяц', period: '🗓 За период',
  event: '🏆 По турниру', report: '🧾 Отчёт для Claude', clear: '🧹 Очистить чат'
};
const MENU = {
  keyboard: [[{ text: B.list }, { text: B.stats }], [{ text: B.month }, { text: B.period }],
             [{ text: B.event }, { text: B.report }], [{ text: B.clear }]],
  resize_keyboard: true, is_persistent: true
};

// ============================================================ Telegram
function tg_(method, payload) {
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + PROPS.getProperty('BOT_TOKEN') + '/' + method, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true
  });
  let res;
  try { res = JSON.parse(r.getContentText()); } catch (e) { res = { ok: false, description: r.getContentText().slice(0, 200) }; }
  if (!res.ok && method !== 'deleteMessage') log_('ошибка ' + method, res.description || res);
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
  const blocks = String(text).split('\n\n');
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

function remember_(chat, ids) {
  const ss = ss_();
  const sh = ss.getSheetByName('tg_messages') || ss.insertSheet('tg_messages');
  if (sh.getLastRow() === 0) sh.appendRow(['chat_id', 'message_id']);
  ids.forEach(function (id) { sh.appendRow([chat, id]); });
}

function allowed_(id) {
  return String(PROPS.getProperty('ALLOWED_USERS') || '').split(',').map(function (s) { return s.trim(); })
    .indexOf(String(id)) >= 0;
}

// ============================================================ вход
function doPost(e) {
  // Ответ через HtmlService (код 200 сразу). ContentService отвечает перенаправлением 302 — Telegram повторяет обновление
  // и не присылает новые, кнопки «не работают».
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
    else if (u.message) { log_('сообщение', u.message.text || (u.message.document && u.message.document.file_name) || '?'); onMessage_(u.message); }
  } catch (err) {
    log_('сбой', String(err && err.stack || err));
  }
  return HtmlService.createHtmlOutput('ok');
}

function doGet() { return ContentService.createTextOutput('FootBot работает'); }

function onMessage_(m) {
  const chat = m.chat.id;
  if (!allowed_(m.from && m.from.id)) return;
  if (m.document) return onFile_(chat, m.document);
  const t = String(m.text || '').trim();
  const cache = CacheService.getScriptCache();
  if (cache.get('period' + chat) && /\d/.test(t)) {
    cache.remove('period' + chat);
    const r = parsePeriod_(t);
    if (!r) return send_(chat, 'Не понял период. Формат: <code>01.11.2026-15.11.2026</code>');
    return send_(chat, stats_({ from: r[0], to: r[1], title: 'Период ' + t }));
  }
  if (t === B.list) return remember_(chat, send_(chat, forecastList_()));
  if (t === B.stats) return send_(chat, stats_({ title: 'Всё время' }));
  if (t === B.report) return send_(chat, stats_({ title: 'Отчёт для Claude', detailed: true }));
  if (t === B.month) return askMonth_(chat);
  if (t === B.event) return askEvent_(chat);
  if (t === B.clear) return clearChat_(chat, m.message_id);
  if (t === B.period) {
    cache.put('period' + chat, '1', 900);
    return send_(chat, 'Напишите период в формате <code>01.11.2026-15.11.2026</code>');
  }
  send_(chat, '⚽ <b>FootBot</b>\nПрогноз на 24 ч приходит сам каждые 2 часа (прошлый список удаляется). ' +
    'Пометка у матча: «рынок» — линия от котировок без маржи, «модель» — котировок нет, расчёт модели с факторами. ' +
    'Итоги — в 00:00, отчёт для Claude — после каждых 10 матчей. По понедельникам бот напомнит прислать файлы football-data.\n\nКнопки — внизу.');
}

function onCallback_(q) {
  const chat = q.message.chat.id;
  tg_('answerCallbackQuery', { callback_query_id: q.id });
  if (!allowed_(q.from.id)) return;
  const d = String(q.data || '');
  if (d.indexOf('m:') === 0) {
    const ym = d.slice(2), y = +ym.slice(0, 4), mo = +ym.slice(5, 7);
    const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
    return send_(chat, stats_({ from: ym + '-01', to: ym + '-' + ('0' + last).slice(-2), title: MONTHS[mo - 1] + ' ' + y }));
  }
  if (d.indexOf('t:') === 0) {
    const list = JSON.parse(CacheService.getScriptCache().get('ev' + chat) || '[]');
    const ev = list[+d.slice(2)];
    if (!ev) return send_(chat, 'Список турниров устарел — нажмите «По турниру» ещё раз.');
    return send_(chat, stats_({ event: ev, title: 'Турнир: ' + ev }));
  }
}

// ============================================================ файлы football-data → Google Диск
function folder_() {
  const it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

function onFile_(chat, doc) {
  const name = String(doc.file_name || 'file');
  if (!/\.xlsx$/i.test(name)) return send_(chat, 'Нужен файл .xlsx с football-data.co.uk.');
  const f = tg_('getFile', { file_id: doc.file_id });
  if (!f.ok) return send_(chat, 'Не удалось получить файл от Telegram, отправьте ещё раз.');
  const url = 'https://api.telegram.org/file/bot' + PROPS.getProperty('BOT_TOKEN') + '/' + f.result.file_path;
  const blob = UrlFetchApp.fetch(url).getBlob().setName(Utilities.formatDate(new Date(), 'Asia/Tashkent', 'yyyyMMdd_HHmmss') + '_' + name);
  folder_().createFile(blob);
  send_(chat, '📥 Файл <b>' + esc_(name) + '</b> получен. Облако заберёт его при ближайшем запуске (до 2 часов) и пришлёт подтверждение.');
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

function num_(x) { const n = parseFloat(String(x).replace(',', '.')); return isNaN(n) ? null : n; }
function blank_(x) { return x === '' || x === null || x === undefined; }
function day_(s) { return String(s).slice(0, 10); }
function utc_(s) { return new Date(String(s).replace(' ', 'T') + ':00Z'); }

function filt_(rows, f) {
  return rows.filter(function (r) {
    const d = day_(r.start_utc);
    if (f.from && d < f.from) return false;
    if (f.to && d > f.to) return false;
    if (f.event && String(r.comp_name) !== f.event) return false;
    return true;
  });
}

function parsePeriod_(t) {
  const m = t.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (!m) return null;
  const p = function (d, mo, y) { return y + '-' + ('0' + mo).slice(-2) + '-' + ('0' + d).slice(-2); };
  return [p(m[1], m[2], m[3]), p(m[4], m[5], m[6])];
}

// ============================================================ прогноз на 24 ч (как в автоматическом списке)
function forecastList_() {
  const now = new Date(), until = new Date(now.getTime() + 24 * 3600e3);
  const today = Utilities.formatDate(now, 'Asia/Tashkent', 'dd.MM');
  const by = {};
  table_('auto_lines').forEach(function (l) {
    if (!blank_(l.status) || blank_(l.z_lo)) return;
    const t = utc_(l.start_utc);
    if (!(t > now && t <= until)) return;
    (by[String(l.espn_id)] = by[String(l.espn_id)] || []).push(l);
  });
  const ids = Object.keys(by).sort(function (a, b) { return by[a][0].start_utc < by[b][0].start_utc ? -1 : 1; });
  if (!ids.length) return 'В ближайшие 24 часа подходящих матчей нет. Как только появятся, список придёт сам.';
  const groups = {}, order = [];
  ids.forEach(function (id) {
    const k = String(by[id][0].comp_name);
    if (!groups[k]) { groups[k] = []; order.push(k); }
    groups[k].push(id);
  });
  const out = ['⚽ <b>Прогноз на 24 ч</b> · ' + Utilities.formatDate(now, 'Asia/Tashkent', 'dd.MM HH:mm') + ' · ' + ids.length + ' матч.'];
  order.forEach(function (k) {
    const parts = ['🏆 <b>' + esc_(k) + '</b>'];
    groups[k].forEach(function (id) {
      const ls = by[id].sort(function (a, b) { return a.slot - b.slot; }), m = ls[0];
      const when = String(m.start_local).slice(0, 5) === today ? String(m.start_local).slice(6) : String(m.start_local);
      const rows = ['🕒 <b>' + esc_(when) + '</b> · <b>' + esc_(m.home) + '</b> — <b>' + esc_(m.away) + '</b> · ' + (SRC_RU[m.source] || '')];
      ls.forEach(function (x) { rows.push(esc_(x.label) + ': <b>' + (+x.z_lo).toFixed(2) + '–' + (+x.z_hi).toFixed(2) + '</b>'); });
      parts.push(rows.join('\n'));
    });
    out.push(parts.join('\n\n'));
  });
  return out.join('\n\n');
}

// ============================================================ аналитика
function outcome_(r) {
  if (r.status === 'ET') return 1;
  if (r.status !== 'FT') return null;
  const h = +r.hg, a = +r.ag;
  return h > a ? 0 : (h === a ? 1 : 2);
}

function stats_(f) {
  const F = filt_(table_('auto_forecasts'), f).filter(function (r) { return r.status === 'FT' || r.status === 'ET'; });
  const L = filt_(table_('auto_lines'), f).filter(function (r) { return (r.result === 'W' || r.result === 'L') && !blank_(r.z_lo); });
  const out = ['📊 <b>' + esc_(f.title) + '</b>'];
  if (!F.length && !L.length) return out[0] + '\n\nЗа этот срок доигранных матчей нет.';
  if (F.length) {
    const s = ['<b>Матчи</b> (исход 90 минут):'];
    ['market', 'model'].forEach(function (src) {
      const x = F.filter(function (r) { return r.source === src; });
      if (!x.length) return;
      let hit = 0, ll = 0;
      x.forEach(function (r) {
        const p = [num_(r.p_h), num_(r.p_d), num_(r.p_a)], o = outcome_(r);
        const k = p.indexOf(Math.max.apply(null, p));
        hit += k === o ? 1 : 0; ll += -Math.log(Math.max(p[o], 1e-6));
      });
      s.push(SRC_RU[src] + ': ' + x.length + ' · исход угадан ' + Math.round(hit / x.length * 100) + '% · лог-лосс ' + (ll / x.length).toFixed(4));
    });
    out.push(s.join('\n'));
  }
  if (L.length) {
    const s = ['<b>Линии</b> (показанные в списке, ставка по нижнему кф диапазона):'];
    Object.keys(MARKET_RU).forEach(function (mk) {
      const x = L.filter(function (r) { return r.market === mk; });
      if (!x.length) return;
      const won = x.filter(function (r) { return r.result === 'W'; }).length;
      const pm = mean_(x.map(function (r) { return num_(r.p); }));
      const roi = mean_(x.map(function (r) { return num_(r.pnl_lo); }));
      s.push(MARKET_RU[mk] + ': ' + x.length + ' · попадание ' + Math.round(won / x.length * 100) + '% (модель ' + Math.round(pm * 100) + '%) · ROI ' + fmtPct_(roi));
    });
    ['market', 'model'].forEach(function (src) {
      const x = L.filter(function (r) { return r.source === src; });
      if (x.length) s.push('Источник «' + SRC_RU[src] + '»: ' + x.length + ' · ROI ' + fmtPct_(mean_(x.map(function (r) { return num_(r.pnl_lo); }))));
    });
    const all = mean_(L.map(function (r) { return num_(r.pnl_lo); }));
    s.push('Все линии: ' + L.length + ' · ROI ' + fmtPct_(all) + ' · прибыль ' + (all * L.length).toFixed(2) + ' ед. при ставке 1');
    out.push(s.join('\n'));
  }
  if (f.detailed) {
    const cal = ['<b>Калибровка исхода</b> (прогноз фаворита / факт / n):'];
    [0.4, 0.5, 0.6, 0.7, 0.8].forEach(function (lo) {
      const x = F.filter(function (r) { const p = Math.max(num_(r.p_h), num_(r.p_d), num_(r.p_a)); return p >= lo && (p < lo + 0.1 || lo === 0.8); });
      if (!x.length) return;
      const pf = mean_(x.map(function (r) { return Math.max(num_(r.p_h), num_(r.p_d), num_(r.p_a)); }));
      const h = x.filter(function (r) { const p = [num_(r.p_h), num_(r.p_d), num_(r.p_a)]; return p.indexOf(Math.max.apply(null, p)) === outcome_(r); }).length / x.length;
      cal.push(Math.round(lo * 100) + '–' + Math.round(lo * 100 + 10) + '%: ' + Math.round(pf * 100) + '% / ' + Math.round(h * 100) + '% / ' + x.length);
    });
    out.push(cal.join('\n'));
    const sm = table_('summary').map(function (r) { return esc_(r['показатель'] + ': ' + r['значение']); });
    if (sm.length) out.push('<b>Сводка облака</b>:\n' + sm.join('\n'));
    out.push('Перешлите это сообщение в чат с Claude для разбора.');
  }
  return out.join('\n\n');
}

function mean_(a) { a = a.filter(function (x) { return x !== null; }); return a.length ? a.reduce(function (s, x) { return s + x; }, 0) / a.length : 0; }
function fmtPct_(x) { return (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%'; }

// ============================================================ выбор месяца и турнира
function askMonth_(chat) {
  const ms = {};
  table_('auto_forecasts').forEach(function (r) { const ym = String(r.start_utc).slice(0, 7); if (/^\d{4}-\d{2}$/.test(ym)) ms[ym] = 1; });
  const keys = Object.keys(ms).sort().reverse().slice(0, 12);
  if (!keys.length) return send_(chat, 'Данных пока нет.');
  const kb = keys.map(function (k) { return [{ text: MONTHS[+k.slice(5, 7) - 1] + ' ' + k.slice(0, 4), callback_data: 'm:' + k }]; });
  tg_('sendMessage', { chat_id: chat, text: 'Выберите месяц:', reply_markup: { inline_keyboard: kb } });
}

function askEvent_(chat) {
  const last = {};
  table_('auto_forecasts').forEach(function (r) { const e = String(r.comp_name); if (e && (!last[e] || last[e] < r.start_utc)) last[e] = String(r.start_utc); });
  const evs = Object.keys(last).sort(function (a, b) { return last[a] < last[b] ? 1 : -1; }).slice(0, 30);
  if (!evs.length) return send_(chat, 'Данных пока нет.');
  CacheService.getScriptCache().put('ev' + chat, JSON.stringify(evs), 21600);
  const kb = evs.map(function (e, i) { return [{ text: e.slice(0, 60), callback_data: 't:' + i }]; });
  tg_('sendMessage', { chat_id: chat, text: 'Выберите турнир:', reply_markup: { inline_keyboard: kb } });
}

// ============================================================ очистка чата
function clearChat_(chat, fromId) {
  let batches = 0, single = 0;
  for (let hi = fromId; hi > 0 && hi > fromId - 1500; hi -= 100) {
    const ids = [];
    for (let i = Math.max(1, hi - 99); i <= hi; i++) ids.push(i);
    const r = tg_('deleteMessages', { chat_id: chat, message_ids: ids });
    if (r.ok) { batches++; continue; }
    let fails = 0;
    for (let k = ids.length - 1; k >= 0 && fails < 25; k--) {
      const x = tg_('deleteMessage', { chat_id: chat, message_id: ids[k] });
      if (x.ok) { single++; fails = 0; } else fails++;
    }
  }
  log_('очистка', 'пачек ' + batches + ', по одному ' + single);
  send_(chat, '🧹 Чат очищен. Сообщения старше 48 часов Telegram удалять ботам не даёт — их можно убрать вручную: ' +
    'меню чата ⋮ → «Очистить историю». Свежий прогноз придёт при ближайшем обновлении (до 2 часов) или по кнопке «' + B.list + '».');
}

// ============================================================ настройка (запускать вручную из редактора)
function setup() {
  const sa = PROPS.getProperty('SA_EMAIL');
  if (!sa) throw new Error('Добавьте свойство SA_EMAIL (client_email из service_account.json)');
  const f = folder_();
  f.addEditor(sa);
  Logger.log('Папка ' + FOLDER_NAME + ' готова и открыта для ' + sa + ': ' + f.getUrl());
}

function setWebhook() {
  const url = PROPS.getProperty('WEBAPP_URL');
  if (!url) throw new Error('Добавьте свойство WEBAPP_URL (адрес веб-приложения, оканчивается на /exec)');
  const r = tg_('setWebhook', { url: url, allowed_updates: ['message', 'callback_query'], drop_pending_updates: true });
  Logger.log(JSON.stringify(r));
  tg_('setMyCommands', { commands: [{ command: 'start', description: 'Меню' }] });
}

function checkWebhook() { Logger.log(JSON.stringify(tg_('getWebhookInfo', {}))); }
