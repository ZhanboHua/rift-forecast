"""Fictional Codex JSONL protocol fixture."""
import json
import os
import sys
import time
from pathlib import Path

prompt = sys.stdin.read()
resume = 'resume' in sys.argv
if os.environ.get('NO_SESSION') == '1':
    sys.exit(1)
session = 'different-session' if resume and os.environ.get('MISMATCH') == '1' else 'fixture-session-123'
print(json.dumps({'type': 'thread.started', 'thread_id': session}), flush=True)
if not resume:
    if os.environ.get('HANG_FIRST') == '1':
        time.sleep(60)
    if os.environ.get('TIMEOUT_FIRST') == '1':
        time.sleep(5)
    sys.exit(1)
if session not in sys.argv:
    sys.exit(2)
task = json.loads(prompt.split('Input: ', 1)[1])
predictions = []
for match in task['matches']:
    wins = match['best_of'] // 2 + 1
    options = [f'{wins}-{i}' for i in range(wins)] + [f'{i}-{wins}' for i in range(wins - 1, -1, -1)]
    predictions.append({'match_id': match['match_id'], 'p_team_a_win': .6,
                        'winner_bet': {'team': match['team_a'], 'stake': 0},
                        'score_bets': {k: 0 for k in options}, 'rationale': 'fictional test',
                        'sources': [], 'take_loan': False})
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(predictions)}}))
print(json.dumps({'type': 'turn.completed'}))
