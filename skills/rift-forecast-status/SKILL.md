---
name: rift-forecast-status
description: Read public Rift Forecast schedules, match results, model standings, run statistics, and aggregate HUMAN results. Use for questions about the public arena, its match-day data, or how to find a published model snapshot.
---

# Rift Forecast status

Run `python3 <skill-directory>/scripts/status.py` for a compact report. Use `--track online` or `--track offline` to focus on one track. Add `--json` for filtered calendar events, batch progress, and cumulative and dated standings. Use `--json --stats` for published run attempts, tokens, duration, and estimated USD cost. The helper makes anonymous reads of the public data snapshot.

Choose the public page that matches the question:

- [Calendar](https://aleahub.im/playground/rift-forecast/#calendar) for match times and results.
- [Online leaderboard](https://aleahub.im/playground/rift-forecast/#leaderboard?track=online) or [offline replay](https://aleahub.im/playground/rift-forecast/#leaderboard?track=offline) for settled chips and Brier. Lower Brier is better. Keep directional hits, neutral outcomes, abstentions, and unknown outcomes distinct.
- [Stats](https://aleahub.im/playground/rift-forecast/#stats) for attempts, token coverage, and estimated cost. Attempts can include retries. An unavailable cost or token count is unknown.
- [Snapshots](https://aleahub.im/playground/rift-forecast/#snapshots) for published screenshots and daily summaries. Share links or a brief summary of visible content.
- [HUMAN details](https://aleahub.im/playground/rift-forecast/#model?track=online&id=human) for aggregate daily results. HUMAN starts at 1,000 simulated chips and adds average daily net P/L after Day 1. Use aggregate figures only.

Report the data snapshot and calendar observation timestamps with timezone. Match results, batch progress, and arena settlement are separate. A sealed batch is not settled. Never infer unpublished picks, missing results, or current activity from stale observations. Keep live Online and retrospective Offline separate. All chips are simulated.

Use anonymous public reads only. Do not sign in, place or edit bets, use administrative or authenticated interfaces, or inspect private infrastructure. Do not include account identifiers, personal bet history, raw screenshots, or unpublished content in the report.
