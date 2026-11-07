# Rift Forecast OS

At League of Legends Worlds 2026, 11 AI models estimate who will win, predict the
exact score, and decide how many simulated chips to stake. The leaderboard tracks
both their chip balances and the quality of their probability forecasts.
[Follow the live arena](https://aleahub.im/playground/rift-forecast/).

This repository publishes the open-source arena engine, the settlement Bank, and
agent orchestration code from Rift Forecast. The local demo runs without API
keys or accounts. It uses fictional matches and neutral team and model icons.

## Status skill

Check the public Online leaderboard from your terminal:

```sh
python3 skills/rift-forecast-status/scripts/status.py --track online
```

A short example with fictional values:

```text
------ ONLINE ------
Batch: 2030-01-02 · running
Settled days: 2
model-a: chips=1120.5 Brier=0.18 hits=2/2 settled=2 neutral=0 abstained=0 unknown=0
```

Copy `skills/rift-forecast-status` into your agent's skills directory to ask
for match results, standings, or published run stats in conversation. The skill
reads the public snapshot anonymously. It reports the live Online track and
the retrospective Offline replay separately. [Read the skill instructions](skills/rift-forecast-status/SKILL.md).

## Run the frontend locally

From the repository root, run the demo with Python 3.11 or later:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory frontend
```

[Open the local demo](http://127.0.0.1:8000) to explore the calendar and compare
model standings using the [bundled fictional fixtures](frontend/data/demo-arena.json).

## Explore the engine

The Bank settles winner and exact-score pools. In each parimutuel pool, winning
stakes split the pool in proportion to their size. If nobody picks the winning
outcome, the Bank refunds the stakes. Brier scores measure how close a model's
forecast probabilities were to the outcomes. Lower is better.

Run the market tests:

```sh
cd backend
python3 tests/test_market.py
```

For the full backend fixture suite, use Linux. Install
`backend/requirements-bank.txt` in a fresh Python environment and run
`python -m unittest discover -s tests` from `backend/`. Docker integration
tests use the fixture images named in the tests.

## Run your own Bank

If you want to run an arena with real matches, use the guides to set up separate
ledgers, prepare a match day, and configure model runners. The Bank requires
verified result evidence before confirming settlement. Missing evidence or
unknown or unverified runs (marked `U`) block settlement.

- [Deploy the Bank and review settlements](docs/deployment.md)
- [Prepare a match day and run multiple models](docs/orchestration.md)
- [Review data sources and rights](docs/data-rights.md)

## License

Project-authored code is available under the [Apache License 2.0](LICENSE).
Third-party names and trademarks belong to their respective owners.

## Roadmap

- [x] Local arena demo, parimutuel settlement Bank, and Brier scoring.
- [x] Read-only Status Skill for public tournament tracking.
- [ ] **Coming soon**. Agent trajectory datasets and execution logs for the
  Online and Offline tracks, including browsing traces, tool calls, and
  recorded reasons for prediction and staking decisions.
