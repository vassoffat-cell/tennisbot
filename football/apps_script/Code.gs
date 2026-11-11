/**
 * FootBot — Telegram-бот на Google Apps Script (бесплатно, круглосуточно в вашем аккаунте Google). Версия 4 (08.11.2026): меню «Аналитики» — из облака, новые разделы без правки скрипта.
 *
 *   • кнопки: «Прогноз 24 ч», «Аналитика» (разделы задаёт облако: всё время, месяц, турнир, 🧠 самообучение …),
 *     «⭐ Ставка дня», «Очистить чат»;
 *   • ⭐ ставка дня с кнопками «✅ Принимаем» / «🔄 Другую» — нажатие записывается в лист dp_actions и сразу запускает облако;
 *   • скриншот 1xBet любого матча (фото) → папка Google Диска «footbot_inbox» → облако распознаёт кф и подбирает ставку дня;
 *     скриншот можно прислать ответом на сообщение со списком — так бот точнее поймёт, какой это матч;
 *   • файлы данных (tennis-data / football-data) → та же папка; облако проверяет содержимое.
 * Все тексты готовит облако: лист tg_cache — список прогноза и текст ставки дня, лист reports — отчёты.
 *
 * Свойства скрипта (Настройки проекта → Свойства скрипта):
 *   BOT_TOKEN, ALLOWED_USERS, SA_EMAIL, WEBAPP_URL, (необязательно) SHEET_ID,
 *   GH_TOKEN — токен GitHub (только репозиторий tennisbot, право Actions: Read and write) — мгновенный запуск облака.
 */

const PROPS = PropertiesService.getScriptProperties();
const BOT = { name: 'FootBot', folder: 'footbot_inbox', workflow: 'football.yml', repo: 'vassoffat-cell/tennisbot' };

const B = { list: '⚽ Прогноз 24 ч', stats: '📊 Аналитика', pick: '⭐ Ставка дня', clear: '🧹 Очистить чат' };
const MENU = { keyboard: [[{ text: B.pick }, { text: B.stats }]],   // 12.11.2026: убраны «Прогноз 24 ч» и «Очистить чат»
               resize_keyboard: true, is_persistent: true };
const STATS_KB = { inline_keyboard: [[{ text: '📊 Всё время', callback_data: 'r:all' }, { text: '📅 По месяцу', callback_data: 'r:month' },
                                      { text: '🏆 По турниру', callback_data: 'r:event' }]] };
const DP_KB = { inline_keyboard: [[{ text: '✅ Принимаем', callback_data: 'dp:yes' }, { text: '🔄 Другую', callback_data: 'dp:no' }]] };

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
  const blocks = Array.isArray(text) ? text : String(text).split('\n\n');   // массив — готовые блоки, не резать
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

// ============================================================ мгновенный запуск облака (GitHub Actions)
function dispatch_() {
  const token = PROPS.getProperty('GH_TOKEN');
  if (!token) return false;
  const cache = CacheService.getScriptCache();
  if (cache.get('dispatch')) return true;                    // не чаще раза в 40 с: облако всё равно обработает всё сразу
  cache.put('dispatch', '1', 40);
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + BOT.repo + '/actions/workflows/' + BOT.workflow + '/dispatches', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify({ ref: 'main' }), muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' }
  });
  if (r.getResponseCode() !== 204) { log_('ошибка запуска облака', r.getResponseCode() + ' ' + r.getContentText().slice(0, 200)); return false; }
  return true;
}

function waitText_(ok) { return ok ? '1–2 минуты' : 'до 2 часов (мгновенный запуск не настроен: свойство GH_TOKEN)'; }

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
  if (t === B.list) return remember_(chat, send_(chat, cachedList_()));
  if (t === B.stats) return tg_('sendMessage', { chat_id: chat, text: '📊 <b>Аналитика</b> — выберите раздел:', parse_mode: 'HTML', reply_markup: statsKb_() });
  if (t === B.pick) return showPick_(chat);
  if (t === B.clear) return clearChat_(chat, m.message_id);
  send_(chat, '⚽ <b>FootBot</b>\nПрогноз на 24 ч (17 турниров) приходит сам каждые 2 часа: у матча — исход и линии с вероятностью (🟢 высокая, 🟡 средняя, ⚪ низкая уверенность).\n⭐ <b>Ставка дня</b> — закреплённое сообщение и кнопка внизу: кф 1xBet ≥ 1,35, максимальная вероятность; «✅ Принимаем» фиксирует ставку, «🔄 Другую» — бот подберёт следующую. Нет нужной линии или кф ниже — пришлите скриншот страницы матча 1xBet (любого матча из списка; лучше ответом на сообщение со списком): бот распознает кф и подберёт ставку.\nВ 00:30 — суточный отчёт. По понедельникам — напоминание о файлах football-data.\n\nКнопки — внизу.');
}

function onCallback_(q) {
  const chat = q.message.chat.id;
  tg_('answerCallbackQuery', { callback_query_id: q.id });
  if (!allowed_(q.from.id)) return;
  const d = String(q.data || '');
  if (d === 'dp:yes' || d === 'dp:no') return dpAction_(chat, q.message.message_id, d.slice(3));
  const rep = table_('reports');
  if (d === 'r:month' || d === 'r:event') {
    const kind = d === 'r:month' ? 'month' : 'event';
    const list = rep.map(function (r, i) { return { r: r, i: i }; }).filter(function (x) { return x.r.kind === kind; });
    if (!list.length) return send_(chat, 'Данных пока нет — отчёты появятся после первых доигранных матчей.');
    const kb = list.slice(0, 30).map(function (x) { return [{ text: String(x.r.title).slice(0, 60), callback_data: 'k:' + x.i }]; });
    return tg_('sendMessage', { chat_id: chat, text: kind === 'month' ? 'Выберите месяц:' : 'Выберите турнир:', reply_markup: { inline_keyboard: kb } });
  }
  if (d.indexOf('k:') === 0) return sendReport_(chat, rep[+d.slice(2)]);
  if (d.indexOf('r:') === 0) {                              // любой раздел отчёта по ключу (r:all, r:learn, …)
    const key = d.slice(2);
    return sendReport_(chat, rep.filter(function (r) { return String(r.key) === key; })[0]);
  }
}

function statsKb_() {
  // Разделы «Аналитики» задаёт облако (лист tg_cache, ключ stats_menu): [[{text, data}], …]; нет — стандартные.
  try {
    const c = table_('tg_cache').filter(function (r) { return r.key === 'stats_menu'; })[0];
    const rows = c ? JSON.parse(String(c.text)) : null;
    if (rows && rows.length) {
      return { inline_keyboard: rows.map(function (row) {
        return row.map(function (b) { return { text: String(b.text), callback_data: String(b.data).slice(0, 60) }; });
      }) };
    }
  } catch (e) { log_('меню аналитики', String(e)); }
  return STATS_KB;
}

function sendReport_(chat, r) {
  if (!r) return send_(chat, 'Отчёт обновился — нажмите «' + B.stats + '» ещё раз.');
  send_(chat, String(r.text) + '\n\n<i>обновлено ' + esc_(String(r.updated)) + ' UTC</i>');
}

function cachedList_() {
  const c = table_('tg_cache').filter(function (r) { return r.key === 'list'; })[0];
  let blocks = [];
  try { blocks = c ? JSON.parse(String(c.text)) : []; } catch (e) { blocks = []; }
  if (!blocks.length) return 'В ближайшие 24 часа матчей с прогнозом нет. Как только появятся, список придёт сам.';
  return blocks;
}

// ============================================================ ⭐ ставка дня
function showPick_(chat) {
  // 12.11.2026: ставки дня живут в закреплённых сообщениях (каждая — отдельным). Кнопка запускает облако:
  // оно проверяет закрепы и заново присылает удалённые. Кнопок ✅/🔄 больше нет.
  const ok = dispatch_();
  send_(chat, '📌 Ставки дня — в закреплённых сообщениях вверху чата. Удалённые пришлю заново через ' + waitText_(ok) + '.');
}

function dpAction_(chat, messageId, action) {
  const ss = ss_();
  const sh = ss.getSheetByName('dp_actions') || ss.insertSheet('dp_actions');
  if (sh.getLastRow() === 0) sh.appendRow(['time', 'action', 'chat_id', 'message_id', 'done']);
  sh.appendRow([Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HH:mm'), action, chat, messageId, '']);
  tg_('editMessageReplyMarkup', { chat_id: chat, message_id: messageId, reply_markup: { inline_keyboard: [] } });
  const ok = dispatch_();
  send_(chat, action === 'yes' ? '✅ Принято. Ставка дня будет зафиксирована (' + waitText_(ok) + ').'
                               : '🔄 Ищу другую ставку дня — в этом или другом матче (' + waitText_(ok) + ').');
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
  send_(chat, '📸 Скриншот получен — распознаю кф 1xBet и подберу ставку дня (' + waitText_(ok) + ').');
}

function onFile_(chat, doc) {
  const name = String(doc.file_name || 'file');
  const blob = fetchBlob_(doc.file_id, name);
  if (!blob) return send_(chat, 'Не удалось получить файл от Telegram (больше 20 МБ?) — отправьте ещё раз.');
  folder_().createFile(blob);
  const ok = dispatch_();
  send_(chat, '📥 Файл <b>' + esc_(name) + '</b> получен. Облако проверит содержимое (' + waitText_(ok) + ') ' +
    'и ответит: принят или нет и почему.');
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
  Logger.log('Папка ' + BOT.folder + ' готова и открыта для ' + sa + ': ' + f.getUrl());
}

function setWebhook() {
  const url = PROPS.getProperty('WEBAPP_URL');
  if (!url) throw new Error('Добавьте свойство WEBAPP_URL (адрес веб-приложения, оканчивается на /exec)');
  const r = tg_('setWebhook', { url: url, allowed_updates: ['message', 'callback_query'], drop_pending_updates: true });
  Logger.log(JSON.stringify(r));
  tg_('setMyCommands', { commands: [{ command: 'start', description: 'Меню' }] });
}

function checkWebhook() { Logger.log(JSON.stringify(tg_('getWebhookInfo', {}))); }

// Проверка мгновенного запуска облака: выполнить один раз из редактора после добавления GH_TOKEN.
function testDispatch() { CacheService.getScriptCache().remove('dispatch'); Logger.log(dispatch_() ? 'облако запущено' : 'не запущено — см. лист bot_log'); }
