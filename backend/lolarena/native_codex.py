"""Execute a Codex CLI prediction turn and verify its native session events."""
import json
import re
import signal
import subprocess
import threading


def invoke(command, model, task, environment, workspace, timeout, session_id=None, *, on_start=None, on_session=None, on_end=None):
    if not isinstance(model, str) or not model or model.startswith('-'):
        raise ValueError('A model name is required')
    if session_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', session_id):
        raise ValueError('Invalid native session ID')
    argv = [*command, 'exec']
    if session_id:
        argv.append('resume')
    argv += ['--json', '--skip-git-repo-check', '-m', model]
    if session_id:
        argv.append(session_id)
    argv.append('-')
    prompt = ('Return only a JSON array with exactly one prediction object per match, without Markdown. '
              'Each object must have: match_id (string); p_team_a_win (number from 0 to 1); '
              'winner_bet (object with team equal to the actual team name from the fixture and stake as a nonnegative number; '
              'if team_a is AAA and team_b is BBB, use AAA or BBB, not the words team_a or team_b); '
              'score_bets (object mapping every legal final score string to a nonnegative numeric stake, '
              'with team_a wins first, for example {\"1-0\": 0, \"0-1\": 0} for best_of 1); '
              'rationale (string); sources (array of URL strings, or an empty array); and take_loan (boolean). '
              'Stakes use simulated chips and must not exceed the available balance. '
              'Use the supplied materials and do not use web tools. Input: ' + json.dumps(task, ensure_ascii=False))
    child = subprocess.Popen(argv, cwd=workspace, env=environment, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    if on_start:
        try:
            on_start(child.pid)
        except Exception:
            import os
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
    lines = []
    errors = []
    def read_stream():
        try:
            size = 0
            for line in child.stdout:
                size += len(line)
                if size > 8_000_000:
                    errors.append(ValueError('Native output exceeded limit'))
                    import os
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return
                lines.append(line)
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict) and event.get('type') == 'thread.started':
                    value = event.get('thread_id')
                    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value) and on_session:
                        on_session(value)
        except Exception as exc:
            errors.append(exc)
    def write_prompt():
        try:
            child.stdin.write(prompt.encode())
            child.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    reader = threading.Thread(target=read_stream, daemon=True)
    writer = threading.Thread(target=write_prompt, daemon=True)
    reader.start()
    writer.start()
    timed_out = False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            import os
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
    finally:
        writer.join(timeout=5)
        reader.join(timeout=5)
        if on_end:
            on_end(child.pid, child.returncode)
    if not reader.is_alive():
        child.stdout.close()
    if reader.is_alive() or writer.is_alive() or errors:
        return {'status': 'U', 'session_id': None, 'reason': 'event_capture_failed'}
    raw = b''.join(lines)
    ids, messages, finished = [], [], False
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'thread.started' and isinstance(event.get('thread_id'), str):
            value = event['thread_id']
            if re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value) and value not in ids:
                ids.append(value)
        if event.get('type') == 'item.completed' and isinstance(event.get('item'), dict):
            item = event['item']
            if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                messages.append(item['text'])
        if event.get('type') == 'turn.completed':
            finished = True
    actual = ids[0] if len(ids) == 1 else None
    if session_id and actual != session_id:
        return {'status': 'U', 'session_id': None, 'reason': 'session_mismatch'}
    if not actual or timed_out or child.returncode != 0 or not finished or not messages:
        return {'status': 'U', 'session_id': actual, 'reason': 'native_turn_incomplete', 'exit_code': child.returncode}
    try:
        predictions = json.loads(messages[-1])
    except ValueError:
        return {'status': 'U', 'session_id': actual, 'reason': 'invalid_native_output'}
    if not isinstance(predictions, list):
        return {'status': 'U', 'session_id': actual, 'reason': 'invalid_native_output'}
    return {'status': 'done', 'session_id': actual, 'predictions': predictions}
