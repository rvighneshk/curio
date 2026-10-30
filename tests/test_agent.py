import unittest
from unittest import mock
from tests import helpers as h
import agent
import taste_agent
from qloo_client import QlooError

def fake_recommend(signal_ids, category, limit=10, exclude_ids=None, explain=True, trending=False, signal_names=None, signal_tags=None, popularity=None, meta=None):
    base = category[:2].upper()
    return [{**h.item(f'{base}-1', f'{category} One', category, ['Sci-Fi', 'Dystopian']), 'qloo': {'affinity': 0.8, 'drivers': []}}, {**h.item(f'{base}-2', f'{category} Two', category, ['Romance']), 'qloo': {'affinity': 0.3, 'drivers': []}}]
FAVS = [h.item('F1', 'Dune', 'Movies', ['Sci-Fi', 'Epic']), h.item('F2', 'Neuromancer', 'Books', ['Sci-Fi'])]

def ranking(reply):
    side = reply if callable(reply) or isinstance(reply, Exception) else None
    return (mock.patch.object(taste_agent, 'gemini_configured', return_value=True), mock.patch.object(taste_agent, 'rank_candidates', side_effect=side, return_value=None if side else reply))

def run(reply=None, **kw):
    if reply is None:
        return agent.run_discovery_agent(FAVS, [], recommend=fake_recommend, **kw)
    a, b = ranking(reply)
    with a, b:
        return agent.run_discovery_agent(FAVS, [], recommend=fake_recommend, **kw)

class QlooOnlyTests(unittest.TestCase):

    def test_five_qloo_backed_steps_one_per_category_without_gemini(self):
        out = run(use_gemini=False)
        self.assertEqual(out['mode'], 'qloo-planner')
        self.assertEqual(len(out['steps']), 5)
        self.assertEqual(len({s['entity']['category'] for s in out['steps']}), 5)
        self.assertTrue(all((s['entity']['entity_id'].endswith('-1') for s in out['steps'])))
        self.assertTrue(all((s['why'] and s['evidence']['shared_tags'] for s in out['steps'])))

    def test_trace_shows_the_three_stages(self):
        tools = [t['tool'] for t in run(use_gemini=False)['trace']]
        self.assertEqual(tools[0], 'analyze_taste')
        self.assertEqual(tools.count('discover'), 5)
        self.assertEqual(tools[-2:], ['rank', 'validate'])

    def test_least_explored_categories_are_visited_first(self):
        discovered = [t['summary'].split(':')[0] for t in run(use_gemini=False)['trace'] if t['tool'] == 'discover']
        self.assertTrue(set(discovered[:3]) <= {'TV Shows', 'Podcasts', 'Artists'})

    def test_never_recommends_what_the_user_already_has_or_skipped(self):

        def leaky(*a, **k):
            return fake_recommend(*a, **k) + [h.item('F1', 'Dune', 'Movies', ['Sci-Fi']), h.item('MO-1', 'Skipped', 'Movies', ['Sci-Fi'])]
        ctx = agent.AgentContext(FAVS, [], skipped_ids=['MO-1'], recommend=leaky)
        names = [i['name'] for i in ctx.discover('Movies')['items']]
        self.assertNotIn('Dune', names)
        self.assertNotIn('Skipped', names)

    def test_missing_categories_are_topped_up_from_the_next_best_qloo_results(self):

        def flaky(ids, category, **k):
            if category in ('TV Shows', 'Podcasts'):
                raise QlooError('down')
            return fake_recommend(ids, category, **k)
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=flaky)
        cats = [s['entity']['category'] for s in out['steps']]
        self.assertEqual(len(out['steps']), 5)
        self.assertTrue(all((cats.count(c) <= agent.MAX_PER_CATEGORY for c in set(cats))))

    def test_vibe_check_can_limit_discovery_to_requested_categories(self):
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=fake_recommend, categories=['Movies', 'Books'])
        discovered = [t['summary'].split(':')[0] for t in out['trace'] if t['tool'] == 'discover']
        self.assertEqual(set(discovered), {'Movies', 'Books'})
        self.assertTrue(all((step['entity']['category'] in {'Movies', 'Books'} for step in out['steps'])))

    def test_explicit_genre_filters_out_unrelated_qloo_candidates(self):

        def genre_recommend(ids, category, **kwargs):
            return [h.item('HOR-1', 'The Night House', category, ['Horror', 'Supernatural']), h.item('ROM-1', 'Which Brings Me to You', category, ['Romance', 'Comedy'])]
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=genre_recommend, categories=['Movies'], required_genres=['horror'])
        self.assertTrue(out['steps'])
        self.assertTrue(all(('Horror' in step['entity']['tags'] for step in out['steps'])))
        self.assertNotIn('Which Brings Me to You', [step['entity']['name'] for step in out['steps']])
        discover = next((event for event in out['trace'] if event['tool'] == 'discover'))
        self.assertEqual(discover['details']['genre_filtered_count'], 1)

    def test_total_qloo_failure_raises(self):

        def down(*a, **k):
            raise QlooError('down')
        with self.assertRaises(QlooError):
            agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=down)

    def test_requires_a_starting_point(self):
        with self.assertRaises(agent.AgentPlanError):
            agent.run_discovery_agent([], [], use_gemini=False, recommend=fake_recommend)

    def test_unknown_category_is_reported_not_raised(self):
        self.assertIn('error', agent.AgentContext(FAVS, [], recommend=fake_recommend).discover('Video Games'))

    def test_gemini_is_never_called_when_not_configured(self):
        with mock.patch.object(taste_agent, 'gemini_configured', return_value=False), mock.patch.object(taste_agent, 'rank_candidates') as rank:
            out = agent.run_discovery_agent(FAVS, [], recommend=fake_recommend)
        rank.assert_not_called()
        self.assertEqual(out['mode'], 'qloo-planner')

    def test_vibe_reference_seeds_are_prioritized_over_existing_favourites(self):
        reference = h.item('REF-1', 'The Conjuring', 'Movies', ['Horror'])
        reference['vibe_role'] = 'reference'
        ctx = agent.AgentContext([*FAVS], [], recommend=fake_recommend)
        ctx.seeds = [*FAVS, reference]
        signals = ctx._signals_for('Movies')
        self.assertEqual(signals[0]['entity_id'], 'REF-1')

    def test_vibe_discovery_requests_a_broader_candidate_pool(self):
        captured = {}

        def capture(ids, category, **kwargs):
            captured['limit'] = kwargs.get('limit')
            return fake_recommend(ids, category, **kwargs)
        ctx = agent.AgentContext(FAVS, [], recommend=capture, categories=['Movies'])
        ctx.discover('Movies')
        self.assertEqual(captured['limit'], 20)

    def test_horror_filter_accepts_paranormal_qloo_labels(self):

        def genre_recommend(ids, category, **kwargs):
            return [h.item('PAR-1', 'The Haunted House', category, ['Paranormal', 'Ghosts']), h.item('ACT-1', 'Kingsman: The Secret Service', category, ['Action', 'Comedy'])]
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=genre_recommend, categories=['Movies'], required_genres=['horror'])
        names = [step['entity']['name'] for step in out['steps']]
        self.assertIn('The Haunted House', names)
        self.assertNotIn('Kingsman: The Secret Service', names)

class GeminiRankingTests(unittest.TestCase):

    def test_gemini_chooses_orders_and_explains_from_qloo_candidates(self):
        out = run({'theme': 'Neon futures', 'steps': [{'id': 'art1', 'note': 'Shares Sci-Fi.'}, {'id': 'book1', 'note': 'b'}, {'id': 'mov1', 'note': 'c'}, {'id': 'pod1', 'note': 'd'}, {'id': 'tv1', 'note': 'e'}]})
        self.assertEqual(out['mode'], 'gemini-agent')
        self.assertEqual(out['theme'], 'Neon futures')
        self.assertEqual([s['entity']['entity_id'] for s in out['steps']], ['AR-1', 'BO-1', 'MO-1', 'PO-1', 'TV-1'])
        self.assertEqual(out['steps'][0]['note'], 'Shares Sci-Fi.')
        self.assertEqual(out['topped_up'], 0)

    def test_gemini_may_pick_a_different_qloo_candidate_than_the_top_score(self):
        out = run({'steps': [{'id': 'mov2'}, {'id': 'book1'}, {'id': 'pod1'}, {'id': 'art1'}, {'id': 'tv1'}]})
        self.assertEqual(out['steps'][0]['entity']['entity_id'], 'MO-2')

    def test_gemini_only_sees_short_handles_and_qloo_evidence(self):
        seen = {}

        def capture(taste, goal, candidates):
            seen['candidates'], seen['goal'] = (candidates, goal)
            return {'steps': []}
        run(capture, goal=None)
        first = seen['candidates'][0]
        self.assertRegex(first['id'], '^(mov|tv|book|pod|art)\\d+$')
        self.assertIn('shared_tags', first)
        self.assertNotIn('entity_id', first)
        self.assertLessEqual(len(seen['candidates']), 15)

    def test_gemini_is_given_descriptions_and_shared_themes_to_reason_with(self):
        seen = {}

        def capture(taste, goal, candidates):
            seen['c'] = candidates
            return {'steps': []}

        def recommend(ids, category, **k):
            return [{**h.item(f'{category[:2]}-1', f'{category} One', category, ['Sci-Fi']), 'qloo': {'affinity': 0.8}, 'description': 'Astronauts stranded in space must survive.'}]
        favs = [{**h.item('F1', 'Gravity', 'Movies', ['Sci-Fi']), 'description': 'Astronauts stranded in orbit fight to survive.'}]
        a_, b_ = ranking(capture)
        with a_, b_:
            agent.run_discovery_agent(favs, [], recommend=recommend)
        first = seen['c'][0]
        self.assertIn('stranded', first['about'])
        self.assertIn('stranded', first['shared_terms'])

    def test_hallucinated_ids_are_dropped_and_the_path_is_completed_from_qloo(self):
        out = run({'steps': [{'id': 'mov1', 'note': 'a'}, {'id': 'GHOST', 'note': 'x'}, {'id': 'book1', 'note': 'b'}]})
        self.assertEqual(out['mode'], 'gemini-agent')
        self.assertEqual(len(out['steps']), 5)
        self.assertEqual(out['topped_up'], 3)
        self.assertEqual([s['entity']['entity_id'] for s in out['steps'][:2]], ['MO-1', 'BO-1'])
        self.assertTrue(all((s['note'] == '' for s in out['steps'][2:])))

    def test_nothing_matching_falls_back_with_the_real_reason(self):
        out = run({'steps': [{'id': 'ghost1'}, {'id': 'ghost2'}, {'id': 'ghost3'}]})
        self.assertEqual(out['mode'], 'qloo-planner')
        self.assertEqual(len(out['steps']), 5)
        self.assertIn("none of Gemini's 3 picks matched Qloo's candidates", out['note'])

    def test_gemini_failure_falls_back_with_a_note(self):
        out = run(taste_agent.TasteAgentError('boom'))
        self.assertEqual(out['mode'], 'qloo-planner')
        self.assertIn("Gemini couldn't rank this time (boom)", out['note'])
        self.assertEqual(len(out['steps']), 5)

    def test_malformed_answers_fall_back(self):
        for bad in ({'theme': 'x'}, {'steps': []}, {'steps': 'nope'}):
            out = run(bad)
            self.assertEqual((out['mode'], len(out['steps'])), ('qloo-planner', 5), bad)

    def test_names_and_mixed_case_handles_resolve(self):
        out = run({'steps': [{'name': 'Movies One'}, {'id': 'BOOK1'}, {'title': 'podcasts one'}, {'entity_id': 'AR-1'}]})
        self.assertEqual(out['mode'], 'gemini-agent')
        self.assertEqual([s['entity']['entity_id'] for s in out['steps'][:4]], ['MO-1', 'BO-1', 'PO-1', 'AR-1'])

    def test_extra_and_duplicate_picks_are_trimmed(self):
        picks = [{'id': h_} for h_ in ('mov1', 'mov1', 'book1', 'pod1', 'art1', 'tv1', 'mov2', 'book2')]
        out = run({'steps': picks})
        ids = [s['entity']['entity_id'] for s in out['steps']]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)

class GoalTests(unittest.TestCase):

    def test_goal_becomes_qloo_tags_that_steer_every_query(self):
        calls = []

        def recorder(ids, category, **k):
            calls.append(k.get('signal_tags'))
            return fake_recommend(ids, category, **k)
        tags = {'cozy': [{'tag_id': 'urn:tag:cozy', 'name': 'Cozy'}], 'funny': [{'tag_id': 'urn:tag:funny', 'name': 'Funny'}]}
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=recorder, goal='something cozy and funny', search_tags=lambda word, limit=1: tags.get(word, []))
        self.assertTrue(all((c == ['urn:tag:cozy', 'urn:tag:funny'] for c in calls)))
        self.assertIn('Cozy, Funny', out['goal_status'])
        self.assertIn('Cozy and Funny', out['theme'])

    def test_goal_is_also_passed_to_gemini(self):
        seen = {}

        def capture(taste, goal, candidates):
            seen['goal'] = goal
            return {'steps': []}
        run(capture, goal='cozy', search_tags=lambda w, limit=1: [])
        self.assertEqual(seen['goal'], 'cozy')

    def test_unmatched_goal_is_reported_not_silently_ignored(self):
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=fake_recommend, goal='slower stranger', search_tags=lambda word, limit=1: [])
        self.assertEqual(out['goal_status'], "No Qloo tag matched your goal, so it wasn't applied.")
        self.assertEqual(len(out['steps']), 5)

    def test_goal_dropped_by_qloo_is_reported(self):

        def drops_goal(ids, category, meta=None, **k):
            if meta is not None:
                meta['dropped'] = ['goal']
            return fake_recommend(ids, category, **k)
        out = agent.run_discovery_agent(FAVS, [], use_gemini=False, recommend=drops_goal, goal='cozy', search_tags=lambda w, limit=1: [{'tag_id': 't', 'name': 'Cozy'}])
        self.assertIn("couldn't combine", out['goal_status'])

    def test_no_goal_means_no_status(self):
        self.assertIsNone(run(use_gemini=False)['goal_status'])
if __name__ == '__main__':
    unittest.main()
