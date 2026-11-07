"""Authenticated Bank HTTP and MCP adapter for a prepared historical day."""

import argparse
from pathlib import Path

from .bank import BankError, CREDENTIAL, create_app_for_bank
from .historical_controller import HistoricalController


class HistoricalBank:
    def __init__(self, root, day_id, allowed_hosts, online=False):
        if not allowed_hosts or any(not isinstance(host, str) or not host for host in allowed_hosts):
            raise ValueError('Explicit allowed hosts are required')
        self.config = {'allowed_hosts': list(allowed_hosts)}
        self.day_id = day_id
        if online:
            from .live_online_controller import LiveOnlineController
            self.controller = LiveOnlineController(Path(root))
        else:
            self.controller = HistoricalController(Path(root))

    def authenticate(self, authorization):
        if not authorization.startswith('Bearer '):
            return None
        return self.controller.authenticate(authorization[7:])

    @staticmethod
    def _call(method, *args):
        try:
            return method(*args)
        except PermissionError as exc:
            raise BankError('Unauthorized', 401) from exc
        except ValueError as exc:
            raise BankError('Invalid request', 400) from exc
        except RuntimeError as exc:
            raise BankError('Operation unavailable', 409) from exc

    def account(self, identity):
        return self._call(self.controller.account_view, self.day_id, identity)

    def match(self, identity):
        return self._call(self.controller.match_view, self.day_id, identity)

    def receipt(self, identity):
        return self._call(self.controller.receipt_view, self.day_id, identity)

    def submit(self, identity, prediction):
        return self.submit_many(identity, [prediction])

    def submit_many(self, identity, predictions):
        token = CREDENTIAL.get()
        if not token or self.controller.authenticate(token) != identity:
            raise BankError('Unauthorized', 401)
        return self._call(self.controller.submit_many, self.day_id, token, predictions)


def create_app(root, day_id, allowed_hosts, online=False):
    return create_app_for_bank(HistoricalBank(root, day_id, allowed_hosts, online=online))


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--day-id', required=True)
    parser.add_argument('--allowed-host', action='append', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args()
    uvicorn.run(create_app(args.root, args.day_id, args.allowed_host, online=args.online), host=args.host,
                port=args.port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
