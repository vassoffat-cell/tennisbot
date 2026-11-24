function planTick_() {
  const token = PROPS.getProperty('GH_TOKEN');
  const sh = token ? ss_().getSheetByName('plan') : null;
  if (!sh || sh.getLastRow() < 2) return;
  const now = Date.now();
  const cache = CacheService.getScriptCache();
  const rows = sh.getRange(2, 1, sh.getLastRow() - 1, 3).getDisplayValues();
  for (var i = 0; i < rows.length; i++) {
    const at = utc_(rows[i][0]), hi = utc_(rows[i][1]);
    if (!at || !hi || now < at || now > hi + 10 * 60 * 1000) continue;
    const key = 'plan|' + BOT.workflow + '|' + rows[i][0];
    if (cache.get(key)) continue;
    const h = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' };
    const api = 'https://api.github.com/repos/' + BOT.repo + '/actions/workflows/' + BOT.workflow + '/';
    const since = new Date(at - 15 * 60 * 1000).toISOString().slice(0, 19) + 'Z';
    const r = UrlFetchApp.fetch(api + 'runs?per_page=5&created=%3E%3D' + since, { headers: h, muteHttpExceptions: true });
    if (r.getResponseCode() !== 200) { log_('план: проверка ' + BOT.workflow, r.getResponseCode()); return; }
    // 25.11: разовые прогоны (репетиция, проверка research) и упавшие/отменённые не закрывают окно матча
    const full = (JSON.parse(r.getContentText()).workflow_runs || []).filter(function (x) {
      return !/rehearse|research/.test(String(x.display_title || x.name || '')) &&
             ['failure', 'cancelled', 'startup_failure', 'timed_out'].indexOf(String(x.conclusion)) < 0;
    });
    if (!full.length) {
      const d = UrlFetchApp.fetch(api + 'dispatches', { method: 'post', contentType: 'application/json',
        payload: JSON.stringify({ ref: 'main' }), headers: h, muteHttpExceptions: true });
      log_('план ' + rows[i][0] + ' UTC · ' + rows[i][2], d.getResponseCode() === 204 ? 'запущено' : d.getResponseCode());
    }
    cache.put(key, '1', 21600);
    return;
  }
}
