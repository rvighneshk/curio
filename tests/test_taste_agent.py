import unittest
from unittest import mock
from tests import helpers as h
import taste_agent as ta

class Resp:

    def __init__(self, text):
        self.text = text

class FakeClient:

    def __init__(self, behaviour):
        self.behaviour, self.tried = (behaviour, [])
        self.models = self

    def generate_content_stream(self, model, contents, config=None):
        self.tried.append(model)
        result = self.behaviour[model]
        if isinstance(result, Exception):
            raise result
        yield Resp(result)

class GeminiLayerTests(unittest.TestCase):

    def patched(self, behaviour):
        client = FakeClient(behaviour)
        env = mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'secret-key'})
        env.start()
        self.addCleanup(env.stop)
        p = mock.patch.object(ta, '_client', return_value=client)
        p.start()
        self.addCleanup(p.stop)
        m = mock.patch.object(ta, '_models', return_value=['m1', 'm2', 'm3'])
        m.start()
        self.addCleanup(m.stop)
        return client

    def test_unavailable_model_falls_through_to_the_next(self):
        client = self.patched({'m1': Exception('404 NOT_FOUND model'), 'm2': 'hello', 'm3': 'unused'})
        self.assertEqual(ta._generate('x'), 'hello')
        self.assertEqual(client.tried, ['m1', 'm2'])

    def test_other_errors_raise_without_leaking_the_key(self):
        self.patched({'m1': Exception('429 quota for key secret-key'), 'm2': 'unused', 'm3': 'unused'})
        with self.assertRaises(ta.TasteAgentError) as ctx:
            ta._generate('x')
        self.assertNotIn('secret-key', str(ctx.exception))
        self.assertIn('[redacted]', str(ctx.exception))

    def test_all_models_missing_is_one_clear_error(self):
        self.patched({m: Exception('404 not found') for m in ('m1', 'm2', 'm3')})
        with self.assertRaises(ta.TasteAgentError) as ctx:
            ta._generate('x')
        self.assertIn('No configured Gemini model is available', str(ctx.exception))

    def test_empty_response_is_an_error(self):
        self.patched({'m1': '   ', 'm2': 'x', 'm3': 'x'})
        with self.assertRaises(ta.TasteAgentError):
            ta._generate('x')

    def test_json_extraction_handles_fences_and_chatter(self):
        self.assertEqual(ta._extract_json('```json\n{"a": 1}\n```'), {'a': 1})

    def test_bridge_parser_keeps_partial_valid_bridges_for_fallback(self):
        with mock.patch.object(ta, '_generate', return_value='{"title":"A path","bridges":["A grounded connection", null]}'):
            result = ta.narrate_path('Dune', [{'from': 'Dune', 'to': 'Arrival'}, {'from': 'Arrival', 'to': 'Contact'}])
        self.assertEqual(result['bridges'], ['A grounded connection', ''])
        self.assertEqual(ta._extract_json('Sure! {"a": {"b": [1, 2]}} hope that helps'), {'a': {'b': [1, 2]}})
        for bad in ('no json here', '{broken', '[1, 2]'):
            with self.assertRaises(ta.TasteAgentError):
                ta._extract_json(bad)

    def test_rank_candidates_prompt_contains_only_qloo_candidates_and_goal(self):
        sent = {}

        def fake(prompt, json_mode=False, progress=None):
            sent['prompt'], sent['json_mode'] = (prompt, json_mode)
            return '{"theme": "T", "steps": [{"id": "mov1", "note": "n"}]}'
        with mock.patch.object(ta, '_generate', fake):
            out = ta.rank_candidates({'top_tags': ['Sci-Fi']}, 'cozy', [{'id': 'mov1', 'name': 'Dune'}])
        self.assertTrue(sent['json_mode'])
        self.assertIn('"id": "mov1"', sent['prompt'])
        self.assertIn('cozy', sent['prompt'])
        self.assertIn('never add anything else', sent['prompt'])
        self.assertEqual(out['steps'][0]['id'], 'mov1')

    def test_rank_candidates_filters_unknown_and_duplicate_ids(self):
        reply = '{"theme":"  Space   journey ","steps":[{"id":"invented","note":"not grounded"},{"id":"mov1","note":"  grounded   note  "},{"id":"mov1","note":"duplicate"},{"id":"book2","note":"also grounded"}]}'
        candidates = [{'id': 'mov1', 'name': 'Dune'}, {'id': 'book2', 'name': 'Contact'}]
        with mock.patch.object(ta, '_generate', return_value=reply):
            result = ta.rank_candidates({}, None, candidates)
        self.assertEqual(result['theme'], 'Space journey')
        self.assertEqual(result['steps'], [{'id': 'mov1', 'note': 'grounded note'}, {'id': 'book2', 'note': 'also grounded'}])

    def test_narrate_path_requires_exactly_the_hops_it_is_given_and_tolerates_bad_shapes(self):
        with mock.patch.object(ta, '_generate', return_value='{"title": "T", "bridges": ["a", "b"]}'):
            self.assertEqual(ta.narrate_path('Start', [{}, {}])['bridges'], ['a', 'b'])
        with mock.patch.object(ta, '_generate', return_value='{"title": "T", "bridges": "oops"}'):
            self.assertEqual(ta.narrate_path('Start', [{}])['bridges'], [])

    def test_path_prompt_hands_gemini_the_descriptions_and_forbids_boilerplate(self):
        sent = {}

        def fake(prompt, json_mode=False, progress=None):
            sent['p'] = prompt
            return '{"title": "T", "bridges": ["a"]}'
        hop = {'from': 'Gravity', 'from_about': 'Astronauts stranded in orbit.', 'to': 'Moon', 'to_about': 'A lone worker on the moon.', 'category': 'Movies', 'shared_tags': [], 'shared_terms': ['stranded']}
        with mock.patch.object(ta, '_generate', fake):
            ta.narrate_path('Gravity', [hop], 'Astronauts stranded in orbit.')
        p = sent['p']
        self.assertIn('Astronauts stranded in orbit.', p)
        self.assertIn('A lone worker on the moon.', p)
        self.assertIn('Do not mention Qloo', p)
        self.assertIn('do not start two sentences the same way', p)
        self.assertIn('exactly 1 bridges', p)

    def test_rank_prompt_asks_for_description_grounded_non_boilerplate_notes(self):
        sent = {}
        with mock.patch.object(ta, '_generate', lambda p, json_mode=False, progress=None: sent.setdefault('p', p) and '{"steps": []}'):
            ta.rank_candidates({}, None, [{'id': 'mov1', 'about': 'A film about stranded astronauts.'}])
        self.assertIn("'about' text", sent['p'])
        self.assertIn('do not mention Qloo', sent['p'])
        self.assertIn('stranded astronauts', sent['p'])

    def test_taste_profile_is_trimmed_and_filters_unknown_connection_entities(self):
        reply = '{"title": "T", "summary": "S", "signals": ["a", 5, "b"], "connections": [{"from": "dune", "to": "Arrival", "reason": "r"}, {"from": "Dune", "to": "Made Up", "reason": "invented"}, "junk"], "extra": 1}'
        favourites = [h.item('1', 'Dune', 'Movies', ['Sci-Fi'])]
        journey = [h.item('2', 'Arrival', 'Movies', ['Space'])]
        with mock.patch.object(ta, '_generate', return_value=reply):
            profile = ta.create_taste_profile(favourites, journey)
        self.assertEqual(profile['signals'], ['a', 'b'])
        self.assertEqual(profile['connections'], [{'from': 'Dune', 'to': 'Arrival', 'reason': 'r'}])
        self.assertNotIn('extra', profile)

    def test_prompt_includes_qloo_tags_for_grounding(self):
        sent = {}
        with mock.patch.object(ta, '_generate', lambda p, json_mode=False, progress=None: sent.setdefault('p', p) and '{"title":"T","signals":["s"]}'):
            ta.create_taste_profile([h.item('1', 'Dune', 'Movies', ['Sci-Fi'])], [])
        self.assertIn('Sci-Fi', sent['p'])
if __name__ == '__main__':
    unittest.main()
