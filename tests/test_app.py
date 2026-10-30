import unittest
from unittest import mock
from tests import helpers as h
try:
    from fastapi.testclient import TestClient
    import main
    from ratelimit import RateLimiter
    from store import SessionStore
    HAVE_APP = True
except ImportError:
    HAVE_APP = False
from tests.test_agent import fake_recommend
from tests.test_deeper import chain_recommend
SID = {'X-Session-ID': 'test-session-000000000000000000000000'}

def fav(entity_id, name, category, tags=()):
    return {'entity_id': entity_id, 'name': name, 'category': category, 'tags': list(tags)}

@unittest.skipUnless(HAVE_APP, 'fastapi is not installed')
class AppTests(unittest.TestCase):

    def setUp(self):
        previous_store = getattr(main, '_store', None)
        if previous_store is not None:
            previous_store.close()
        main._store = SessionStore(':memory:')
        self.addCleanup(main._store.close)
        main._sessions.clear()
        main.qloo_limiter = RateLimiter(1000)
        main.ai_limiter = RateLimiter(1000)
        self.client = TestClient(main.app)
        patcher = mock.patch.dict('os.environ', {'GEMINI_API_KEY': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, *items):
        for i in items:
            self.assertEqual(self.client.post('/api/onboarding/favourites', json=i, headers=SID).status_code, 200)

    def test_vibe_check_can_start_without_onboarding_favourites(self):
        parsed = {'original_prompt': 'A moody science-fiction book and late-night podcast', 'mood': ['moody'], 'themes': ['science fiction'], 'categories': ['Books', 'Podcasts'], 'seed_terms': ['science fiction', 'late-night podcast'], 'constraints': [], 'discovery_goal': 'cross-category exploration', 'parser': 'deterministic'}
        grounded_seed = fav('QB1', 'Qloo Book Seed', 'Books', ['Science Fiction'])
        result = {'theme': 'After dark', 'steps': [], 'mode': 'qloo-planner', 'trace': []}
        with mock.patch.object(main.vibe_intent, 'parse_vibe_prompt', return_value=parsed), mock.patch.object(main.vibe_intent, 'resolve_qloo_seeds', return_value=[grounded_seed]), mock.patch.object(main.discovery_agent, 'run_discovery_agent', return_value=result) as run:
            response = self.client.post('/api/vibe-check', headers=SID, json={'prompt': parsed['original_prompt']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['intent']['categories'], ['Books', 'Podcasts'])
        self.assertEqual(run.call_args.args[0][0]['entity_id'], 'QB1')
        self.assertEqual(run.call_args.kwargs['categories'], ['Books', 'Podcasts'])

    def test_vibe_check_rejects_whitespace_prompt(self):
        response = self.client.post('/api/vibe-check', headers=SID, json={'prompt': '   '})
        self.assertEqual(response.status_code, 422)

    def test_basics(self):
        self.assertIn('Curio', self.client.get('/').text)
        body = self.client.get('/health').json()
        self.assertEqual(body['status'], 'ok')
        self.assertFalse(body['gemini_configured'])
        self.assertEqual(len(self.client.get('/api/categories').json()['categories']), 5)

    def test_session_header_is_validated(self):
        self.assertEqual(self.client.get('/api/collection').status_code, 400)
        self.assertEqual(self.client.get('/api/collection', headers={'X-Session-ID': 'x'}).status_code, 400)
        self.assertEqual(self.client.get('/api/collection', headers={'X-Session-ID': 'short-session-id'}).status_code, 400)
        self.assertEqual(self.client.get('/api/collection', headers=SID).status_code, 200)

    def test_sessions_are_isolated(self):
        other_sid = {'X-Session-ID': 'other-session-000000000000000000000000'}
        self.add(fav('F1', 'Dune', 'Movies'))
        self.assertEqual(self.client.get('/api/onboarding/favourites', headers=other_sid).json()['count'], 0)
        self.assertEqual(self.client.get('/api/onboarding/favourites', headers=SID).json()['count'], 1)

    def test_favourite_limit_and_persistence_across_restart(self):
        self.add(*[fav(f'M{i}', f'Movie {i}', 'Movies', ['Sci-Fi']) for i in range(3)])
        over = self.client.post('/api/onboarding/favourites', json=fav('M9', 'Movie 9', 'Movies'), headers=SID)
        self.assertEqual(over.status_code, 409)
        main._sessions.clear()
        data = self.client.get('/api/onboarding/favourites', headers=SID).json()
        self.assertEqual(data['count'], 3)

    def test_explore_is_strictly_one_to_one_even_with_other_favourites_and_likes(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']), fav('F2', 'Neuromancer', 'Books', ['Sci-Fi']))
        liked = {**h.item('R1', 'Arrival', 'Movies', ['Sci-Fi']), 'properties': {'huge': 'x' * 9000}}
        res = self.client.post('/api/recommendations/actions', headers=SID, json={'entity_id': 'R1', 'action': 'like', 'journey_id': 'F1', 'parent_id': 'F1', 'entity': liked, 'seed_entity': fav('F1', 'Dune', 'Movies')})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('properties', self.client.get('/api/collection', headers=SID).json()['liked'][0])
        with mock.patch.object(main.qloo_client, 'get_recommendations', return_value=[]) as rec:
            data = self.client.post('/api/recommendations', headers=SID, json={'seed_entity_id': 'F1', 'seed_category': 'Movies', 'category': 'Books', 'journey_id': 'F1'}).json()
        self.assertEqual(rec.call_args.args[0], ['F1'])
        self.assertEqual([s['entity_id'] for s in data['signals']], ['F1'])
        self.assertIn('F2', rec.call_args.kwargs['exclude_ids'])

    def test_explore_handles_unusable_qloo_seed_with_friendly_notice(self):
        self.add(fav('F1', 'Even If This Love Disappears from the World Tonight', 'Movies'))
        from qloo_client import QlooBadRequest
        qloo_error = QlooBadRequest('Qloo rejected the request: {"errors":[{"message":"All of the provided signal.interests.entities were invalid. At least one valid signal or filter is required for entity insights.","path":"signal.interests.entities"}]}')
        with mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=qloo_error):
            response = self.client.post('/api/recommendations', headers=SID, json={'seed_entity_id': 'F1', 'seed_category': 'Movies', 'category': 'Movies', 'journey_id': 'F1'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'], [])
        self.assertIn('taste graph', data['notice'])
        self.assertNotIn('signal.interests.entities', data['notice'])

    def test_explore_puts_topical_results_first_and_attaches_qloo_evidence(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        off = {**h.item('R0', 'Unrelated', 'Books', ['Romance']), 'qloo': {'affinity': 0.9, 'drivers': []}}
        on = {**h.item('R1', 'Hyperion', 'Books', ['Sci-Fi', 'Epic']), 'qloo': {'affinity': 0.5, 'drivers': []}}
        with mock.patch.object(main.qloo_client, 'get_recommendations', return_value=[off, on]):
            data = self.client.post('/api/recommendations', headers=SID, json={'seed_entity_id': 'F1', 'seed_category': 'Movies', 'category': 'Books', 'journey_id': 'F1'}).json()
        self.assertEqual([r['entity_id'] for r in data['results']], ['R1', 'R0'])
        self.assertEqual(data['results'][0]['evidence']['shared_tags'], ['Sci-Fi'])
        self.assertEqual(data['results'][0]['evidence']['affinity'], 0.5)

    def test_recommendation_explanation_endpoint_is_removed(self):
        response = self.client.post('/api/explain', headers=SID, json={'source': h.item('F1', 'Dune', 'Movies', ['Sci-Fi']), 'recommendation': h.item('R1', 'Arrival', 'Movies', ['Sci-Fi'])})
        self.assertEqual(response.status_code, 404)

    def test_taste_profile_needs_favourites_then_works_without_gemini(self):
        self.assertEqual(self.client.post('/api/gemini/taste-profile', headers=SID, json={}).status_code, 409)
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']), fav('F2', 'Neuromancer', 'Books', ['Sci-Fi']))
        data = self.client.post('/api/gemini/taste-profile', headers=SID, json={}).json()
        self.assertEqual((data['source'], data['cached']), ('qloo', False))
        self.assertIn('Sci-Fi', data['profile']['signals'])

    def test_taste_profile_is_cached_until_taste_changes_or_refresh(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        good = {'title': 'Neon futures', 'summary': 's', 'signals': ['a', 'b', 'c'], 'connections': []}
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'x'}), mock.patch.object(main.taste_agent, 'create_taste_profile', return_value=good) as create, mock.patch.object(main.qloo_client, 'get_entities', return_value=[]):
            first = self.client.post('/api/gemini/taste-profile', headers=SID, json={}).json()
            second = self.client.post('/api/gemini/taste-profile', headers=SID, json={}).json()
            self.assertEqual((first['source'], first['cached'], second['source'], second['cached']), ('gemini', False, 'gemini', True))
            self.assertEqual(create.call_count, 1)
            self.client.post('/api/gemini/taste-profile', headers=SID, json={'refresh': True})
            self.assertEqual(create.call_count, 2)
            main._sessions.clear()
            self.client.post('/api/gemini/taste-profile', headers=SID, json={})
            self.assertEqual(create.call_count, 2)
            self.add(fav('F2', 'Neuromancer', 'Books', ['Sci-Fi']))
            self.client.post('/api/gemini/taste-profile', headers=SID, json={})
            self.assertEqual(create.call_count, 3)

    def test_taste_profile_falls_back_when_gemini_fails_and_does_not_cache_the_fallback(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'x'}), mock.patch.object(main.taste_agent, 'create_taste_profile', side_effect=main.taste_agent.TasteAgentError('quota')) as create, mock.patch.object(main.qloo_client, 'get_entities', return_value=[]):
            a = self.client.post('/api/gemini/taste-profile', headers=SID, json={})
            b = self.client.post('/api/gemini/taste-profile', headers=SID, json={})
        self.assertEqual((a.status_code, a.json()['source'], b.json()['cached']), (200, 'qloo', False))
        self.assertEqual(create.call_count, 2)

    def test_agent_path_end_to_end_without_gemini(self):
        self.assertEqual(self.client.post('/api/agent/path', headers=SID, json={}).status_code, 409)
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        with mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=fake_recommend), mock.patch.object(main.qloo_client, 'get_entities', return_value=[]):
            data = self.client.post('/api/agent/path', headers=SID, json={}).json()
        self.assertEqual(data['mode'], 'qloo-planner')
        self.assertEqual(len(data['steps']), 5)
        self.assertTrue(data['trace'])

    def test_agent_path_with_gemini_ranking_and_with_gemini_failing(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        rec = mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=fake_recommend)
        ent = mock.patch.object(main.qloo_client, 'get_entities', return_value=[])
        env = mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'x'})
        reply = {'theme': 'Neon', 'steps': [{'id': 'art1', 'note': 'n'}, {'id': 'book1', 'note': 'n'}]}
        with env, rec, ent, mock.patch.object(main.taste_agent, 'rank_candidates', return_value=reply):
            ok = self.client.post('/api/agent/path', headers=SID, json={}).json()
        self.assertEqual((ok['mode'], ok['steps'][0]['entity']['entity_id'], len(ok['steps'])), ('gemini-agent', 'AR-1', 5))
        with env, rec, ent, mock.patch.object(main.taste_agent, 'rank_candidates', side_effect=main.taste_agent.TasteAgentError('boom')):
            res = self.client.post('/api/agent/path', headers=SID, json={})
        self.assertEqual((res.status_code, res.json()['mode'], len(res.json()['steps'])), (200, 'qloo-planner', 5))

    def test_popularity_is_forwarded_and_relaxation_reported(self):
        self.add(fav('F1', 'Dune', 'Movies'))

        def fake(*args, **kwargs):
            kwargs['meta']['dropped'] = ['popularity']
            return []
        with mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=fake) as rec:
            data = self.client.post('/api/recommendations', headers=SID, json={'seed_entity_id': 'F1', 'seed_category': 'Movies', 'category': 'Books', 'journey_id': 'F1', 'popularity': 'gems'}).json()
        self.assertEqual(rec.call_args.kwargs['popularity'], 'gems')
        self.assertEqual(data['relaxed'], ['popularity'])

    def deeper_body(self, **kw):
        return {'seed_entity_id': 'F1', 'seed_category': 'Movies', 'journey_id': 'F1', **kw}

    def test_deeper_path_works_without_gemini_and_is_built_hop_by_hop_from_qloo(self):
        self.assertEqual(self.client.post('/api/deeper/path', headers=SID, json={}).status_code, 409)
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        calls = []
        with mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=chain_recommend(calls)), mock.patch.object(main.qloo_client, 'get_entities', return_value=[]):
            data = self.client.post('/api/deeper/path', headers=SID, json=self.deeper_body()).json()
        self.assertEqual((data['mode'], len(data['steps'])), ('qloo', 5))
        self.assertTrue(all((len(c['signals']) == 1 for c in calls)))
        self.assertEqual(data['steps'][0]['from'], 'Dune')

    def test_memory_loved_item_can_start_deeper_path_without_curio_onboarding(self):
        loved = fav('ML-1', 'Louis Armstrong', 'Artists', ['Jazz', 'Swing'])
        self.assertEqual(self.client.post('/api/memory/reaction', headers=SID, json={'entity': loved, 'reaction': 'love'}).status_code, 200)
        with mock.patch.object(main.qloo_client, 'get_entities', return_value=[loved]), mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=chain_recommend()) as rec:
            response = self.client.post('/api/deeper/path', headers=SID, json={'seed_entity_id': 'ML-1', 'seed_category': 'Artists', 'source': 'memory', 'hops': 5})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['source_mode'], 'memory')
        self.assertEqual(data['starting_entity']['entity_id'], 'ML-1')
        self.assertEqual(data['steps'][0]['from'], 'Louis Armstrong')
        self.assertEqual(len(data['steps']), 5)
        self.assertEqual(main.session('test-session-000000000000000000000000')['memory']['reactions']['ML-1']['reaction'], 'love')
        self.assertTrue(rec.call_args_list)
        self.assertEqual(rec.call_args_list[0].args[0], ['ML-1'])

    def test_memory_deeper_path_requires_a_loved_reaction_and_qloo_verification(self):
        item = fav('ML-2', 'The Supremes', 'Artists', ['Soul'])
        body = {'seed_entity_id': 'ML-2', 'seed_category': 'Artists', 'source': 'memory'}
        not_reacted = self.client.post('/api/deeper/path', headers=SID, json=body)
        self.assertEqual(not_reacted.status_code, 409)
        self.client.post('/api/memory/reaction', headers=SID, json={'entity': item, 'reaction': 'ok'})
        not_loved = self.client.post('/api/deeper/path', headers=SID, json=body)
        self.assertEqual(not_loved.status_code, 409)
        self.client.post('/api/memory/reaction', headers=SID, json={'entity': item, 'reaction': 'love'})
        with mock.patch.object(main.qloo_client, 'get_entities', return_value=[]), mock.patch.object(main.qloo_client, 'search_entities', return_value=[]):
            unverified = self.client.post('/api/deeper/path', headers=SID, json=body)
        self.assertEqual(unverified.status_code, 422)

    def test_memory_deeper_stream_returns_result_and_preserves_source_mode(self):
        loved = fav('ML-3', 'Ella Fitzgerald', 'Artists', ['Jazz'])
        self.client.post('/api/memory/reaction', headers=SID, json={'entity': loved, 'reaction': 'love'})
        with mock.patch.object(main.qloo_client, 'get_entities', return_value=[loved]), mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=chain_recommend()):
            response = self.client.post('/api/deeper/path/stream', headers=SID, json={'seed_entity_id': 'ML-3', 'seed_category': 'Artists', 'source': 'memory', 'hops': 2})
        self.assertEqual(response.status_code, 200)
        self.assertIn('event: progress', response.text)
        self.assertIn('"source_mode":"memory"', response.text)
        self.assertIn('event: result', response.text)

    def test_deeper_path_gemini_bridges_are_optional_and_never_break_the_path(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        env = mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'x'})
        rec = mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=chain_recommend())
        ent = mock.patch.object(main.qloo_client, 'get_entities', return_value=[])
        with env, rec, ent, mock.patch.object(main.taste_agent, 'narrate_path', return_value={'title': 'T', 'bridges': ['a', 'b', 'c', 'd', 'e']}) as narrate:
            ok = self.client.post('/api/deeper/path', headers=SID, json=self.deeper_body()).json()
        self.assertEqual((ok['mode'], ok['title'], ok['steps'][0]['note']), ('gemini', 'T', 'a'))
        start_name, hops, start_about = narrate.call_args.args
        self.assertEqual(start_name, 'Dune')
        self.assertEqual(len(hops), 5)
        self.assertIn('to_about', hops[0])
        with env, rec, ent, mock.patch.object(main.taste_agent, 'narrate_path', side_effect=main.taste_agent.TasteAgentError('quota')):
            failed = self.client.post('/api/deeper/path', headers=SID, json=self.deeper_body())
        self.assertEqual((failed.status_code, failed.json()['mode'], len(failed.json()['steps'])), (200, 'qloo', 5))
        self.assertTrue(any((t['tool'] == 'fallback' for t in failed.json()['trace'])))

    def test_deeper_stream_emits_live_progress_and_final_result(self):
        self.add(fav('F1', 'Dune', 'Movies', ['Sci-Fi']))
        rec = mock.patch.object(main.qloo_client, 'get_recommendations', side_effect=chain_recommend())
        ent = mock.patch.object(main.qloo_client, 'get_entities', return_value=[])
        with rec, ent:
            response = self.client.post('/api/deeper/path/stream', headers=SID, json=self.deeper_body())
        self.assertEqual(response.status_code, 200)
        self.assertIn('event: progress', response.text)
        self.assertIn('Following Qloo connections', response.text)
        self.assertIn('event: result', response.text)
        self.assertIn('"steps"', response.text)

    def test_stream_hides_unexpected_exception_details_from_client(self):
        with mock.patch.object(main, '_build_taste_profile', side_effect=RuntimeError('provider token=secret-token internal endpoint')):
            with self.assertLogs('curio', level='ERROR') as captured:
                response = self.client.post('/api/gemini/taste-profile/stream', headers=SID, json={})
        self.assertEqual(response.status_code, 200)
        self.assertIn('event: error', response.text)
        self.assertIn('Something went wrong while Curio was processing this request.', response.text)
        self.assertNotIn('secret-token', response.text)
        self.assertNotIn('internal endpoint', response.text)
        self.assertTrue(any(('provider token=secret-token internal endpoint' in line for line in captured.output)))

    def test_old_gemini_ideas_endpoint_is_gone(self):
        self.assertEqual(self.client.post('/api/gemini/deeper-suggestions', headers=SID, json={}).status_code, 404)

    def test_rate_limit_returns_429(self):
        main.qloo_limiter = RateLimiter(1)
        with mock.patch.object(main, 'search_entities', return_value=[]):
            ok = self.client.get('/api/search', params={'q': 'dune', 'category': 'Movies'})
            blocked = self.client.get('/api/search', params={'q': 'dune', 'category': 'Movies'})
        self.assertEqual((ok.status_code, blocked.status_code), (200, 429))

    def test_client_ip_uses_first_valid_forwarded_address_when_proxy_trusted(self):
        from starlette.requests import Request
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': '/', 'raw_path': b'/', 'query_string': b'', 'headers': [(b'x-forwarded-for', b'198.51.100.77, 203.0.113.9')], 'client': ('127.0.0.1', 12345), 'server': ('testserver', 80)}
        request = Request(scope)
        with mock.patch.object(main, 'TRUST_PROXY', True):
            self.assertEqual(main.client_ip(request), '198.51.100.77')

    def test_client_ip_ignores_invalid_forwarded_entries(self):
        from starlette.requests import Request
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': '/', 'raw_path': b'/', 'query_string': b'', 'headers': [(b'x-forwarded-for', b'forged, 203.0.113.9')], 'client': ('127.0.0.1', 12345), 'server': ('testserver', 80)}
        request = Request(scope)
        with mock.patch.object(main, 'TRUST_PROXY', True):
            self.assertEqual(main.client_ip(request), '203.0.113.9')

    def test_reset_keeps_favourites(self):
        self.add(fav('F1', 'Dune', 'Movies'))
        self.client.post('/api/session/reset', headers=SID)
        self.assertEqual(self.client.get('/api/onboarding/favourites', headers=SID).json()['count'], 1)
if __name__ == '__main__':
    unittest.main()
