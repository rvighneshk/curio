import unittest
from tests import helpers as h
import deeper
from qloo_client import QlooError
START = h.item('S0', 'The Martian', 'Movies', ['Sci-Fi', 'Survival'])

def chain_recommend(calls=None, tags=('Sci-Fi',)):

    def recommend(signal_ids, category, limit=10, exclude_ids=None, explain=True, trending=False, signal_names=None, signal_tags=None, popularity=None, meta=None):
        if calls is not None:
            calls.append({'signals': list(signal_ids), 'category': category, 'exclude': list(exclude_ids or []), 'popularity': popularity})
        seed = signal_ids[0]
        base = category[:2].upper()
        return [{**h.item(f'{base}-x-{seed}', f'{category} decoy {seed}', category, ['Romance']), 'qloo': {'affinity': 0.2}}, {**h.item(f'{base}-{seed}', f'{category} after {seed}', category, list(tags)), 'qloo': {'affinity': 0.9}}]
    return recommend

class BuildPathTests(unittest.TestCase):

    def test_every_hop_is_a_one_to_one_query_from_the_previous_item(self):
        calls = []
        out = deeper.build_path(START, hops=5, recommend=chain_recommend(calls))
        self.assertTrue(all((len(c['signals']) == 1 for c in calls)))
        seeds_in_order = []
        for c in calls:
            if c['signals'][0] not in seeds_in_order:
                seeds_in_order.append(c['signals'][0])
        previous = ['S0'] + [s['entity']['entity_id'] for s in out['steps'][:-1]]
        self.assertEqual(seeds_in_order, previous)
        self.assertEqual([s['from'] for s in out['steps']], ['The Martian'] + [s['entity']['name'] for s in out['steps'][:-1]])

    def test_the_path_rotates_across_all_five_categories(self):
        out = deeper.build_path(START, hops=5, recommend=chain_recommend())
        cats = [s['entity']['category'] for s in out['steps']]
        self.assertEqual(len(cats), 5)
        self.assertEqual(len(set(cats)), 5)
        self.assertNotEqual(cats[0], 'Movies')
        self.assertTrue(all((a != b for a, b in zip(cats, cats[1:]))))

    def test_best_scoring_candidate_wins_not_the_first_listed(self):
        out = deeper.build_path(START, hops=1, recommend=chain_recommend())
        self.assertIn('after S0', out['steps'][0]['entity']['name'])
        self.assertEqual(out['steps'][0]['evidence']['shared_tags'], ['Sci-Fi'])

    def test_blocked_and_repeated_items_are_never_chosen(self):

        def recommend(ids, category, **k):
            return [h.item('BLOCKED', 'Blocked', category, ['Sci-Fi']), h.item('S0', 'The Martian', category, ['Sci-Fi']), h.item(f'{category[:2]}-ok', f'{category} ok', category, ['Sci-Fi'])]
        out = deeper.build_path(START, blocked_ids=['BLOCKED'], hops=3, recommend=recommend)
        ids = [s['entity']['entity_id'] for s in out['steps']]
        self.assertNotIn('BLOCKED', ids)
        self.assertNotIn('S0', ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_exclusions_are_sent_to_qloo_with_path_ids_last(self):
        calls = []
        deeper.build_path(START, blocked_ids=['B1', 'B2'], hops=2, recommend=chain_recommend(calls))
        second_hop = [c for c in calls if c['signals'][0] != 'S0'][0]
        self.assertIn('B1', second_hop['exclude'])
        self.assertEqual(second_hop['exclude'][-2:], ['S0', second_hop['exclude'][-1]])

    def test_stops_early_with_a_note_when_qloo_runs_dry(self):

        def thin(ids, category, **k):
            return chain_recommend()(ids, category, **k) if ids[0] == 'S0' else []
        out = deeper.build_path(START, hops=5, recommend=thin)
        self.assertEqual(len(out['steps']), 1)
        self.assertIn('no further new discoveries after 1 hop', out['note'])

    def test_nothing_at_all_is_a_clean_empty_result(self):
        out = deeper.build_path(START, hops=3, recommend=lambda *a, **k: [])
        self.assertEqual(out['steps'], [])
        self.assertIn('no new discoveries', out['note'])

    def test_partial_qloo_errors_are_skipped_not_fatal(self):

        def flaky(ids, category, **k):
            if category == 'TV Shows':
                raise QlooError('down')
            return chain_recommend()(ids, category, **k)
        out = deeper.build_path(START, hops=4, recommend=flaky)
        self.assertEqual(len(out['steps']), 4)
        self.assertNotIn('TV Shows', [s['entity']['category'] for s in out['steps']])
        self.assertTrue(any(('Qloo error' in t['summary'] for t in out['trace'])))

    def test_transient_qloo_errors_are_sanitized_in_live_progress_but_kept_in_trace(self):
        updates = []

        def flaky(ids, category, **k):
            if category == 'TV Shows':
                raise QlooError('private provider diagnostic')
            return chain_recommend()(ids, category, **k)
        out = deeper.build_path(START, hops=1, recommend=flaky, progress=lambda title, detail: updates.append((title, detail)))
        self.assertTrue(any(('private provider diagnostic' in row['summary'] for row in out['trace'])))
        self.assertTrue(any(('checking another category' in detail for _, detail in updates)))
        self.assertFalse(any(('private provider diagnostic' in detail for _, detail in updates)))

    def test_total_qloo_failure_raises(self):

        def down(*a, **k):
            raise QlooError('down')
        with self.assertRaises(QlooError):
            deeper.build_path(START, hops=3, recommend=down)

    def test_popularity_is_forwarded(self):
        calls = []
        deeper.build_path(START, hops=1, recommend=chain_recommend(calls), popularity='gems')
        self.assertTrue(all((c['popularity'] == 'gems' for c in calls)))

    def test_start_needs_an_entity_id(self):
        with self.assertRaises(deeper.DeeperPathError):
            deeper.build_path({'name': 'x'}, recommend=chain_recommend())

    def test_steps_carry_evidence_and_a_plain_why_without_gemini(self):
        out = deeper.build_path(START, hops=2, recommend=chain_recommend())
        self.assertEqual(out['mode'], 'qloo')
        self.assertTrue(all(('Sci-Fi' in s['why'] and s['note'] == '' for s in out['steps'])))
        self.assertTrue(out['title'].startswith('A path from The Martian'))

def described(name, category, text, tags=(), affinity=0.5, eid=None):
    return {**h.item(eid or name, name, category, list(tags)), 'description': text, 'qloo': {'affinity': affinity}}

class RelevanceTests(unittest.TestCase):
    START_D = {**START, 'description': 'Astronauts stranded on Mars must survive.'}

    def test_a_related_candidate_in_a_later_category_beats_an_unrelated_one_in_the_first(self):

        def recommend(ids, category, **k):
            if category == 'TV Shows':
                return [described('News Show', category, 'Daily politics roundup.', affinity=0.9)]
            if category == 'Books':
                return [described('Red Planet', category, 'Colonists stranded on a hostile world struggle to survive.', affinity=0.4)]
            return []
        out = deeper.build_path(self.START_D, hops=1, recommend=recommend)
        self.assertEqual(out['steps'][0]['entity']['name'], 'Red Planet')
        self.assertTrue(any(('nothing in common' in t['summary'] for t in out['trace'])))

    def test_when_nothing_is_related_the_strongest_seen_candidate_is_used(self):

        def recommend(ids, category, **k):
            affinity = {'TV Shows': 0.3, 'Books': 0.8, 'Podcasts': 0.5}.get(category)
            return [described(f'{category} pick', category, 'Unrelated.', affinity=affinity)] if affinity else []
        out = deeper.build_path(self.START_D, hops=1, recommend=recommend)
        self.assertEqual(out['steps'][0]['entity']['name'], 'Books pick')

    def test_only_a_few_categories_are_compared_per_hop(self):
        calls = []

        def recommend(ids, category, **k):
            calls.append(category)
            return [described(f'{category} pick', category, 'Unrelated.')]
        deeper.build_path(self.START_D, hops=1, recommend=recommend)
        self.assertEqual(len(calls), deeper.MAX_CATEGORY_TRIES)

    def test_description_themes_make_the_bridge_specific(self):

        def recommend(ids, category, **k):
            return [described('Moon', category, 'A lone worker stranded on the moon must survive.', affinity=0.9)]
        out = deeper.build_path(self.START_D, hops=1, recommend=recommend)
        self.assertIn('stranded and survive', out['steps'][0]['why'])

    def test_narration_payload_hands_gemini_both_descriptions(self):

        def recommend(ids, category, **k):
            return [described(f'{category} after {ids[0][:3]}', category, f'About {category} after {ids[0][:3]} stranded.', eid=f'{category}-{ids[0]}')]
        out = deeper.build_path(self.START_D, hops=2, recommend=recommend)
        first, second = deeper.narration_payload(out)
        self.assertEqual(first['from_about'], self.START_D['description'])
        self.assertEqual(second['from_about'], out['steps'][0]['entity']['description'])
        self.assertEqual(second['to_about'], out['steps'][1]['entity']['description'])

class NarrationTests(unittest.TestCase):

    def setUp(self):
        self.result = deeper.build_path(START, hops=3, recommend=chain_recommend())

    def test_payload_lists_hops_with_only_qloo_data(self):
        payload = deeper.narration_payload(self.result)
        self.assertEqual(len(payload), 3)
        self.assertEqual(set(payload[0]), {'from', 'from_about', 'to', 'to_about', 'category', 'shared_tags', 'shared_terms'})

    def test_bridges_and_title_are_merged_and_items_never_change(self):
        before = [s['entity']['entity_id'] for s in self.result['steps']]
        deeper.apply_narration(self.result, {'title': 'Into the unknown', 'bridges': ['a', 'b', 'c']})
        self.assertEqual([s['note'] for s in self.result['steps']], ['a', 'b', 'c'])
        self.assertEqual(self.result['mode'], 'gemini')
        self.assertEqual(self.result['title'], 'Into the unknown')
        self.assertEqual([s['entity']['entity_id'] for s in self.result['steps']], before)

    def test_short_or_empty_narration_degrades_gracefully(self):
        deeper.apply_narration(self.result, {'bridges': ['only one', '  ']})
        self.assertEqual([s['note'] for s in self.result['steps']], ['only one', '', ''])
        self.assertTrue(self.result['title'].startswith('A path from'))

    def test_no_bridges_means_still_a_qloo_path(self):
        deeper.apply_narration(self.result, {'title': '', 'bridges': []})
        self.assertEqual(self.result['mode'], 'qloo')
if __name__ == '__main__':
    unittest.main()
