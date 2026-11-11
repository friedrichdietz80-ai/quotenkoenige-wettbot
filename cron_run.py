"""Einmal-Lauf für GitHub Actions: prüft fällige Spiele, analysiert sie und postet per Discord-API.
Läuft alle ~10 Minuten, braucht keinen dauerhaft laufenden Bot."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp

import config
import embeds
import espn
from analyzer import analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cron")

LEAGUES = {lg.key: lg for lg in config.LEAGUES if lg.enabled}
STATE = config.STATE_FILE
DISCORD_API = "https://discord.com/api/v10"


def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    state = {k: v for k, v in state.items() if datetime.fromisoformat(v) > cutoff}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=0)


async def post_embed(session: aiohttp.ClientSession, channel_id: int, embed_dict: dict):
    headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
    async with session.post(f"{DISCORD_API}/channels/{channel_id}/messages",
                            json={"embeds": [embed_dict]}, headers=headers) as r:
        if r.status >= 300:
            raise RuntimeError(f"Discord {r.status}: {await r.text()}")


async def handle(session, ev: espn.Event, state: dict):
    lg = LEAGUES[ev.league_key]
    state[ev.id] = datetime.now(timezone.utc).isoformat()  # auch bei Fehler nicht endlos wiederholen
    try:
        try:
            summ = await espn.summary(session, lg.key, ev.id)
        except Exception as exc:
            log.warning("ESPN-Summary fehlgeschlagen: %s", exc)
            summ = {}
        result = await analyze(ev, lg.name, lg.sport, lg.extra_markets,
                               espn.build_selections(summ, ev), espn.team_news(summ),
                               espn.odds_details(summ))
        await post_embed(session, config.CHANNELS[lg.sport], embeds.build(result, lg.sport).to_dict())
        log.info("Gepostet: %s vs %s", ev.home, ev.away)
    except Exception:
        log.exception("Fehler bei %s vs %s", ev.home, ev.away)


async def main():
    state = load_state()
    now = datetime.now(timezone.utc)
    due: list[espn.Event] = []
    async with aiohttp.ClientSession() as session:
        for lg in LEAGUES.values():
            try:
                events = await espn.list_events(session, lg.key)
            except Exception as exc:
                log.warning("Spielplan %s fehlgeschlagen: %s", lg.name, exc)
                continue
            lead = timedelta(minutes=lg.lead_minutes)
            for ev in events:
                if ev.id not in state and ev.kickoff - lead <= now < ev.kickoff:
                    due.append(ev)
        log.info("%d Spiel(e) fällig", len(due))
        sem = asyncio.Semaphore(2)

        async def run(ev):
            async with sem:
                await handle(session, ev, state)

        await asyncio.gather(*(run(ev) for ev in sorted(due, key=lambda e: e.kickoff)))
    save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
