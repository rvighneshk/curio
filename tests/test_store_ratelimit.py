import unittest
from tests import helpers as h
from ratelimit import DailyCap, RateLimiter
from store import SessionStore

class StoreTests(unittest.TestCase):

    def test_roundtrip_update_delete(self):
        store = SessionStore(':memory:')
        self.addCleanup(store.close)
        self.assertIsNone(store.get('abc'))
        store.put('abc', {'a': 1, 'text': 'café'})
        store.put('abc', {'a': 2})
        self.assertEqual(store.get('abc'), {'a': 2})
        store.delete('abc')
        self.assertIsNone(store.get('abc'))

    def test_prune_removes_only_old_sessions(self):
        store = SessionStore(':memory:')
        self.addCleanup(store.close)
        store.put('old', {})
        store._db.execute("UPDATE sessions SET updated_at = 0 WHERE session_id = 'old'")
        store.put('new', {})
        self.assertEqual(store.prune(30), 1)
        self.assertIsNone(store.get('old'))
        self.assertEqual(store.get('new'), {})

    def test_sessions_are_isolated(self):
        store = SessionStore(':memory:')
        self.addCleanup(store.close)
        store.put('a', {'v': 1})
        store.put('b', {'v': 2})
        self.assertEqual(store.get('a'), {'v': 1})

class LimiterTests(unittest.TestCase):

    def test_sliding_window(self):
        now = [0.0]
        limiter = RateLimiter(2, 60, clock=lambda: now[0])
        self.assertTrue(limiter.allow('ip'))
        self.assertTrue(limiter.allow('ip'))
        self.assertFalse(limiter.allow('ip'))
        self.assertTrue(limiter.allow('other'))
        now[0] = 61
        self.assertTrue(limiter.allow('ip'))

    def test_daily_cap_resets_next_day(self):
        now = [1000.0]
        cap = DailyCap(2, clock=lambda: now[0])
        self.assertTrue(cap.allow())
        self.assertTrue(cap.allow())
        self.assertFalse(cap.allow())
        now[0] += 86400
        self.assertTrue(cap.allow())
if __name__ == '__main__':
    unittest.main()
