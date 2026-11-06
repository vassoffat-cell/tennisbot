"""Сопоставление названий команд ESPN («Manchester United») с football-data («Man United»).
Сначала словарь соответствий, затем нечёткий поиск среди команд страны (для еврокубков — всех стран).
Неопознанные команды не угадываются: матч пропускается и попадает в журнал запуска."""
from __future__ import annotations

import difflib
import re
import unicodedata

ALIASES = {
    # Англия
    'manchester city': 'Man City', 'manchester united': 'Man United', 'nottingham forest': "Nott'm Forest",
    'wolverhampton wanderers': 'Wolves', 'tottenham hotspur': 'Tottenham', 'newcastle united': 'Newcastle',
    'west ham united': 'West Ham', 'brighton & hove albion': 'Brighton', 'brighton and hove albion': 'Brighton',
    'afc bournemouth': 'Bournemouth', 'leeds united': 'Leeds', 'ipswich town': 'Ipswich', 'hull city': 'Hull',
    'coventry city': 'Coventry', 'leicester city': 'Leicester', 'sheffield wednesday': 'Sheffield Weds',
    'queens park rangers': 'QPR', 'west bromwich albion': 'West Brom', 'preston north end': 'Preston', 'stoke city': 'Stoke',
    'swansea city': 'Swansea', 'cardiff city': 'Cardiff', 'norwich city': 'Norwich', 'birmingham city': 'Birmingham',
    'blackburn rovers': 'Blackburn', 'bolton wanderers': 'Bolton', 'derby county': 'Derby', 'charlton athletic': 'Charlton',
    'lincoln city': 'Lincoln', 'oxford united': 'Oxford', 'luton town': 'Luton', 'plymouth argyle': 'Plymouth',
    # Испания
    'atletico madrid': 'Ath Madrid', 'atletico de madrid': 'Ath Madrid', 'athletic club': 'Ath Bilbao',
    'athletic bilbao': 'Ath Bilbao', 'real sociedad': 'Sociedad', 'celta vigo': 'Celta', 'celta de vigo': 'Celta',
    'espanyol': 'Espanol', 'rcd espanyol': 'Espanol', 'rayo vallecano': 'Vallecano', 'real betis': 'Betis',
    'deportivo alaves': 'Alaves', 'alaves': 'Alaves', 'deportivo la coruna': 'La Coruna', 'racing santander': 'Santander',
    'real oviedo': 'Oviedo', 'sporting gijon': 'Sp Gijon', 'real valladolid': 'Valladolid', 'las palmas': 'Las Palmas',
    'leganes': 'Leganes', 'real zaragoza': 'Zaragoza',
    # Италия
    'internazionale': 'Inter', 'inter milan': 'Inter', 'ac milan': 'Milan', 'hellas verona': 'Verona', 'as roma': 'Roma',
    'ss lazio': 'Lazio', 'ssc napoli': 'Napoli',
    # Германия
    'borussia dortmund': 'Dortmund', 'eintracht frankfurt': 'Ein Frankfurt', '1 fc koln': 'FC Koln', 'fc cologne': 'FC Koln',
    'koln': 'FC Koln', 'bayer leverkusen': 'Leverkusen', 'borussia monchengladbach': "M'gladbach", 'mainz': 'Mainz',
    'mainz 05': 'Mainz', '1 fsv mainz 05': 'Mainz', 'hamburg sv': 'Hamburg', 'hamburger sv': 'Hamburg',
    '1 fc heidenheim 1846': 'Heidenheim', 'tsg hoffenheim': 'Hoffenheim', 'st pauli': 'St Pauli', 'fc st pauli': 'St Pauli',
    'vfb stuttgart': 'Stuttgart', '1 fc union berlin': 'Union Berlin', 'union berlin': 'Union Berlin',
    'vfl wolfsburg': 'Wolfsburg', 'sv elversberg': 'Elversberg', 'sc paderborn 07': 'Paderborn', 'fc augsburg': 'Augsburg',
    'sc freiburg': 'Freiburg', 'werder bremen': 'Werder Bremen',
    # Франция
    'paris saint germain': 'Paris SG', 'olympique marseille': 'Marseille', 'olympique lyonnais': 'Lyon', 'as monaco': 'Monaco',
    'stade rennais': 'Rennes', 'stade brestois 29': 'Brest', 'stade brestois': 'Brest', 'losc lille': 'Lille',
    'ogc nice': 'Nice', 'aj auxerre': 'Auxerre', 'le havre ac': 'Le Havre', 'saint etienne': 'St Etienne',
    'as saint etienne': 'St Etienne', 'rc strasbourg': 'Strasbourg',
    # Португалия
    'sporting cp': 'Sp Lisbon', 'sporting lisbon': 'Sp Lisbon', 'sc braga': 'Sp Braga', 'braga': 'Sp Braga',
    'fc porto': 'Porto', 'vitoria de guimaraes': 'Guimaraes', 'vitoria guimaraes': 'Guimaraes', 'vitoria sc': 'Guimaraes',
    'estrela da amadora': 'Estrela', 'avs futebol': 'AVS', 'avs futebol sad': 'AVS', 'academico de viseu': 'Academico Viseu',
    'sl benfica': 'Benfica',
    # Турция
    'istanbul basaksehir': 'Buyuksehyr', 'basaksehir': 'Buyuksehyr', 'goztepe': 'Goztep', 'fatih karagumruk': 'Karagumruk',
    'caykur rizespor': 'Rizespor', 'gaziantep fk': 'Gaziantep', 'genclerbirligi': 'Genclerbirligi', 'amed sfk': 'Amedspor',
    'corum fk': 'Corum',
    # Нидерланды
    'psv': 'PSV Eindhoven', 'fc twente': 'Twente', 'fc utrecht': 'Utrecht', 'fortuna sittard': 'For Sittard',
    'fc groningen': 'Groningen', 'sc heerenveen': 'Heerenveen', 'heracles almelo': 'Heracles', 'nec nijmegen': 'Nijmegen',
    'nec': 'Nijmegen', 'fc volendam': 'Volendam', 'pec zwolle': 'Zwolle', 'sc cambuur': 'Cambuur', 'ado den haag': 'Den Haag',
    'az': 'AZ Alkmaar',
    # Бельгия
    'royal antwerp': 'Antwerp', 'krc genk': 'Genk', 'kaa gent': 'Gent', 'kv mechelen': 'Mechelen', 'oh leuven': 'Oud-Heverlee Leuven',
    'union st gilloise': 'St. Gilloise', 'union saint gilloise': 'St. Gilloise', 'royale union saint gilloise': 'St. Gilloise',
    'standard liege': 'Standard', 'sint truiden': 'St Truiden', 'zulte waregem': 'Waregem', 'raal la louviere': 'RAAL La Louviere',
    'la louviere': 'RAAL La Louviere', 'lommel': 'Lommel SK', 'kv kortrijk': 'Kortrijk', 'sporting charleroi': 'Charleroi',
    # Шотландия
    'heart of midlothian': 'Hearts', 'st mirren': 'St Mirren', 'st johnstone': 'St Johnstone',
    # Россия
    'zenit st petersburg': 'Zenit', 'zenit saint petersburg': 'Zenit', 'fc krasnodar': 'Krasnodar', 'fc rostov': 'FK Rostov',
    'rostov': 'FK Rostov', 'krylia sovetov': 'Krylya Sovetov', 'pari nizhny novgorod': 'Pari NN', 'baltika kaliningrad': 'Baltika',
    'akhmat': 'Akhmat Grozny',
    # еврокубковые написания (UEFA / openfootball)
    'olympiakos piraeus': 'Olympiakos', 'pae olympiakos sfp': 'Olympiakos', 'olympiacos': 'Olympiakos', 'lille osc': 'Lille',
    'sporting braga': 'Sp Braga', 'sporting clube de braga': 'Sp Braga', 'rsc anderlecht': 'Anderlecht',
    'cska moskva': 'CSKA Moscow', 'lokomotiv moskva': 'Lokomotiv Moscow', 'spartak moskva': 'Spartak Moscow',
    'paok saloniki': 'PAOK', 'aek athen': 'AEK', 'aek athens': 'AEK', 'aris saloniki': 'Aris', 'acf fiorentina': 'Fiorentina',
    'lazio roma': 'Lazio', 'club atletico de madrid': 'Ath Madrid', 'fc internazionale milano': 'Inter',
    'sport lisboa e benfica': 'Benfica', 'atalanta bc': 'Atalanta', 'sporting clube de portugal': 'Sp Lisbon',
    '1899 hoffenheim': 'Hoffenheim', 'bor monchengladbach': "M'gladbach", 'feyenoord rotterdam': 'Feyenoord',
    'real sociedad de futbol': 'Sociedad', 'olympique de marseille': 'Marseille', 'montpellier hsc': 'Montpellier',
    'racing club de lens': 'Lens', 'rc lens': 'Lens', 'racing genk': 'Genk', 'ajax amsterdam': 'Ajax', 'gazovik orenburg': 'Orenburg',
    'fc baltika kaliningrad': 'Baltika', 'c d nacional': 'Nacional', 'cd nacional': 'Nacional', 'deportivo': 'La Coruna',
}
DROP = r'\b(fc|cf|afc|sc|ac|as|ss|ssc|sv|vfl|vfb|tsg|rcd|cd|ud|sd|ca|club|calcio|sk|fk|kv|krc|kaa|bk)\b'


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"[.\-'’]", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _core(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(DROP, ' ', norm(s))).strip()


def match(espn_name: str, candidates: set[str]) -> str | None:
    """Название football-data для команды ESPN среди candidates или None."""
    n = norm(espn_name)
    if n in ALIASES and ALIASES[n] in candidates:
        return ALIASES[n]
    by_norm = {norm(c): c for c in candidates}
    if n in by_norm:
        return by_norm[n]
    core = _core(espn_name)
    by_core = {}
    for c in candidates:
        by_core.setdefault(_core(c), []).append(c)
    if core in by_core and len(by_core[core]) == 1:
        return by_core[core][0]
    scored = sorted(((difflib.SequenceMatcher(None, core, k).ratio(), v[0]) for k, v in by_core.items() if len(v) == 1),
                    reverse=True)
    if scored and scored[0][0] >= 0.86 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
        return scored[0][1]
    return None
