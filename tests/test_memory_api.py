import unittest
from unittest import mock
from tests import helpers as h
try:
    from fastapi.testclient import TestClient
    import main
    import memory
    from ratelimit import RateLimiter
    from store import SessionStore
    HAVE_APP = True
except ImportError:
    HAVE_APP = False
from tests.test_memory import FakeQloo, SEEDS
SID = {'X-Session-ID': 'test-session-000000000000000000000000'}
BODY = {'birth_year': 1942, 'region': 'United States', 'seeds': SEEDS, 'adapt': False}

@unittest.skipUnless(HAVE_APP, 'fastapi is not installed')
class MemoryApiTests(unittest.TestCase):

    def setUp(self):
        previous = getattr(main, '_store', None)
        if previous is not None:
            previous.close()
        main._store = SessionStore(':memory:')
        self.addCleanup(main._store.close)
        main._sessions.clear()
        main.qloo_limiter = RateLimiter(1000)
        main.ai_limiter = RateLimiter(1000)
        self.client = TestClient(main.app)
        for patcher in (mock.patch.dict('os.environ', {'GEMINI_API_KEY': ''}), mock.patch.object(memory.qloo_client, 'get_recommendations', FakeQloo())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_session_plan_is_grounded_and_requires_a_session_id(self):
        self.assertEqual(self.client.post('/api/memory/session', json=BODY).status_code, 400)
        response = self.client.post('/api/memory/session', json=BODY, headers=SID)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['mode'], 'qloo-planner')
        self.assertTrue(data['stats']['grounded'])
        self.assertTrue(data['music'] and data['screen'])
        self.assertNotIn('shown_ids', data)

    def test_reaction_then_adapt_changes_the_plan(self):
        first = self.client.post('/api/memory/session', json=BODY, headers=SID).json()
        loved, upset = (first['screen'][0]['entity'], first['music'][0]['entity'])
        self.assertEqual(self.client.post('/api/memory/reaction', json={'entity': loved, 'reaction': 'love'}, headers=SID).status_code, 200)
        counts = self.client.post('/api/memory/reaction', json={'entity': upset, 'reaction': 'upset'}, headers=SID).json()['reaction_counts']
        self.assertEqual((counts['love'], counts['upset']), (1, 1))
        second = self.client.post('/api/memory/session', json={**BODY, 'adapt': True}, headers=SID).json()
        ids = {i['entity']['entity_id'] for i in [*second['music'], *second['screen']]}
        self.assertNotIn(upset['entity_id'], ids)
        self.assertEqual([r['entity']['entity_id'] for r in second['revisit']], [loved['entity_id']])
        self.assertTrue(second['adaptations'])
        fresh = self.client.post('/api/memory/session', json=BODY, headers=SID).json()
        self.assertFalse(fresh['adaptations'])

    def test_validation(self):
        bad_seed = {**BODY, 'seeds': [{'entity_id': 'b', 'name': 'A Book', 'category': 'Books'}]}
        self.assertEqual(self.client.post('/api/memory/session', json=bad_seed, headers=SID).status_code, 422)
        self.assertEqual(self.client.post('/api/memory/session', json={**BODY, 'birth_year': 1700}, headers=SID).status_code, 422)
        self.assertEqual(self.client.post('/api/memory/reaction', json={'entity': {'name': 'x'}, 'reaction': 'love'}, headers=SID).status_code, 422)
        self.assertEqual(self.client.post('/api/memory/reaction', json={'entity': {'entity_id': '1', 'name': 'x'}, 'reaction': 'meh'}, headers=SID).status_code, 422)

    def test_stream_endpoint_emits_progress_and_result(self):
        with self.client.stream('POST', '/api/memory/session/stream', json=BODY, headers=SID) as response:
            text = ''.join(response.iter_text())
        self.assertIn('event: progress', text)
        self.assertIn('event: result', text)

    def test_presets_baseline_and_reset(self):
        presets = self.client.get('/api/memory/presets').json()['presets']
        self.assertEqual({p['key'] for p in presets}, set(memory.PRESETS))
        self.assertEqual(self.client.post('/api/memory/preset/nope', headers=SID).status_code, 404)
        baseline = self.client.post('/api/memory/baseline', json=BODY, headers=SID).json()
        self.assertFalse(baseline['available'])
        self.assertEqual(self.client.post('/api/memory/reset', headers=SID).json()['status'], 'reset')
if __name__ == '__main__':
    unittest.main()
