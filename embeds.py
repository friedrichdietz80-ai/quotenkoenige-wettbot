"""Discord-Embeds für die Analysen."""
from zoneinfo import ZoneInfo

import discord

import config
from analyzer import Analysis, Pick

COLORS = {"football": 0x2ECC71, "nfl": 0x013369, "nba": 0xC9082A}
EMOJI = {"football": "⚽", "nfl": "🏈", "nba": "🏀"}


def _pick_line(p: Pick) -> str:
    market = f" (Markt {p.selection.fair_prob:.0%})" if p.selection.fair_prob else ""
    check = " ⚠️ Quote aus Websuche, vor dem Tippen prüfen" if p.selection.from_search else ""
    return (f"**{p.selection.label}** @ **{p.selection.best_odds:.2f}** ({p.selection.best_book})\n"
            f"Unsere Wkeit {p.prob:.0%}{market} · EV **{p.ev:+.1%}** · Einsatz {p.stake_pct} %\n"
            f"_{p.reason}_{check}")


def build(a: Analysis, sport: str) -> discord.Embed:
    kickoff = a.event.kickoff.astimezone(ZoneInfo(config.TIMEZONE))
    e = discord.Embed(
        title=f"{EMOJI[sport]} {a.event.home} vs {a.event.away}",
        description=f"**{a.league_name}** · Anstoß {kickoff:%H:%M} Uhr\n\n{a.summary}"[:4000],
        color=COLORS[sport],
    )
    if a.favorite:
        e.add_field(name="🏆 Favorit", value=a.favorite[:1024], inline=False)
    if a.absences:
        e.add_field(name="🚑 Ausfälle / Fraglich", value="\n".join(f"• {x}" for x in a.absences)[:1024], inline=False)
    if a.main_tip:
        e.add_field(name="🎯 Unser Tipp", value=_pick_line(a.main_tip)[:1024], inline=False)
    elif a.tip_text:
        e.add_field(name="🎯 Unser Tipp", value=a.tip_text[:1024], inline=False)
    if a.value_picks:
        e.add_field(name="💎 Value-Wetten",
                    value="\n\n".join(_pick_line(p) for p in a.value_picks)[:1024], inline=False)
    else:
        e.add_field(name="💎 Value-Wetten", value="Kein klarer Value – Markt wirkt fair bepreist.", inline=False)
    if a.sources:
        e.add_field(name="Quellen", value="\n".join(a.sources)[:1024], inline=False)
    e.set_footer(text=f"Unsicherheit: {a.uncertainty} · Quoten: US-Buchmacher via ESPN, deine können abweichen · "
                      "Keine Gewinngarantie · 18+")
    return e
