"""⏱ Профиль прогона (14.11, экономия минут Ц7): где уходит время — сеть по сайтам (urllib и requests/gspread) и итог.
Подключается одной строкой (prof.install()) и печатает одну строку в журнал GitHub (prof.report()). Ничего не меняет в расчётах."""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
from collections import defaultdict

T0 = time.time()
NET: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])


def _host(url) -> str:
    try:
        u = url.full_url if hasattr(url, 'full_url') else str(url)
        return urllib.parse.urlparse(u).netloc.split(':')[0].replace('www.', '') or '?'
    except Exception:
        return '?'


def install() -> None:
    orig = urllib.request.urlopen

    def urlopen(url, *a, **k):
        t = time.time()
        try:
            return orig(url, *a, **k)
        finally:
            x = NET[_host(url)]; x[0] += 1; x[1] += time.time() - t
    urllib.request.urlopen = urlopen
    try:
        import requests
        orig_r = requests.Session.request

        def request(self, method, url, *a, **k):
            t = time.time()
            try:
                return orig_r(self, method, url, *a, **k)
            finally:
                x = NET[_host(url)]; x[0] += 1; x[1] += time.time() - t
        requests.Session.request = request
    except Exception:
        pass


def report() -> str:
    tot = time.time() - T0
    net = sum(v[1] for v in NET.values())
    top = sorted(NET.items(), key=lambda kv: -kv[1][1])[:6]
    return (f'⏱ прогон {tot:.0f} с, сеть {net:.0f} с: '
            + ', '.join(f'{h} {int(n)}×{s:.0f}с' for h, (n, s) in top))
