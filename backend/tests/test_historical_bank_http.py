import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path

from starlette.testclient import TestClient

from lolarena.bank import create_app_for_bank
from lolarena.historical_bank import HistoricalBank
from lolarena.history_accounts import HISTORICAL_ACCOUNTS
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_historical_controller import HistoricalControllerTest


class HistoricalBankHttpTest(HistoricalControllerTest):
    def test_authenticated_http_mcp_and_cross_account_isolation(self):
        self.prepare()
        first, second, _ = HISTORICAL_ACCOUNTS
        self.controller.open_first('day-1', first, 60)
        self.controller.open_first('day-1', second, 60)
        bank = HistoricalBank.__new__(HistoricalBank)
        bank.config = {'allowed_hosts': ['testserver']}
        bank.day_id = 'day-1'
        bank.controller = self.controller
        first_headers = {'Authorization': 'Bearer ' + self.tokens[first]}
        second_headers = {'Authorization': 'Bearer ' + self.tokens[second]}
        with TestClient(create_app_for_bank(bank)) as client:
            self.assertEqual(client.get('/api/match').status_code, 401)
            self.assertEqual(client.get('/api/account', headers=first_headers).json()['account_id'], first)
            fixtures = client.get('/api/match', headers=first_headers).json()['matches']
            self.assertEqual(len(fixtures), 2)
            bad = {**self.prediction(2, 3), 'p_team_a_win': 2}
            response = client.post('/api/submission', headers=first_headers, json=[self.prediction(1, 1), bad])
            self.assertEqual(response.status_code, 400)
            with closing(sqlite3.connect(self.root / 'bank/accounts.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_submissions').fetchone()[0], 0)
            response = client.post('/api/submission', headers=first_headers, json=[self.prediction(1, 1), self.prediction(2, 3)])
            self.assertEqual(response.status_code, 200)
            other = client.get('/api/submission', headers=second_headers).json()
            self.assertFalse(other['matches']['fictional-1']['submitted'])
            init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                               'clientInfo': {'name': 'fixture-test', 'version': '1'}}}
            mcp_headers = {**first_headers, 'Accept': 'application/json, text/event-stream'}
            self.assertEqual(client.post('/mcp', headers=mcp_headers, json=init).status_code, 200)
            tools = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}
            listed = client.post('/mcp', headers=mcp_headers, json=tools).json()
            self.assertIn('bank_submit_predictions', {item['name'] for item in listed['result']['tools']})
            call = {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'bank_submit_predictions', 'arguments': {'predictions': [self.prediction(1, 1)]}}}
            second_mcp_headers = {**second_headers, 'Accept': 'application/json, text/event-stream'}
            result = client.post('/mcp', headers=second_mcp_headers, json=call)
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['result'].get('isError', False))
            self.assertTrue(client.get('/api/submission', headers=second_headers).json()['matches']['fictional-1']['submitted'])
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/', headers={'Origin': 'http://invalid.example'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
