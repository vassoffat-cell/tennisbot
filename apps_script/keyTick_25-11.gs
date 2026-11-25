// 25.11 Р8: до 11:00 ждём итоги; 11:15 — отчёт + самообучение, 11:30 — ⭐1 новых суток; хоккей — 12:15 / 12:30
const KEY_SLOTS = [['daily.yml', 11, 15], ['daily.yml', 11, 30], ['daily.yml', 12, 15], ['daily.yml', 12, 30],   // 🎾 отчёт · 🎾1 · 🏒 отчёт · 🏒1
                   ['football.yml', 11, 15], ['football.yml', 11, 30]];                                            // ⚽ отчёт · ⚽1
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
