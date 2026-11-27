// 27.11 14:35 (В9, «давай»): ВСЁ меню «Аналитика / История» — картинками (месяц, год, прошлые дни — из ветки репозитория cards-fb / cards-tn).
// Ставится в ОБА бота одинаково. Что сделать: в Code.gs ЗАМЕНИТЬ функцию out_ целиком на новую (ниже) и ДОБАВИТЬ 4 новые функции:
// fname_, ghRaw_, ghIdx_, photoBlob_ (ниже, целиком). Остальное (img_, show_, tick) — без изменений (поставлено 27.11 14:06).
// Нужен GH_TOKEN в свойствах скрипта (уже есть — им запускаются прогоны).

function out_(chat, key, markup) {
  const blocks = text_(key);
  if (!blocks) return send_(chat, 'За этот период данных нет.', markup);
  const folded = blocks.filter(function (b) { return String(b).indexOf('<blockquote expandable>') >= 0; });
  const tail = function (ids) { return folded.length ? ids.concat(send_(chat, folded, markup)) : ids; };
  const imgs = img_(key);
  if (imgs.length) {                                      // 1) отчёт уже уходил картинкой — по file_id, мгновенно
    const ids = [];
    imgs.forEach(function (fid, i) {
      const p = { chat_id: chat, photo: fid, has_spoiler: true };
      if (i === imgs.length - 1 && !folded.length) p.reply_markup = markup || MENU;
      const r = tg_('sendPhoto', p);
      if (r && r.ok) ids.push(r.result.message_id);
    });
    if (ids.length === imgs.length) return tail(ids);
    del_(chat, ids);
  }
  const br = key.indexOf('fb|') === 0 ? 'cards-fb' : 'cards-tn';   // 2) картинка из ветки репозитория (месяц, год, прошлые дни)
  const n = +(ghIdx_(br)[key] || 0);
  if (n) {
    const ids = [];
    for (let i = 1; i <= n; i++) {
      const blob = ghRaw_(br, fname_(key, i));
      if (!blob) break;
      const r = photoBlob_(chat, blob, i === n && !folded.length ? (markup || MENU) : null);
      if (r && r.ok) ids.push(r.result.message_id); else break;
    }
    if (ids.length === n) return tail(ids);
    del_(chat, ids);
  }
  return send_(chat, blocks, markup);                     // 3) нет картинки — текст, как раньше
}

function fname_(key, i) { return String(key).replace(/[^0-9A-Za-z\-]/g, '_') + '_' + i + '.png'; }

function ghRaw_(br, path) {
  const token = PROPS.getProperty('GH_TOKEN');
  if (!token) return null;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + BOT.repo + '/contents/' + path + '?ref=' + br,
    { headers: { Authorization: 'token ' + token, Accept: 'application/vnd.github.raw' }, muteHttpExceptions: true });
  if (r.getResponseCode() !== 200 && path === 'index.json') log_('картинки меню: GitHub ' + r.getResponseCode(), br);   // 403/404 — у GH_TOKEN нет «Contents: Read»
  return r.getResponseCode() === 200 ? r.getBlob() : null;
}

function ghIdx_(br) {
  const c = CacheService.getScriptCache(), ck = 'cidx_' + br;
  const v = c.get(ck);
  if (v) { try { return JSON.parse(v); } catch (e) {} }
  const b = ghRaw_(br, 'index.json');
  if (!b) return {};
  const t = b.getDataAsString();
  try { c.put(ck, t, 600); } catch (e) {}
  try { return JSON.parse(t); } catch (e) { return {}; }
}

function photoBlob_(chat, blob, markup) {
  const p = { chat_id: String(chat), photo: blob.setName('card.png'), has_spoiler: 'true' };
  if (markup) p.reply_markup = JSON.stringify(markup);
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + PROPS.getProperty('BOT_TOKEN') + '/sendPhoto',
    { method: 'post', payload: p, muteHttpExceptions: true });
  let res;
  try { res = JSON.parse(r.getContentText()); } catch (e) { res = { ok: false, description: r.getContentText().slice(0, 200) }; }
  if (!res.ok) log_('ошибка sendPhoto', res.description || res);
  return res;
}
