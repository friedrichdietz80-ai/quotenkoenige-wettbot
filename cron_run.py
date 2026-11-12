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
STATUS_FILE = "status.json"
errors: list[str] = []


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
        state[ev.id] = datetime.now(timezone.utc).isoformat()  # nur bei Erfolg merken -> Fehler werden wiederholt
        log.info("Gepostet: %s vs %s", ev.home, ev.away)
    except Exception as exc:
        log.exception("Fehler bei %s vs %s", ev.home, ev.away)
        errors.append(f"{ev.home} vs {ev.away}: {type(exc).__name__}: {str(exc)[:300]}")


async def check_keys(session: aiohttp.ClientSession) -> dict:
    """Prüft ohne Kosten, ob die beiden Secrets gültig sind (Werte werden nie ausgegeben)."""
    res = {}
    tok = config.DISCORD_TOKEN or ""
    key = config.ANTHROPIC_API_KEY or ""
    res["discord_token_laenge"] = len(tok)
    res["anthropic_key_laenge"] = len(key)
    res["anthropic_key_beginnt_mit_sk_ant"] = key.startswith("sk-ant-")
    res["leerzeichen_oder_anfuehrungszeichen"] = any(c in tok + key for c in ' "\'\n')
    try:
        async with session.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bot {tok}"}) as r:
            res["discord"] = r.status
    except Exception as exc:
        res["discord"] = str(exc)[:200]
    try:
        async with session.get("https://api.anthropic.com/v1/models",
                               headers={"x-api-key": key, "anthropic-version": "2023-06-01"}) as r:
            res["anthropic"] = r.status
            if r.status != 200:
                res["anthropic_fehler"] = (await r.text())[:300]
    except Exception as exc:
        res["anthropic"] = str(exc)[:200]
    return res


async def main():
    state = load_state()
    now = datetime.now(timezone.utc)
    due: list[espn.Event] = []
    async with aiohttp.ClientSession() as session:
        keys = await check_keys(session)
        log.info("Schlüssel-Check: %s", keys)
        for lg in LEAGUES.values():
            try:
                events = await espn.list_events(session, lg.key)
            except Exception as exc:
                log.warning("Spielplan %s fehlgeschlagen: %s", lg.name, exc)
                continue
            lead = timedelta(minutes=lg.lead_minutes)
            for ev in events:
                if lg.group_prefix and ev.group and not ev.group.startswith(lg.group_prefix):
                    continue
                if ev.id not in state and ev.kickoff - lead <= now < ev.kickoff:
                    due.append(ev)
        log.info("%d Spiel(e) fällig", len(due))
        sem = asyncio.Semaphore(3)

        async def run(ev):
            async with sem:
                await handle(session, ev, state)

        await asyncio.gather(*(run(ev) for ev in sorted(due, key=lambda e: e.kickoff)))
    save_state(state)
    old = {}
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            old = json.load(f)
    status = {"letzter_lauf": now.isoformat(timespec="minutes"), "schluessel": keys,
              "faellig": len(due), "fehler": errors}
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
