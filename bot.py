"""Wett-Analyse-Bot: postet automatisch X Minuten vor jedem Spiel eine Analyse."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

import config
import embeds
import espn
from analyzer import analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

LEAGUES = {lg.key: lg for lg in config.LEAGUES if lg.enabled}


class State:
    """Merkt sich, welche Spiele schon gepostet wurden (überlebt Neustarts)."""

    def __init__(self, path: str):
        self.path = path
        self.posted: dict[str, str] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.posted = json.load(f)

    def mark(self, event_id: str):
        self.posted[event_id] = datetime.now(timezone.utc).isoformat()
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # alte Einträge aufräumen
        self.posted = {k: v for k, v in self.posted.items() if datetime.fromisoformat(v) > cutoff}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.posted, f)


class WettBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.state = State(config.STATE_FILE)
        self.schedule: dict[str, espn.Event] = {}
        self.last_refresh: datetime | None = None
        self.session: aiohttp.ClientSession | None = None
        self.lock = asyncio.Semaphore(2)  # max. 2 Analysen parallel (Kosten/Rate-Limits)

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        await self.tree.sync()
        self.loop_check.start()

    async def on_ready(self):
        # Befehle direkt auf jedem Server registrieren (sofort sichtbar statt bis zu 1 h Wartezeit)
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        log.info("Bot online als %s auf %d Server(n)", self.user, len(self.guilds))

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

    # ---------- Spielplan ----------
    async def refresh_schedule(self):
        now = datetime.now(timezone.utc)
        new: dict[str, espn.Event] = {}
        for lg in LEAGUES.values():
            try:
                for ev in await espn.list_events(self.session, lg.key):
                    if lg.group_prefix and ev.group and not ev.group.startswith(lg.group_prefix):
                        continue
                    if now - timedelta(hours=3) < ev.kickoff < now + timedelta(days=2):
                        new[ev.id] = ev
            except Exception as exc:
                log.warning("Spielplan %s fehlgeschlagen: %s", lg.name, exc)
        self.schedule = new
        self.last_refresh = now
        log.info("Spielplan aktualisiert: %d Spiele in den nächsten 48 h", len(new))

    # ---------- Analyse ----------
    async def run_analysis(self, ev: espn.Event) -> discord.Embed:
        lg = LEAGUES[ev.league_key]
        async with self.lock:
            try:
                summ = await espn.summary(self.session, lg.key, ev.id)
            except Exception as exc:  # ohne ESPN-Daten recherchiert Claude alles selbst
                log.warning("ESPN-Summary fehlgeschlagen: %s", exc)
                summ = {}
            result = await analyze(ev, lg.name, lg.sport, lg.extra_markets,
                                   espn.build_selections(summ, ev), espn.team_news(summ),
                                   espn.odds_details(summ))
            return embeds.build(result, lg.sport)

    async def post(self, ev: espn.Event):
        lg = LEAGUES[ev.league_key]
        channel = self.get_channel(config.CHANNELS.get(lg.sport, 0))
        if channel is None:
            log.error("Kein Channel für %s konfiguriert", lg.sport)
            return
        try:
            embed = await self.run_analysis(ev)
            await channel.send(embed=embed)
            log.info("Gepostet: %s vs %s", ev.home, ev.away)
        except Exception as exc:
            log.exception("Analyse %s vs %s fehlgeschlagen: %s", ev.home, ev.away, exc)
        finally:
            self.state.mark(ev.id)  # nicht endlos wiederholen

    @tasks.loop(minutes=5)
    async def loop_check(self):
        now = datetime.now(timezone.utc)
        if not self.last_refresh or now - self.last_refresh > timedelta(hours=3):
            await self.refresh_schedule()
        if not config.AUTO_POST:
            return
        due = []
        for ev in self.schedule.values():
            lead = timedelta(minutes=LEAGUES[ev.league_key].lead_minutes)
            if ev.id not in self.state.posted and ev.kickoff - lead <= now < ev.kickoff:
                due.append(ev)
        for ev in sorted(due, key=lambda e: e.kickoff):
            asyncio.create_task(self.post(ev))
            self.state.mark(ev.id)  # sofort markieren, damit der nächste Loop nicht doppelt startet

    @loop_check.before_loop
    async def _wait(self):
        await self.wait_until_ready()


bot = WettBot()


def _find(query: str) -> espn.Event | None:
    q = query.lower()
    hits = [e for e in bot.schedule.values() if q in e.home.lower() or q in e.away.lower()]
    return min(hits, key=lambda e: e.kickoff) if hits else None


@bot.tree.command(name="analyse", description="Analyse für ein Spiel jetzt sofort erstellen")
@app_commands.describe(team="Teamname (oder Teil davon), z. B. 'Bayern' oder 'Lakers'")
async def analyse_cmd(interaction: discord.Interaction, team: str):
    await interaction.response.defer(thinking=True)
    if not bot.schedule:
        await bot.refresh_schedule()
    ev = _find(team)
    if not ev:
        await interaction.followup.send(f"Kein Spiel mit „{team}“ in den nächsten 48 Stunden gefunden.")
        return
    try:
        await interaction.followup.send(embed=await bot.run_analysis(ev))
    except Exception as exc:
        await interaction.followup.send(f"Analyse fehlgeschlagen: {exc}")


@bot.tree.command(name="spiele", description="Kommende Spiele, die analysiert werden")
async def spiele_cmd(interaction: discord.Interaction):
    if not bot.schedule:
        await bot.refresh_schedule()
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.TIMEZONE)
    upcoming = sorted(bot.schedule.values(), key=lambda e: e.kickoff)[:25]
    if not upcoming:
        await interaction.response.send_message("Keine Spiele in den nächsten 48 Stunden.")
        return
    lines = [f"`{e.kickoff.astimezone(tz):%a %H:%M}` {LEAGUES[e.league_key].name}: {e.home} – {e.away}"
             for e in upcoming]
    await interaction.response.send_message("\n".join(lines)[:2000])


if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)
