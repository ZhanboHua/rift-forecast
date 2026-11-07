"""Fictional stdio adapter for orchestration tests."""
import json
import sys

task = json.load(sys.stdin)
if not task['materials']:
    sys.exit(1)
predictions = []
for match in task['matches']:
    wins = match['best_of'] // 2 + 1
    options = [f'{wins}-{i}' for i in range(wins)] + [f'{i}-{wins}' for i in range(wins - 1, -1, -1)]
    predictions.append({'match_id': match['match_id'], 'p_team_a_win': 0.6,
                        'winner_bet': {'team': match['team_a'], 'stake': 0},
                        'score_bets': {score: 0 for score in options},
                        'rationale': 'Fictional fixture', 'sources': [], 'take_loan': False})
print(json.dumps(predictions))
