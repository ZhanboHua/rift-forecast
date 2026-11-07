#!/usr/bin/env python3
"""Read a small, public view of Rift Forecast's latest snapshot."""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

SITE_URL = "https://aleahub.im/playground/rift-forecast/"
SNAPSHOT_URL = SITE_URL + "data/arena.json"
TRACKS = ("online", "offline")
METRICS = ("chips", "accuracy")
STANDING_FIELDS = (
    "model", "balance", "loans_taken", "net", "brier", "n_settled", "winner_hits",
    "n_directional", "n_neutral", "n_unknown", "n_abstain",
)
ATTEMPT_FIELDS = ("model", "phase", "date", "duration", "finished", "verified",
                  "exit", "accepted", "tokens", "estimated_usd")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def pick(row, fields):
    return {key: row[key] for key in fields if key in row and
            (row[key] is None or type(row[key]) in (str, int, float, bool))}


def standings(board):
    return {metric: [pick(row, STANDING_FIELDS) for row in board.get(metric, [])]
            for metric in METRICS}


def snapshot_view(data, tracks, include_stats=False):
    if data.get("schemaVersion") != 2:
        raise ValueError("Unsupported public snapshot schema")
    calendar = data["calendar"]
    board = data["leaderboard"]
    events = []
    for event in calendar["events"]:
        view = pick(event, ("id", "start", "referenceStart", "stage", "bestOf", "status"))
        for key, value_type in (("teams", str), ("score", int)):
            values = event.get(key)
            if values is None or (isinstance(values, list) and all(type(value) is value_type for value in values)):
                view[key] = values
        arena = event.get("arena", {})
        view["arena"] = {track: pick(arena[track], ("state", "date", "anchor"))
                         for track in tracks if track in arena}
        events.append(view)

    batches = {}
    for track in tracks:
        batch = data.get("batches", {}).get(track)
        if batch:
            batches[track] = {
                **pick(batch, ("anchor", "date", "state", "expected")),
                "models": [pick(row, ("model", "first", "recovery", "review", "deadline", "accepted"))
                           for row in batch.get("models", [])],
            }
    result = {
        "source": SNAPSHOT_URL,
        "site": SITE_URL,
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
        **pick(data, ("observedAt",)),
        "calendarObservedAt": pick(calendar, ("observedAt",)).get("observedAt"),
        "settledDaysByTrack": pick(board.get("settledDaysByTrack", {}), tracks),
        "events": events,
        "leaderboards": {track: standings(board["tracks"].get(track, {})) for track in tracks},
        "daily": {track: {day: standings(rows) for day, rows in board.get("daily", {}).get(track, {}).items()
                          if len(day) == 10 and day.replace("-", "").isdigit()} for track in tracks},
        "batches": batches,
    }
    if include_stats:
        runtime = data.get("runtime", {})
        result["runtime"] = {track: [pick(row, ATTEMPT_FIELDS) for row in runtime.get(track, [])]
                             for track in tracks}
    return result


def print_report(result):
    print(f"Snapshot: {result.get('observedAt')} | Calendar: {result['calendarObservedAt']}")
    print(f"Source: {SITE_URL}")
    for track, board in result["leaderboards"].items():
        print(f"\n------ {track.upper()} ------")
        batch = result["batches"].get(track, {})
        if batch:
            print(f"Batch: {batch.get('date')} · {batch.get('state')}")
        print(f"Settled days: {result['settledDaysByTrack'].get(track, 'unknown')}")
        for row in board["chips"][:5]:
            print(f"{row.get('model')}: chips={row.get('balance')} Brier={row.get('brier')} "
                  f"hits={row.get('winner_hits')}/{row.get('n_directional')} "
                  f"settled={row.get('n_settled')} neutral={row.get('n_neutral')} "
                  f"abstained={row.get('n_abstain')} unknown={row.get('n_unknown')}")
    ordered = sorted(result["events"], key=lambda event: event.get("start", ""))
    finished = [event for event in ordered if event.get("status") == "done"][-3:]
    upcoming = [event for event in ordered if event.get("status") in ("scheduled", "live")][:3]
    for label, events in (("LATEST FINISHED", finished), ("UPCOMING", upcoming)):
        print(f"\n------ {label} ------")
        for event in events:
            states = {track: row.get("state") for track, row in event["arena"].items()}
            print(f"{event.get('start')} {event.get('teams')} {event.get('status')} "
                  f"score={event.get('score')} arena={states}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=TRACKS, help="Select one arena track")
    parser.add_argument("--json", action="store_true", help="Print the filtered public view as JSON")
    parser.add_argument("--stats", action="store_true", help="Include public run attempts in JSON output")
    args = parser.parse_args()
    if args.stats and not args.json:
        parser.error("--stats requires --json")
    request = Request(SNAPSHOT_URL, headers={"User-Agent": "rift-forecast-status/1.1", "Cache-Control": "no-cache"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = snapshot_view(json.load(response), (args.track,) if args.track else TRACKS, args.stats)
    except HTTPError as exc:
        print(f"[ERROR] Public snapshot returned HTTP {exc.code}", file=sys.stderr)
        return 1
    except (OSError, URLError, ValueError, KeyError, TypeError, AttributeError):
        print("[ERROR] Public snapshot unavailable or incompatible. Check the public site.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
