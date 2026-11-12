"""Claude recherchiert (Websuche) und schätzt Wahrscheinlichkeiten – der Code rechnet den Value."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic

import config
from espn import Event, Selection

log = logging.getLogger("analyzer")
client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

SPORT_HINTS = {
    "football": ("Fußball. Prüfe Verletzte, Gesperrte, voraussichtliche/offizielle Aufstellungen "
                 "(z. B. FotMob, Kicker, Transfermarkt, Vereinsseiten), Form der letzten 5 Spiele, "
                 "Heim/Auswärts, xG, Rotation wegen Europapokal, Motivation (Tabelle)."),
    "nfl": ("NFL. Prüfe den finalen Injury Report (Out/Doubtful/Questionable) und Inactives, "
            "QB-Situation, O-Line, Wetter im Stadion, Rest/Reisen, ATS-Form."),
    "nba": ("NBA. Prüfe den aktuellsten offiziellen Injury Report (Out/Questionable/Probable), "
            "Back-to-Backs, Load Management, Pace, Offensive/Defensive Rating, letzte 10 Spiele."),
}

SYSTEM = """Du bist ein nüchterner, quantitativer Sportwetten-Analyst. Du recherchierst aktuelle
Fakten per Websuche und schätzt dann EIGENE Wahrscheinlichkeiten – unabhängig von den Quoten.
Sei ehrlich: Wenn der Markt effizient wirkt, sag das. Übertreibe keine Sicherheit.
Erfinde niemals Quoten: Gib bei Zusatzwetten nur Quoten an, die du in einer Quelle gesehen hast.
Antworte am Ende AUSSCHLIESSLICH mit einem JSON-Objekt in einem ```json Block."""

JSON_SPEC = """Format:
```json
{
  "zusammenfassung": "3-5 Sätze: Lage, Form, wichtigste Ausfälle und warum",
  "favorit": "Teamname + kurze Begründung",
  "ausfaelle": ["Spieler (Team) – Status", "..."],
  "schaetzungen": [
    {"id": "<ID aus der Liste>", "wahrscheinlichkeit": 0.00, "begruendung": "1 Satz"}
  ],
  "zusatz_wetten": [
    {"auswahl": "z. B. Harry Kane trifft", "quote": 0.00, "buchmacher": "Quelle der Quote",
     "wahrscheinlichkeit": 0.00, "begruendung": "1 Satz"}
  ],
  "kombi_pick": {"auswahl": "sichere Wette mit Quote 1.20-1.80, z. B. Doppelte Chance, Über 1.5 Tore, Sieg, Handicap",
                 "quote": 0.00, "buchmacher": "Quelle der Quote", "wahrscheinlichkeit": 0.00,
                 "begruendung": "1 Satz"},
  "tipp": {"id": "<ID exakt aus der Liste kopiert ODER exakter 'auswahl'-Text einer zusatz_wette>", "begruendung": "1-2 Sätze"},
  "unsicherheit": "niedrig | mittel | hoch"
}
```
Gib für JEDE ID aus der Quotenliste eine Schätzung ab. Bei "zusatz_wetten" 2-4 Spieler-/Zusatzmärkte,
deren aktuelle Dezimalquote du per Websuche gefunden hast (bevorzugt deutsche Buchmacher oder
Vergleichsseiten wie Oddschecker). Fehlt die Quotenliste, liefere die Hauptmärkte (Sieg/Remis,
Über/Unter) ebenfalls als zusatz_wetten mit gefundenen Quoten. Wahrscheinlichkeiten zwischen 0 und 1.
"kombi_pick": die Wette mit Quote zwischen 1.20 und 1.80, die du für am wahrscheinlichsten hältst
(für Kombiwetten). Darf auch eine ID aus der Liste sein (dann "auswahl" = ID). Nur echte, gefundene Quoten."""


@dataclass
class Pick:
    selection: Selection
    prob: float
    ev: float          # Expected Value pro 1 € Einsatz
    reason: str

    @property
    def stake_pct(self) -> float:
        """Empfohlener Einsatz in % der Bankroll: Achtel-Kelly, gedeckelt auf 2 %."""
        b = self.selection.best_odds - 1
        kelly = (b * self.prob - (1 - self.prob)) / b if b > 0 else 0
        return round(max(0.0, min(2.0, kelly / 8 * 100)), 1)


@dataclass
class Analysis:
    event: Event
    league_name: str
    summary: str
    favorite: str
    absences: list[str]
    value_picks: list[Pick]
    main_tip: Pick | None
    kombi: Pick | None
    uncertainty: str
    sources: list[str]
    tip_text: str = ""        # Tipp als Text, falls er keiner Quote zugeordnet werden kann


def _odds_table(selections: list[Selection]) -> str:
    if not selections:
        return "(ESPN liefert für dieses Spiel keine Quoten – recherchiere sie selbst.)"
    rows = []
    for s in selections:
        fair = f"{s.fair_prob:.1%}" if s.fair_prob else "–"
        rows.append(f"{s.id} | {s.label} | Quote {s.best_odds:.2f} ({s.best_book}) | Markt-Wkeit {fair}")
    return "\n".join(rows)


def _extract_json(text: str) -> dict:
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # z. B. unmaskierte Anführungszeichen in Begründungen -> reparieren
        from json_repair import repair_json
        data = repair_json(raw, return_objects=True)
        if not isinstance(data, dict):
            raise
        return data


async def _ask_claude(prompt: str) -> tuple[str, list[str]]:
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": config.WEB_SEARCH_TOOL, "name": "web_search", "max_uses": config.WEB_SEARCH_MAX_USES}]
    sources: list[str] = []
    for _ in range(4):  # pause_turn kann bei langen Suchen auftreten
        resp = await client.messages.create(model=config.CLAUDE_MODEL, max_tokens=4000,
                                            system=SYSTEM, tools=tools, messages=messages)
        for block in resp.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):
                sources += [r.url for r in block.content if getattr(r, "url", None)]
        if resp.stop_reason != "pause_turn":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text, list(dict.fromkeys(sources))
        messages.append({"role": "assistant", "content": resp.content})
    raise RuntimeError("Claude hat die Analyse nicht abgeschlossen")


def _clean(text) -> str:
    """Entfernt Zitier-Markup wie <cite index="1-2">…</cite> aus Claudes Text."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"</cite>\)?", "", text)
    text = re.sub(r"\(?<?cite\b[^>]*>", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _book(name) -> str:
    """'TonyBet (−105 laut Squawka, ...)' -> 'TonyBet'"""
    name = str(name or "Websuche")
    return re.split(r"\s*[(\[,;]", name)[0].strip()[:30] or "Websuche"


def _prob(val) -> float | None:
    try:
        p = float(val)
    except (TypeError, ValueError):
        return None
    return p if 0 < p < 1 else None


def _norm(x: str) -> str:
    x = x.lower().removeprefix("extra|")
    x = re.sub(r"\bover\b", "über", x)
    x = re.sub(r"\bunder\b", "unter", x)
    return re.sub(r"[^a-z0-9äöüß.+-]+", " ", x).strip()


def _match_tip(tip_id: str, picks: dict) -> "Pick | None":
    """Ordnet den Tipp auch bei leicht abweichender Schreibweise zu."""
    if not tip_id:
        return None
    if tip_id in picks:
        return picks[tip_id]
    t = _norm(tip_id)
    for key, pk in picks.items():
        if t in (_norm(key), _norm(pk.selection.label), _norm(pk.selection.id)):
            return pk
    for key, pk in picks.items():
        lab = _norm(pk.selection.label)
        if t and lab and (t in lab or lab in t):
            return pk
    return None


async def analyze(event: Event, league_name: str, sport: str, extra_markets: str,
                  selections: list[Selection], team_news: str, odds_text: str) -> Analysis:
    tz = ZoneInfo(config.TIMEZONE)
    kickoff = event.kickoff.astimezone(tz).strftime("%d.%m.%Y %H:%M")
    now_local = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    prompt = f"""Spiel: {event.home} (Heim) vs {event.away} (Auswärts)
Wettbewerb: {league_name} | Anstoß: {kickoff} ({config.TIMEZONE})
Aktuelle Zeit: {now_local} – das Spiel hat NOCH NICHT begonnen. Suche nicht nach Ergebnissen.
Fokus: {SPORT_HINTS[sport]}

Daten von ESPN (Verletzte/Aufstellungen):
{team_news or 'Keine – recherchiere selbst.'}

Quoten von ESPN (US-Buchmacher; ID | Auswahl | Dezimalquote | margenbereinigte Markt-Wkeit):
{_odds_table(selections)}
Original-Quotentext: {odds_text or '–'}

Zusatzmärkte, die du per Websuche prüfen sollst: {extra_markets}

Recherchiere jetzt die aktuellen Infos (heute!) und schätze danach deine Wahrscheinlichkeiten.
{JSON_SPEC}"""

    text, sources = await _ask_claude(prompt)
    data = _extract_json(_clean(text))

    picks: dict[str, Pick] = {}
    by_id = {s.id: s for s in selections}
    for est in data.get("schaetzungen", []):
        s, p = by_id.get(est.get("id")), _prob(est.get("wahrscheinlichkeit"))
        if s and p:
            picks[s.id] = Pick(s, p, p * s.best_odds - 1, est.get("begruendung", ""))

    for z in data.get("zusatz_wetten", []):
        p = _prob(z.get("wahrscheinlichkeit"))
        try:
            q = float(z.get("quote"))
        except (TypeError, ValueError):
            continue
        label = str(z.get("auswahl", "")).strip()
        if not p or not label or not 1.01 < q < 100:
            continue
        s = Selection(f"extra|{label}", "extra", label, q, _book(z.get("buchmacher")),
                      None, from_search=True)
        picks[label] = Pick(s, p, p * q - 1, z.get("begruendung", ""))

    value = sorted((pk for pk in picks.values() if pk.ev >= config.MIN_EDGE and pk.prob >= config.MIN_PROB),
                   key=lambda pk: pk.ev, reverse=True)[:4]

    tip_data = data.get("tipp") or {}
    if isinstance(tip_data, str):
        tip_data = {"id": tip_data}
    main_tip = _match_tip(str(tip_data.get("id") or tip_data.get("auswahl") or ""), picks)
    if main_tip is None and picks:
        # Fallback: Wette mit dem höchsten Erwartungswert
        main_tip = max(picks.values(), key=lambda pk: pk.ev)
    if main_tip and tip_data.get("begruendung"):
        main_tip.reason = tip_data["begruendung"]
    if main_tip:
        value = [pk for pk in value if pk is not main_tip]

    # Kombi-Pick: Quote 1.20-1.80, möglichst hohe Wahrscheinlichkeit, kein deutlich negativer EV
    kp = data.get("kombi_pick") or {}
    if isinstance(kp, dict) and kp.get("auswahl"):
        found = _match_tip(str(kp["auswahl"]), picks)
        if found is None:
            p, q = _prob(kp.get("wahrscheinlichkeit")), None
            try:
                q = float(kp.get("quote"))
            except (TypeError, ValueError):
                pass
            if p and q and q > 1:
                label = _clean(str(kp["auswahl"]))
                found = Pick(Selection(f"extra|{label}", "extra", label, q, _book(kp.get("buchmacher")),
                                       None, from_search=True), p, p * q - 1, kp.get("begruendung", ""))
        elif kp.get("begruendung"):
            found = Pick(found.selection, found.prob, found.ev, kp["begruendung"])
        claude_kombi = found
    else:
        claude_kombi = None
    in_range = [pk for pk in picks.values()
                if config.KOMBI_MIN <= pk.selection.best_odds <= config.KOMBI_MAX and pk.ev > -0.03]
    if claude_kombi and config.KOMBI_MIN <= claude_kombi.selection.best_odds <= config.KOMBI_MAX \
            and claude_kombi.ev > -0.03:
        kombi = claude_kombi
    else:
        kombi = max(in_range, key=lambda pk: (pk.prob, pk.ev)) if in_range else None
    tip_text = ""
    if main_tip is None and (tip_data.get("id") or tip_data.get("begruendung")):
        tip_text = f"{tip_data.get('id', '')} – {tip_data.get('begruendung', '')}".strip(" –")

    return Analysis(event, league_name, data.get("zusammenfassung", ""), data.get("favorit", ""),
                    data.get("ausfaelle", [])[:10], value, main_tip, kombi,
                    data.get("unsicherheit", "mittel"), sources[:5], tip_text)
