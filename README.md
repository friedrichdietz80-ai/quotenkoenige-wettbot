# Quotenkönige Wett-Bot ⚽🏈🏀

Analysiert Spiele (Top-5-Ligen, Champions League, NFL, NBA) kurz vor Anpfiff mit Claude und postet Favorit, Ausfälle, Tipp und Value-Wetten in Discord.

- **GitHub Actions** (`.github/workflows/tipps.yml`) läuft alle ~10 Minuten und postet fällige Analysen – auch wenn kein PC an ist.
- **Lokal** (`bot.py`, optional) stellt die Befehle `/analyse` und `/spiele` bereit.

Benötigte Repository-Secrets: `DISCORD_TOKEN`, `ANTHROPIC_API_KEY`.
Ligen, Vorlaufzeit und Channels: `config.py`.

Keine Gewinngarantie · 18+
