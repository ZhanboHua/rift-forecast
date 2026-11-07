"""Example prediction adapter for an OpenAI-compatible chat endpoint."""
import json
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    task = json.load(sys.stdin)
    endpoint = os.environ['MODEL_API_URL']
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Use HTTPS or a loopback test endpoint')
    if parsed.username or parsed.password:
        raise ValueError('Credentials in URLs are not supported')
    prompt = ('Return only a JSON array with one prediction for each match in the input. '
              'Each prediction must contain match_id, p_team_a_win, winner_bet {team, stake}, '
              'score_bets with every legal final score using the first team first, rationale, sources, '
              'and take_loan. Use simulated chips only; total stakes across matches must not exceed the cap. '
              'Do not invent sources. Input: ' + json.dumps(task, ensure_ascii=False))
    body = json.dumps({'model': os.environ['MODEL_NAME'], 'messages': [{'role': 'user', 'content': prompt}]}).encode()
    request = Request(endpoint, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['MODEL_API_KEY']})
    with urlopen(request, timeout=120) as response:
        result = json.loads(response.read(2_000_001))
    predictions = json.loads(result['choices'][0]['message']['content'])
    if not isinstance(predictions, list):
        raise ValueError('Expected a prediction array')
    print(json.dumps(predictions, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('[ERROR] Model adapter failed', file=sys.stderr)
        sys.exit(1)
