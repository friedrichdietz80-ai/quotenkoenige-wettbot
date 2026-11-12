"""Kostenlose Datenquelle: ESPNs öffentliche JSON-Endpunkte (kein API-Key nötig).
Liefert Spielplan, Quoten (Moneyline/Handicap/Über-Unter), Verletzte und Aufstellungen.
Inoffiziell: ESPN kann das Format ändern – darum wird alles defensiv ausgelesen."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

log = logging.getLogger("espn")
BASE = "https://site.api.espn.com/apis/site/v2/sports"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
HOSTS = ["https://site.api.espn.com", "https://site.web.api.espn.com"]


@dataclass
class Selection:
    id: str
    market: str            # h2h / totals / spreads / extra
    label: str
    best_odds: float       # Dezimalquote
    best_book: str
    fair_prob: float | None
    from_search: bool = False   # True = Quote stammt aus Claudes Websuche (bitte prüfen)


@dataclass
class Event:
    id: str
    league_key: str
    home: str
    away: str
    kickoff: datetime
    group: str = ""        # z. B. "Group A1" (Nations League)


def dig(obj, *path, default=None):
    for p in path:
        if isinstance(obj, dict):
            obj = obj.get(p)
        elif isinstance(obj, list) and isinstance(p, int) and len(obj) > p:
            obj = obj[p]
        else:
            return default
        if obj is None:
            return default
    return obj


def american_to_decimal(val) -> float | None:
    """'+180' -> 2.80, '-220' -> 1.45, 'EVEN' -> 2.0"""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s in ("EVEN", "EV"):
        return 2.0
    try:
        ml = float(s.replace("+", ""))
    except ValueError:
        return None
    if ml == 0:
        return None
    if 1.0 < ml < 30 and "." in s:  # war schon dezimal
        return ml
    return round(1 + ml / 100, 3) if ml > 0 else round(1 + 100 / abs(ml), 3)


async def _get(session: aiohttp.ClientSession, url: str, **params) -> dict:
    """Probiert beide ESPN-Hosts, falls einer mit 403/400 antwortet."""
    last_exc = None
    for host in HOSTS:
        u = url.replace(HOSTS[0], host)
        try:
            async with session.get(u, params=params, headers=HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=30)) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        except aiohttp.ClientResponseError as exc:
            last_exc = exc
            if exc.status not in (400, 403, 404):
                raise
    raise last_exc


async def list_events(session: aiohttp.ClientSession, league_key: str) -> list[Event]:
    """league_key z. B. 'soccer/ger.1', 'football/nfl', 'basketball/nba'."""
    today = datetime.now(timezone.utc)
    events: dict[str, Event] = {}
    for offset in (-1, 0, 1, 2):  # ESPN-Tage sind US-Zeit -> großzügig abfragen
        day = (today + timedelta(days=offset)).strftime("%Y%m%d")
        data = await _get(session, f"{BASE}/{league_key}/scoreboard", dates=day)
        for e in data.get("events", []):
            comps = dig(e, "competitions", 0, "competitors", default=[])
            home = next((c for c in comps if c.get("homeAway") == "home"), None)
            away = next((c for c in comps if c.get("homeAway") == "away"), None)
            if not home or not away or not e.get("date"):
                continue
            events[e["id"]] = Event(
                id=e["id"], league_key=league_key,
                home=dig(home, "team", "displayName", default="?"),
                away=dig(away, "team", "displayName", default="?"),
                kickoff=datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
                group=dig(e, "competitions", 0, "groups", "name", default="") or "",
            )
    return list(events.values())


async def summary(session: aiohttp.ClientSession, league_key: str, event_id: str) -> dict:
    return await _get(session, f"{BASE}/{league_key}/summary", event=event_id)


# ---------- Quoten ----------
def _first(*vals):
    return next((v for v in vals if v is not None), None)


def build_selections(summ: dict, ev: Event) -> list[Selection]:
    sel: dict[str, dict] = {}
    fair_acc: dict[str, list[float]] = {}

    def add(sid, market, label, odds, book, group):
        if not odds or odds <= 1.0:
            return
        entry = sel.setdefault(sid, {"market": market, "label": label, "prices": []})
        entry["prices"].append((odds, book))
        groups.setdefault(group, []).append((sid, odds))

    for pc in summ.get("pickcenter") or []:
        book = dig(pc, "provider", "name", default="ESPN")
        groups: dict[str, list[tuple[str, float]]] = {}
        # Moneyline / 1X2 (altes und neues ESPN-Format)
        h = american_to_decimal(_first(dig(pc, "homeTeamOdds", "moneyLine"),
                                       dig(pc, "moneyline", "home", "close", "odds"),
                                       dig(pc, "moneyline", "home", "open", "odds")))
        a = american_to_decimal(_first(dig(pc, "awayTeamOdds", "moneyLine"),
                                       dig(pc, "moneyline", "away", "close", "odds"),
                                       dig(pc, "moneyline", "away", "open", "odds")))
        d = american_to_decimal(_first(dig(pc, "drawOdds", "moneyLine"),
                                       dig(pc, "moneyline", "draw", "close", "odds"),
                                       dig(pc, "moneyline", "draw", "open", "odds")))
        add(f"h2h|{ev.home}", "h2h", f"Sieg {ev.home}", h, book, "h2h")
        add(f"h2h|{ev.away}", "h2h", f"Sieg {ev.away}", a, book, "h2h")
        add("h2h|Draw", "h2h", "Unentschieden", d, book, "h2h")
        # Über/Unter
        total = pc.get("overUnder")
        if total is not None:
            o = american_to_decimal(_first(pc.get("overOdds"), dig(pc, "total", "over", "close", "odds")))
            u = american_to_decimal(_first(pc.get("underOdds"), dig(pc, "total", "under", "close", "odds")))
            add(f"totals|Over|{total}", "totals", f"Über {total}", o, book, "totals")
            add(f"totals|Under|{total}", "totals", f"Unter {total}", u, book, "totals")
        # Handicap (ESPN gibt die Linie aus Sicht des Heimteams an)
        spread = pc.get("spread")
        if spread is not None:
            try:
                sp = float(spread)
                hs = american_to_decimal(_first(dig(pc, "homeTeamOdds", "spreadOdds"),
                                                dig(pc, "pointSpread", "home", "close", "odds")))
                as_ = american_to_decimal(_first(dig(pc, "awayTeamOdds", "spreadOdds"),
                                                 dig(pc, "pointSpread", "away", "close", "odds")))
                add(f"spreads|{ev.home}|{sp:+g}", "spreads", f"{ev.home} {sp:+g}", hs, book, "spreads")
                add(f"spreads|{ev.away}|{-sp:+g}", "spreads", f"{ev.away} {-sp:+g}", as_, book, "spreads")
            except (TypeError, ValueError):
                pass
        for items in groups.values():
            if len(items) < 2:
                continue
            tot = sum(1 / p for _, p in items)
            for sid, p in items:
                fair_acc.setdefault(sid, []).append((1 / p) / tot)

    out = []
    for sid, e in sel.items():
        best, book = max(e["prices"], key=lambda x: x[0])
        fair = fair_acc.get(sid)
        out.append(Selection(sid, e["market"], e["label"], best, book,
                             sum(fair) / len(fair) if fair else None))
    return out


def odds_details(summ: dict) -> str:
    """Original-Text der Quoten (z. B. 'BOS -5.5') als Plausibilitätscheck für Claude."""
    parts = [f"{dig(pc, 'provider', 'name', default='?')}: {pc.get('details', '')} O/U {pc.get('overUnder', '?')}"
             for pc in summ.get("pickcenter") or []]
    return "; ".join(parts)


# ---------- Verletzte & Aufstellungen ----------
def team_news(summ: dict) -> str:
    lines = []
    for t in summ.get("injuries") or []:
        team = dig(t, "team", "displayName", default="?")
        for i in t.get("injuries") or []:
            name = dig(i, "athlete", "displayName", default="?")
            status = i.get("status") or dig(i, "type", "description", default="")
            detail = dig(i, "details", "type", default="") or i.get("shortComment", "")
            lines.append(f"Verletzung {team}: {name} – {status} {detail}".strip())
    for r in summ.get("rosters") or []:
        team = dig(r, "team", "displayName", default="?")
        starters = [dig(p, "athlete", "displayName") for p in r.get("roster") or [] if p.get("starter")]
        starters = [s for s in starters if s]
        if starters:
            lines.append(f"Offizielle Startelf {team} ({r.get('formation', '?')}): {', '.join(starters)}")
    return "\n".join(lines[:60])
