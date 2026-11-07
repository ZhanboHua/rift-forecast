"""Normalize operator-supplied Riot and OP.GG match snapshots without fetching."""
import argparse
import json
import re
from pathlib import Path


def riot_matches(raw):
    decoder = json.JSONDecoder()
    result = []
    for match in re.finditer(r'\{"__typename":"EventMatch","id":', raw):
        event, _ = decoder.raw_decode(raw, match.start())
        teams = event.get('matchTeams', [])
        if len(teams) != 2:
            raise ValueError('Riot event must have exactly two teams')
        best_of = event['match']['strategy']['count']
        state = event['state']
        status = {'completed': 'finished', 'inProgress': 'live', 'unstarted': 'scheduled'}.get(state)
        if status is None:
            raise ValueError('Unknown Riot match status')
        result.append(normalize(event['id'], event['startTime'], best_of, [t['code'] for t in teams],
                                status, [(t.get('result') or {}).get('gameWins') for t in teams]))
    if not result:
        raise ValueError('No Riot events found in snapshot')
    return result


def opgg_matches(raw):
    payload = json.loads(raw)
    matches = payload['data']['matchesBySerie']
    if not isinstance(matches, list) or not matches:
        raise ValueError('No OP.GG matches found')
    result = []
    for item in matches:
        status = {'finished': 'finished', 'inProgress': 'live', 'scheduled': 'scheduled', 'not_started': 'scheduled'}.get(item['status'])
        if status is None:
            raise ValueError('Unknown OP.GG match status')
        result.append(normalize(item['id'], item['scheduledAt'], item['numberOfGames'],
                                [(item.get('homeTeam') or {}).get('acronym') or 'TBD', (item.get('awayTeam') or {}).get('acronym') or 'TBD'],
                                status, [item.get('homeScore'), item.get('awayScore')]))
    return result


def normalize(match_id, start, best_of, teams, status, score):
    if not isinstance(match_id, str) or not match_id or not isinstance(start, str) or type(best_of) is not int or best_of not in (1, 3, 5):
        raise ValueError('Invalid match identity or format')
    if len(teams) != 2 or any(not isinstance(t, str) or not re.fullmatch(r'[A-Z0-9]{1,12}', t) for t in teams) or (teams[0] == teams[1] and teams[0] != 'TBD'):
        raise ValueError('Invalid teams')
    if status == 'finished':
        wins = best_of // 2 + 1
        if len(score) != 2 or any(type(x) is not int or not 0 <= x <= wins for x in score) or max(score) != wins or min(score) >= wins:
            raise ValueError('Invalid completed match score')
    else:
        score = None
    return {'match_id': match_id, 'start': start, 'best_of': best_of, 'teams': teams,
            'status': status, 'score': score}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', choices=('riot', 'opgg'))
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    text = args.snapshot.read_text(encoding='utf-8')
    matches = riot_matches(text) if args.source == 'riot' else opgg_matches(text)
    ids = [row['match_id'] for row in matches]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate match ID')
    print(json.dumps(matches, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
