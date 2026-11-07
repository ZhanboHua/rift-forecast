"""Loopback-only authenticated control API for an offline historical ledger."""

import argparse
import hmac
import json
import sqlite3
import stat
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .historical_controller import HistoricalController


def create_app(controller, token, allowed_hosts):
    if not token or not allowed_hosts:
        raise ValueError('A control credential and explicit hosts are required')

    async def action(request: Request):
        host = request.headers.get('host', '')
        origin = request.headers.get('origin')
        if host not in allowed_hosts or (origin and origin != 'http://' + host):
            return JSONResponse({'error': 'Forbidden'}, status_code=403)
        authorization = request.headers.get('authorization', '')
        if (not authorization.startswith('Bearer ') or not authorization.isascii() or len(authorization) > 520
                or not hmac.compare_digest(authorization[7:], token)):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        try:
            if int(request.headers.get('content-length', '-1')) not in range(0, 65537):
                return JSONResponse({'error': 'Invalid request size'}, status_code=413)
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            day, operation = request.path_params['day'], request.path_params['operation']
            specs = {
                'open-first': ({'account', 'seconds'}, lambda: controller.open_first(day, payload['account'], payload['seconds'])),
                'finish': ({'account', 'phase', 'session_id', 'status'}, lambda: controller.finish_run(
                    day, payload['account'], payload['phase'], payload['session_id'], payload['status'])),
                'recover': ({'account', 'session_id', 'seconds'}, lambda: controller.open_recovery(
                    day, payload['account'], payload['session_id'], payload['seconds'])),
                'lock': (set(), lambda: controller.seal_day(day)),
                'advance': ({'value'}, lambda: controller.advance_clock(day, payload['value'])),
                'prepare-settlement': ({'match_id', 'score', 'result_id', 'source', 'published_at', 'evidence'},
                           lambda: controller.prepare_settlement(day, payload['match_id'], payload['score'], payload['result_id'],
                                                                payload['source'], payload['published_at'], payload['evidence'])),
                'confirm-settlement': ({'match_id', 'sha256'}, lambda: controller.confirm_settlement(day, payload['match_id'], payload['sha256'])),
                'review-status': ({'match_id'}, lambda: controller.review_status(day, payload['match_id'])),
                'dismiss-review': ({'match_id', 'sha256'}, lambda: controller.dismiss_review(day, payload['match_id'], payload['sha256'])),
                'roster': ({'account', 'action'}, lambda: controller.change_roster(payload['account'], payload['action'])),
            }
            if controller.track == 'online':
                specs['open-review'] = ({'account', 'session_id', 'seconds'}, lambda: controller.open_review(
                    day, payload['account'], payload['session_id'], payload['seconds']))
            if operation not in specs:
                return JSONResponse({'error': 'Unknown operation'}, status_code=404)
            expected, execute = specs[operation]
            if set(payload) != expected:
                raise ValueError('Unexpected request fields')
            result = execute()
            return JSONResponse({'ok': True, 'result': result}, headers={'Cache-Control': 'no-store'})
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            return JSONResponse({'error': 'Invalid request'}, status_code=400, headers={'Cache-Control': 'no-store'})
        except RuntimeError:
            return JSONResponse({'error': 'Operation unavailable'}, status_code=409, headers={'Cache-Control': 'no-store'})
        except sqlite3.Error:
            return JSONResponse({'error': 'Control storage unavailable'}, status_code=503, headers={'Cache-Control': 'no-store'})

    return Starlette(routes=[Route('/v1/days/{day:str}/{operation:str}', action, methods=['POST'])])


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args()
    path = args.root / 'control-auth.token'
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError('Control credential must be a private regular file')
    token = path.read_text().strip()
    controller_type = HistoricalController
    if args.online:
        from .live_online_controller import LiveOnlineController
        controller_type = LiveOnlineController
    app = create_app(controller_type(args.root), token,
                     {'127.0.0.1:' + str(args.port), 'localhost:' + str(args.port)})
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
