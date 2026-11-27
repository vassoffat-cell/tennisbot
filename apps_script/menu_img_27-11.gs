// 27.11 (В9) «Аналитика» и «История» — картинками. Ставится в ОБА бота (FootBot и TennisBot) одинаково.
// Что сделать (инструкция — в сообщении чата):
//   1) ДОБАВИТЬ в Code.gs две новые функции img_ и out_ (ниже, целиком);
//   2) ЗАМЕНИТЬ две строки в show_ и две строки в tick (ниже — «было → стало»).

// ============================================================ 27.11 (В9): отчёты в меню — картинками
// Облако кладёт в лист archive (столбец I «img») file_id картинок отчёта, когда отчёт ушёл картинкой.
// Есть картинки — меню шлёт их (без загрузки, мгновенно) + свёрнутые списки текстом; нет — текст, как раньше.
function img_(key) {
  const sh = ss_().getSheetByName('archive');
  if (!sh || sh.getLastRow() < 2 || sh.getLastColumn() < 9) return [];
  const ks = sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues();
  for (let i = ks.length - 1; i >= 0; i--) {
    if (String(ks[i][0]) === key) {
      try { const v = JSON.parse(String(sh.getRange(i + 2, 9).getValue() || '[]')); return Array.isArray(v) ? v : []; }
      catch (e) { return []; }
    }
  }
  return [];
}

function out_(chat, key, markup) {
  const blocks = text_(key);
  if (!blocks) return send_(chat, 'За этот период данных нет.', markup);
  const imgs = img_(key);
  if (!imgs.length) return send_(chat, blocks, markup);
  const folded = blocks.filter(function (b) { return String(b).indexOf('<blockquote expandable>') >= 0; });
  const ids = [];
  imgs.forEach(function (fid, i) {
    const p = { chat_id: chat, photo: fid, has_spoiler: true };
    if (i === imgs.length - 1 && !folded.length) p.reply_markup = markup || MENU;
    const r = tg_('sendPhoto', p);
    if (r && r.ok) ids.push(r.result.message_id);
  });
  if (ids.length < imgs.length) return ids.concat(send_(chat, blocks, markup));   // картинка не ушла — тот же отчёт текстом
  return folded.length ? ids.concat(send_(chat, folded, markup)) : ids;
}


// ---------- show_: было
//   const blocks = text_(key);
//   s.shown = blocks ? send_(chat, blocks, { inline_keyboard: [] }) : send_(chat, 'За этот период данных нет.', { inline_keyboard: [] });
// ---------- show_: стало
//   const blocks = text_(key);
//   s.shown = out_(chat, key, { inline_keyboard: [] });

// ---------- tick: было
//   const blocks = s.lastKey ? text_(s.lastKey) : (s.lastText ? [s.lastText] : null);
//   if (blocks) send_(chat, blocks);
// ---------- tick: стало
//   if (s.lastKey) out_(chat, s.lastKey);
//   else if (s.lastText) send_(chat, [s.lastText]);
