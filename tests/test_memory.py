import json
import unittest
from unittest import mock
from tests import helpers as h
import memory
import qloo_client as q
import taste_agent

def ent(entity_id, name, category, year=None, tags=(), affinity=0.9, songs=None):
    entity = {'entity_id': entity_id, 'name': name, 'category': category, 'tags': list(tags), 'release_year': year, 'description': f'About {name}.', 'type': 'urn:entity', 'qloo': {'affinity': affinity, 'popularity': 0.8, 'drivers': [{'entity_id': 'S1', 'name': 'Frank Sinatra', 'weight': 0.6}]}, 'facts': {'notable_songs': songs} if songs else {}}
    return entity
CATALOG = {'TV Shows': [ent('T1', 'The Honeymooners', 'TV Shows', 1955, ['Comedy']), ent('T2', 'Father Knows Best', 'TV Shows', 1954, ['Family']), ent('T3', 'The Lucy Show', 'TV Shows', 1962, ['Comedy']), ent('T4', 'Hazel', 'TV Shows', 1961), ent('T9', 'Modern Show', 'TV Shows', 2005)], 'Movies': [ent('M1', 'Auntie Mame', 'Movies', 1958), ent('M2', 'Desk Set', 'Movies', 1957), ent('M3', 'Cat on a Hot Tin Roof', 'Movies', 1958)], 'Artists': [ent('A1', 'Louis Armstrong', 'Artists', songs=['What a Wonderful World']), ent('A2', 'Nat King Cole', 'Artists'), ent('A3', 'Peggy Lee', 'Artists'), ent('A4', 'Vaughn Monroe', 'Artists'), ent('A5', 'Doris Day', 'Artists'), ent('A9', 'Upsetting Singer', 'Artists')]}
SEEDS = [{'entity_id': 'S1', 'name': 'Frank Sinatra', 'category': 'Artists', 'tags': ['Swing']}, {'entity_id': 'S2', 'name': 'I Love Lucy', 'category': 'TV Shows', 'tags': ['Comedy'], 'release_year': 1951}]
PROFILE = {'birth_year': 1942, 'region': 'United States'}

class FakeQloo:

    def __init__(self, catalog=None, dropped=None, thin_screen=False):
        self.catalog = catalog or CATALOG
        self.calls = []
        self.dropped = dropped or []
        self.thin_screen = thin_screen

    def __call__(self, signal_ids, category, limit=10, exclude_ids=None, explain=True, signal_names=None, year_min=None, year_max=None, popularity_max=None, age_bracket=None, meta=None, **kw):
        self.calls.append({'signals': list(signal_ids), 'category': category, 'year_min': year_min, 'year_max': year_max, 'popularity_max': popularity_max, 'age_bracket': age_bracket, 'exclude': list(exclude_ids or [])})
        items = [dict(e) for e in self.catalog[category] if e['entity_id'] not in (exclude_ids or [])]
        if self.thin_screen and category in memory.SCREEN_CATEGORIES and year_min and (year_min >= 1952) and ((year_max or 0) <= 1972):
            items = items[:1]
        if meta is not None:
            meta['dropped'] = list(self.dropped)
        return items

class WindowTests(unittest.TestCase):

    def test_formative_window_and_age_bracket(self):
        self.assertEqual(memory.formative_window(1942), (1952, 1972))
        low, high = memory.formative_window(memory.current_year() - 5)
        self.assertLessEqual(high, memory.current_year())
        self.assertGreaterEqual(high, low)
        self.assertEqual(memory.age_bracket(1942), '55_and_older')
        self.assertIsNone(memory.age_bracket(memory.current_year() - 20))

    def test_bad_profile_rejected(self):
        with self.assertRaises(memory.MemoryLaneError):
            memory.MemoryAgent({'birth_year': 'abc'}, SEEDS)
        with self.assertRaises(memory.MemoryLaneError):
            memory.MemoryAgent({'birth_year': 1800}, SEEDS)
        with self.assertRaises(memory.MemoryLaneError):
            memory.MemoryAgent(PROFILE, [])

class QlooOnlyPlannerTests(unittest.TestCase):

    def run_agent(self, fake=None, **kwargs):
        fake = fake or FakeQloo()
        result = memory.run_memory_agent(PROFILE, SEEDS, use_gemini=False, recommend=fake, **kwargs)
        return (result, fake)

    def test_plan_is_grounded_era_filtered_and_traced(self):
        result, fake = self.run_agent()
        self.assertEqual(result['mode'], 'qloo-planner')
        returned = {e['entity_id'] for items in CATALOG.values() for e in items}
        for item in [*result['music'], *result['screen']]:
            self.assertIn(item['entity']['entity_id'], returned)
        names = [i['entity']['name'] for i in result['screen']]
        self.assertNotIn('Modern Show', names)
        self.assertEqual(len(result['music']), memory.MUSIC_COUNT)
        self.assertTrue(result['stats']['grounded'])
        tv_call = next((c for c in fake.calls if c['category'] == 'TV Shows'))
        self.assertEqual((tv_call['year_min'], tv_call['year_max']), (1952, 1972))
        artist_call = next((c for c in fake.calls if c['category'] == 'Artists'))
        self.assertEqual(artist_call['popularity_max'], memory.POPULARITY_CAP)
        self.assertEqual(artist_call['age_bracket'], '55_and_older')
        self.assertTrue(any((s in artist_call['signals'] for s in ('T1', 'T2', 'T3', 'T4'))))
        self.assertTrue(any((t['tool'] == 'explore' for t in result['trace'])))
        self.assertEqual(result['music'][0]['entity']['links']['open'].startswith('https://open.spotify.com/'), True)

    def test_every_item_has_a_why_and_prompt(self):
        result, _ = self.run_agent()
        for item in [*result['music'], *result['screen']]:
            self.assertTrue(item['why'])
            self.assertTrue(item['prompt'])
        self.assertIn('What a Wonderful World', result['music'][0]['prompt'])

    def test_thin_results_trigger_adaptation(self):
        result, fake = self.run_agent(FakeQloo(thin_screen=True))
        tv_calls = [c for c in fake.calls if c['category'] == 'TV Shows']
        self.assertEqual(len(tv_calls), 2)
        self.assertLess(tv_calls[1]['year_min'], tv_calls[0]['year_min'])
        self.assertTrue(any(('widened' in a for a in result['adaptations'])))

    def test_local_filter_when_qloo_ignores_era(self):
        result, _ = self.run_agent(FakeQloo(dropped=['era']))
        self.assertNotIn('Modern Show', [i['entity']['name'] for i in result['screen']])

    def test_qloo_returning_nothing_is_an_error(self):
        empty = FakeQloo({'TV Shows': [], 'Movies': [], 'Artists': []})
        with self.assertRaises(q.QlooError):
            memory.run_memory_agent(PROFILE, SEEDS, use_gemini=False, recommend=empty)

class ReactionTests(unittest.TestCase):

    def test_reactions_reshape_the_next_plan(self):
        reactions = {'T1': {'reaction': 'love', 'entity': {**CATALOG['TV Shows'][0], 'year': 1955}}, 'T2': {'reaction': 'love', 'entity': {**CATALOG['TV Shows'][1], 'year': 1954}}, 'A9': {'reaction': 'upset', 'entity': CATALOG['Artists'][5]}}
        fake = FakeQloo()
        result = memory.run_memory_agent(PROFILE, SEEDS, reactions=reactions, shown_ids=['T1', 'T2', 'A9', 'A1'], use_gemini=False, recommend=fake)
        self.assertEqual((result['era']['from'], result['era']['to']), (1951, 1958))
        self.assertEqual({r['entity']['entity_id'] for r in result['revisit']}, {'T1', 'T2'})
        shown = {i['entity']['entity_id'] for i in [*result['music'], *result['screen']]}
        self.assertNotIn('A9', shown)
        self.assertNotIn('A1', shown)
        self.assertNotIn('T1', shown)
        self.assertTrue(any(('cluster' in a for a in result['adaptations'])))
        self.assertTrue(any(("didn't sit well" in a for a in result['adaptations'])))
        artist_call = next((c for c in fake.calls if c['category'] == 'Artists'))
        self.assertIn('T1', artist_call['signals'])

    def test_record_reaction_validation(self):
        state = memory.new_state()
        counts = memory.record_reaction(state, {**CATALOG['Artists'][0], 'year': None}, 'love')
        self.assertEqual(counts['love'], 1)
        with self.assertRaises(memory.MemoryLaneError):
            memory.record_reaction(state, CATALOG['Artists'][0], 'meh')
        with self.assertRaises(memory.MemoryLaneError):
            memory.record_reaction(state, {'name': 'No id'}, 'love')

class ToolTests(unittest.TestCase):

    def setUp(self):
        self.agent = memory.MemoryAgent(PROFILE, SEEDS, recommend=FakeQloo())

    def test_explore_rejects_bad_input(self):
        self.assertIn('error', self.agent.explore('Books', ['s1']))
        self.assertIn('error', self.agent.explore('Movies', ['nope']))
        self.assertIn('error', self.agent.explore('Movies', None))

    def test_explore_accepts_string_handle_and_budget(self):
        self.assertGreater(self.agent.explore('Movies', 's1', 1952, 1972)['count'], 0)
        for _ in range(memory.MAX_TOOL_CALLS):
            self.agent.explore('Movies', ['s1'])
        self.assertIn('budget', self.agent.explore('Movies', ['s1'])['error'])

    def test_hint_and_inspect(self):
        agent = memory.MemoryAgent(PROFILE, SEEDS, recommend=FakeQloo({'TV Shows': [CATALOG['TV Shows'][0]], 'Movies': [], 'Artists': []}))
        result = agent.explore('TV Shows', ['s1', 's2'], 1952, 1972)
        self.assertIn('Widen', result['hint'])
        handle = result['results'][0]['handle']
        self.assertEqual(agent.inspect(handle)['name'], 'The Honeymooners')
        self.assertIn('error', agent.inspect('zzz'))

class GeminiPathTests(unittest.TestCase):

    def patched(self, plan_fn):
        return (mock.patch.multiple('memory', taste_agent=mock.Mock(gemini_configured=lambda: True, TasteAgentError=taste_agent.TasteAgentError, _redact=lambda s: s)), mock.patch.object(memory.MemoryAgent, 'plan_with_gemini', plan_fn))

    def test_valid_picks_kept_and_invented_ones_discarded(self):

        def fake_plan(agent):
            agent.explore('TV Shows', agent.seed_handles, 1952, 1972)
            agent.explore('Artists', agent.seed_handles, deeper_cuts=True)
            return {'title': 'Sunday evenings', 'intro': 'Gentle start.', 'music': ['art1', 'art999', 'Made Up Singer'], 'screen': ['tv1', 'tv777'], 'prompts': [{'about': 'art1', 'question': 'What does this music bring to mind?'}, {'about': 'ghost', 'question': 'x'}], 'tips': ['Go slowly.'], 'adaptation_note': 'Widened the era after a thin first result.'}
        p1, p2 = self.patched(fake_plan)
        with p1, p2:
            result = memory.run_memory_agent(PROFILE, SEEDS, recommend=FakeQloo())
        self.assertEqual(result['mode'], 'gemini-agent')
        self.assertEqual(result['title'], 'Sunday evenings')
        names = [i['entity']['name'] for i in [*result['music'], *result['screen']]]
        self.assertNotIn('Made Up Singer', names)
        self.assertEqual(result['music'][0]['prompt'], 'What does this music bring to mind?')
        self.assertTrue(result['stats']['grounded'])
        self.assertGreaterEqual(len(result['music']), 3)
        self.assertIn('Agent: Widened the era after a thin first result.', result['adaptations'])

    def test_gemini_failure_falls_back_to_qloo_planner(self):

        def broken(agent):
            raise taste_agent.TasteAgentError('model unavailable')
        p1, p2 = self.patched(broken)
        with p1, p2:
            result = memory.run_memory_agent(PROFILE, SEEDS, recommend=FakeQloo())
        self.assertEqual(result['mode'], 'qloo-planner')
        self.assertIn('prepared your recommendations using Qloo', result['note'])
        self.assertTrue(result['music'] and result['screen'])

    def test_gemini_that_never_calls_tools_falls_back(self):

        def lazy(agent):
            if not agent.entities:
                raise taste_agent.TasteAgentError('Gemini answered without calling the Qloo tools')
        p1, p2 = self.patched(lazy)
        with p1, p2:
            result = memory.run_memory_agent(PROFILE, SEEDS, recommend=FakeQloo())
        self.assertEqual(result['mode'], 'qloo-planner')
        self.assertTrue(result['music'])

class BaselineAndPresetTests(unittest.TestCase):

    def test_baseline_counts_items_outside_the_window(self):
        payload = json.dumps({'items': [{'category': 'Artist', 'name': 'Frank Sinatra', 'year': None}, {'category': 'TV Show', 'name': 'Friends', 'year': 1994}, {'category': 'Movie', 'name': 'Casablanca', 'year': 1942}, {'category': 'TV Show', 'name': 'I Love Lucy', 'year': 1955}]})
        with mock.patch.object(taste_agent, 'gemini_configured', return_value=True), mock.patch.object(taste_agent, '_generate', return_value=payload):
            out = memory.baseline_llm_only(PROFILE, SEEDS, ['I Love Lucy'])
        self.assertTrue(out['available'])
        self.assertEqual(out['dated_count'], 3)
        self.assertEqual(out['outside_window'], 2)
        self.assertEqual(out['overlap_with_grounded'], 1)

    def test_baseline_retries_when_gemini_returns_no_items(self):
        payload = json.dumps({'items': [{'category': 'Artist', 'name': 'Nat King Cole', 'year': None}]})
        with mock.patch.object(taste_agent, 'gemini_configured', return_value=True), mock.patch.object(taste_agent, '_generate', side_effect=['{"items":[]}', payload]) as generate:
            out = memory.baseline_llm_only(PROFILE, SEEDS)
        self.assertEqual(len(out['items']), 1)
        self.assertEqual(generate.call_count, 2)
        self.assertIn('Independent Gemini response', out['note'])

    def test_baseline_unavailable_without_gemini(self):
        with mock.patch.object(taste_agent, 'gemini_configured', return_value=False):
            self.assertFalse(memory.baseline_llm_only(PROFILE, SEEDS)['available'])

    def test_preset_prefers_exact_name(self):

        def search(name, category, limit):
            return [h.item('X1', name + ' Jr.', category), h.item('X2', name, category)]
        out = memory.resolve_preset('classic-hollywood', search)
        self.assertEqual([s['entity_id'] for s in out['seeds']], ['X2', 'X2', 'X2'])
        self.assertEqual(out['birth_year'], 1942)
        with self.assertRaises(memory.MemoryLaneError):
            memory.resolve_preset('nope', search)

class QlooClientMemoryParams(unittest.TestCase):

    def setUp(self):
        q.clear_cache()
        patcher = mock.patch.object(q, '_sleep', lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_era_popularity_and_audience_params_are_sent(self):
        payload = h.insights_payload(h.qloo_entity('E1', 'X'))
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, payload)) as get:
            q.get_recommendations(['S1'], 'Movies', year_min=1952, year_max=1972, popularity_max=0.9, age_bracket='55_and_older')
        params = get.call_args.kwargs['params']
        self.assertEqual(params['filter.release_year.min'], 1952)
        self.assertEqual(params['filter.release_year.max'], 1972)
        self.assertEqual(params['filter.popularity.max'], 0.9)
        self.assertEqual(params['signal.demographics.age'], '55_and_older')

    def test_only_the_rejected_parameter_is_dropped(self):
        ok = h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))
        bad = h.FakeResponse(400, text='{"errors":[{"message":"unknown parameter signal.demographics.age"}]}')
        meta = {}
        with mock.patch.object(q.requests, 'get', side_effect=[bad, ok]) as get:
            q.get_recommendations(['S1'], 'Artists', year_min=1950, age_bracket='55_and_older', meta=meta)
        second = get.call_args_list[1].kwargs['params']
        self.assertNotIn('signal.demographics.age', second)
        self.assertIn('filter.release_year.min', second)
        self.assertIn('feature.explainability', second)
        self.assertEqual(meta['dropped'], ['audience'])

    def test_empty_results_relax_audience_before_anything_else_and_never_era(self):
        empty = h.FakeResponse(200, h.insights_payload())
        ok = h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))
        with mock.patch.object(q.requests, 'get', side_effect=[empty, ok]) as get:
            q.get_recommendations(['S1'], 'Movies', year_min=1950, age_bracket='55_and_older', explain=False)
        self.assertNotIn('signal.demographics.age', get.call_args_list[1].kwargs['params'])
        self.assertIn('filter.release_year.min', get.call_args_list[1].kwargs['params'])

    def test_artist_facts_and_spotify_id_are_extracted(self):
        entity = {'entity_id': 'A', 'name': 'Louis Armstrong', 'type': 'urn:entity:artist', 'properties': {'notable_songs': ['What a Wonderful World', 'Hello, Dolly!'], 'date_of_birth': '1901-08-04', 'year_of_death': 1971, 'known_for': 'Virtuosic trumpet playing.', 'adjectives_for_fans': ['Devoted', 'Multigenerational']}, 'external': {'spotify': [{'id': '19eLuQmk9aCobbVDHc6eek', 'followers': 1}]}}
        out = q.normalize_entity(entity, 'Artists')
        self.assertEqual(out['spotify_id'], '19eLuQmk9aCobbVDHc6eek')
        self.assertEqual(out['facts']['notable_songs'][0], 'What a Wonderful World')
        self.assertEqual(out['facts']['born'], '1901-08-04')
        self.assertEqual(out['facts']['died'], '1971')
        self.assertEqual(out['facts']['fan_traits'], ['Devoted', 'Multigenerational'])
        self.assertEqual(q.normalize_entity({'entity_id': 'B', 'name': 'No Spotify', 'external': {'spotify': [{'id': '../x'}]}}, 'Artists')['spotify_id'], '')
if __name__ == '__main__':
    unittest.main()
