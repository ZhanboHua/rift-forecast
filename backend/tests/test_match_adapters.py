import json
import unittest
from lolarena.match_adapters import opgg_matches, riot_matches


class AdapterTests(unittest.TestCase):
    def test_opgg_finished_and_scheduled(self):
        row = {'id': '1', 'scheduledAt': '2030-01-01T00:00:00Z', 'numberOfGames': 3,
               'homeTeam': {'acronym': 'AAA'}, 'awayTeam': {'acronym': 'BBB'},
               'homeScore': 2, 'awayScore': 1, 'status': 'finished'}
        raw = json.dumps({'data': {'matchesBySerie': [row]}})
        self.assertEqual(opgg_matches(raw)[0]['score'], [2, 1])
        row['homeScore'] = 1
        with self.assertRaises(ValueError):
            opgg_matches(json.dumps({'data': {'matchesBySerie': [row]}}))
        row['status'] = 'not_started'
        self.assertIsNone(opgg_matches(json.dumps({'data': {'matchesBySerie': [row]}}))[0]['score'])

    def test_riot_completed(self):
        row = {'__typename': 'EventMatch', 'id': '1', 'startTime': '2030-01-01T00:00:00Z',
               'state': 'completed', 'match': {'strategy': {'count': 1}},
               'matchTeams': [{'code': 'AAA', 'result': {'gameWins': 1}}, {'code': 'BBB', 'result': {'gameWins': 0}}]}
        text = json.dumps(row, separators=(',', ':'))
        self.assertEqual(riot_matches(text)[0]['score'], [1, 0])
