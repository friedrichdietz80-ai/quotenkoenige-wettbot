"""Zentrale Konfiguration. Alles Geheime kommt aus der .env-Datei."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Schlüssel aus .env laden, sonst aus der ausgefüllten .env.example
load_dotenv(".env") or load_dotenv(".env.example")


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if isinstance(val, str):
        val = val.strip().strip('"').strip("'").strip()  # Leerzeichen/Zeilenumbrüche vom Einfügen entfernen
    if required and not val:
        raise RuntimeError(f"Umgebungsvariable {name} fehlt (siehe .env.example)")
    return val


DISCORD_TOKEN = _env("DISCORD_TOKEN", required=True)
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", required=True)

CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-5-5")
WEB_SEARCH_TOOL = _env("WEB_SEARCH_TOOL", "web_search_20250305")
WEB_SEARCH_MAX_USES = int(_env("WEB_SEARCH_MAX_USES", "5"))

MIN_EDGE = float(_env("MIN_EDGE", "0.04"))           # ab 4 % Expected Value = "Value"
MIN_PROB = float(_env("MIN_PROB", "0.20"))           # extreme Longshots ignorieren
KOMBI_MIN = float(_env("KOMBI_MIN", "1.20"))         # Quotenbereich für den Kombi-Pick
KOMBI_MAX = float(_env("KOMBI_MAX", "1.80"))

AUTO_POST = _env("AUTO_POST", "1") == "1"   # 0 = lokaler Bot postet nicht automatisch (macht GitHub)
TIMEZONE = _env("TIMEZONE", "Europe/Berlin")
STATE_FILE = _env("STATE_FILE", "state.json")


@dataclass
class League:
    key: str              # ESPN-Pfad, z. B. "soccer/ger.1"
    name: str
    sport: str            # "football" | "nfl" | "nba"
    lead_minutes: int     # wie viele Minuten vor Anpfiff posten
    extra_markets: str    # was Claude zusätzlich per Websuche checken soll
    enabled: bool = True


FOOTBALL_EXTRA = "Torschützen (jederzeit), beide Teams treffen, Über/Unter Tore, Handicap"
NFL_EXTRA = "Anytime TD, Passing/Rushing/Receiving Yards"
NBA_EXTRA = "Punkte, Rebounds, Assists, PRA der Schlüsselspieler"

LEAGUES: list[League] = [
    League("soccer/eng.1", "Premier League", "football", 60, FOOTBALL_EXTRA),
    League("soccer/ger.1", "Bundesliga", "football", 60, FOOTBALL_EXTRA),
    League("soccer/esp.1", "La Liga", "football", 60, FOOTBALL_EXTRA),
    League("soccer/ita.1", "Serie A", "football", 60, FOOTBALL_EXTRA),
    League("soccer/fra.1", "Ligue 1", "football", 60, FOOTBALL_EXTRA),
    League("soccer/uefa.champions", "Champions League", "football", 60, FOOTBALL_EXTRA),
    League("soccer/uefa.nations", "UEFA Nations League", "football", 60, FOOTBALL_EXTRA),
    League("soccer/tur.1", "Süper Lig", "football", 60, FOOTBALL_EXTRA, enabled=False),
    # NFL: Inactives kommen 90 Min vor Kickoff -> 75 Min vorher posten
    League("football/nfl", "NFL", "nfl", 75, NFL_EXTRA),
    # NBA: Injury Report wird bis kurz vor Tip-off aktualisiert
    League("basketball/nba", "NBA", "nba", 60, NBA_EXTRA),
]

# In welchen Channel postet welche Sportart
CHANNELS = {
    "football": int(_env("CHANNEL_FOOTBALL") or "1570153570127315014"),
    "nfl": int(_env("CHANNEL_NFL") or "1570153630328426526"),
    "nba": int(_env("CHANNEL_NBA") or "1570153647940173934"),
}
