import unittest
from tests import helpers as h
import taste

class TasteTests(unittest.TestCase):

    def setUp(self):
        self.favs = [h.item('1', 'Dune', 'Movies', ['Sci-Fi', 'Epic']), h.item('2', 'Neuromancer', 'Books', ['Sci-Fi', 'Cyberpunk']), h.item('3', 'Vangelis', 'Artists', ['Electronic', 'Sci-Fi'])]
        self.liked = [h.item('4', 'Severance', 'TV Shows', ['Dystopian', 'Sci-Fi'])]

    def test_signature_weights_and_cross_domain_threads(self):
        sig = taste.taste_signature(self.favs, self.liked)
        self.assertEqual(sig['top_tags'][0]['tag'], 'Sci-Fi')
        self.assertEqual(sig['top_tags'][0]['weight'], 7)
        self.assertEqual(sig['threads'][0]['tag'], 'Sci-Fi')
        self.assertGreaterEqual(len(sig['threads'][0]['categories']), 3)
        self.assertEqual(sig['coverage'], {'Movies': 1, 'Books': 1, 'Artists': 1, 'TV Shows': 1})

    def test_signature_of_nothing(self):
        sig = taste.taste_signature([], [])
        self.assertEqual(sig['top_tags'], [])
        self.assertIn('just getting started', taste.fallback_profile(sig)['title'])

    def test_evidence_shared_tags_and_driver_fallback(self):
        cand = h.item('9', 'Arrival', 'Movies', ['Sci-Fi', 'Drama'])
        ev = taste.build_evidence(self.favs, cand)
        self.assertEqual(ev['shared_tags'], ['Sci-Fi'])
        self.assertTrue(ev['drivers'])
        self.assertIsNone(ev['affinity'])

    def test_evidence_prefers_qloo_drivers_and_affinity(self):
        cand = {**h.item('9', 'Arrival', 'Movies', ['Sci-Fi']), 'qloo': {'affinity': 0.77, 'popularity': 0.5, 'drivers': [{'entity_id': '2', 'name': 'Neuromancer', 'weight': 0.9}]}}
        ev = taste.build_evidence(self.favs, cand)
        self.assertEqual(ev['drivers'][0]['name'], 'Neuromancer')
        self.assertEqual(ev['affinity'], 0.77)

    def test_explain_text_only_uses_evidence(self):
        text = taste.explain_text('Dune', 'Arrival', {'shared_tags': ['Sci-Fi', 'Drama'], 'drivers': []})
        self.assertIn('Sci-Fi and Drama', text)
        thin = taste.explain_text('Dune', 'Arrival', {'shared_tags': [], 'drivers': []})
        self.assertIn('audience data', thin)
        self.assertNotIn('directed', thin.lower())

    def test_compact_entity_drops_bulky_raw_fields(self):
        raw = {**h.item('1', 'Dune', 'Movies', ['Sci-Fi']), 'properties': {'x': 'y' * 5000}, 'external': {'imdb': [{}] * 50}}
        compact = taste.compact_entity(raw)
        self.assertNotIn('properties', compact)
        self.assertNotIn('external', compact)
        self.assertEqual(compact['tags'], ['Sci-Fi'])

    def test_fallback_profile_has_threads_as_connections(self):
        prof = taste.fallback_profile(taste.taste_signature(self.favs, self.liked))
        self.assertIn('Sci-Fi', prof['signals'])
        self.assertTrue(prof['connections'])
        self.assertIn('Sci-Fi', prof['connections'][0]['reason'])

    def test_driver_source_distinguishes_qloo_statements_from_inference(self):
        cand = h.item('9', 'Arrival', 'Movies', ['Sci-Fi'])
        self.assertEqual(taste.build_evidence(self.favs, cand)['drivers_source'], 'tags')
        stated = {**cand, 'qloo': {'drivers': [{'entity_id': '2', 'name': 'Neuromancer', 'weight': 0.9}]}}
        self.assertEqual(taste.build_evidence(self.favs, stated)['drivers_source'], 'qloo')
        self.assertIsNone(taste.build_evidence([], {**cand, 'tags': []})['drivers_source'])

    def test_goal_keywords_keep_content_words_only(self):
        self.assertEqual(taste.goal_keywords('something cozy and funny'), ['cozy', 'funny'])
        self.assertEqual(taste.goal_keywords('I want a slower, stranger vibe'), ['slower', 'stranger'])
        self.assertEqual(taste.goal_keywords('a an the of'), [])
        self.assertEqual(taste.goal_keywords(None), [])
        self.assertEqual(len(taste.goal_keywords('dark moody cozy funny tense')), 3)

    def test_prefer_on_topic_is_a_stable_partition(self):
        seed = h.item('S', 'Seed', 'Movies', ['Sci-Fi'])
        a, b, c, d = (h.item(i, i, 'Movies', t) for i, t in (('a', ['Romance']), ('b', ['sci-fi']), ('c', []), ('d', ['Sci-Fi', 'Epic'])))
        self.assertEqual([i['entity_id'] for i in taste.prefer_on_topic(seed, [a, b, c, d])], ['b', 'd', 'a', 'c'])
        self.assertEqual([i['entity_id'] for i in taste.prefer_on_topic(h.item('S', 'S', 'Movies', []), [a, b])], ['a', 'b'])

    def test_profile_key_tracks_taste_changes_but_not_ordering(self):
        favs, liked = (self.favs, self.liked)
        k1 = taste.profile_key(favs, liked, [])
        self.assertEqual(k1, taste.profile_key(list(reversed(favs)), liked, []))
        self.assertNotEqual(k1, taste.profile_key(favs, [*liked, h.item('9', 'New', 'Books')], []))
        self.assertNotEqual(k1, taste.profile_key(favs, liked, [{'entity_id': '9', 'feedback': 'liked'}]))
        self.assertNotEqual(taste.profile_key(favs, liked, [{'entity_id': '9', 'feedback': 'liked'}]), taste.profile_key(favs, liked, [{'entity_id': '9', 'feedback': 'skipped'}]))

    def test_description_overlap_finds_distinctive_shared_words_only(self):
        a = {'description': 'Two astronauts work together to survive after an accident leaves them stranded in space.'}
        b = {'description': 'Stranded on an ancient spaceship, a crew of scientists must fight to survive. A story about people.'}
        self.assertEqual(taste.description_overlap(a, b), ['stranded', 'survive'])
        self.assertEqual(taste.description_overlap(a, {'description': ''}), [])
        self.assertEqual(taste.description_overlap({}, {}), [])
        self.assertEqual(taste.description_overlap({'short_description': 'astronauts drifting'}, {'description': 'an astronaut stranded'}), ['astronaut'])

    def test_evidence_carries_shared_terms(self):
        seed = {**h.item('1', 'Gravity', 'Movies'), 'description': 'Astronauts stranded in orbit fight to survive.'}
        cand = {**h.item('2', 'Moon', 'Movies'), 'description': 'A lone worker stranded on the moon must survive isolation.'}
        self.assertEqual(taste.build_evidence([seed], cand)['shared_terms'], ['stranded', 'survive'])
        self.assertEqual(taste.build_evidence([seed], h.item('3', 'X', 'Movies'))['shared_terms'], [])

    def test_every_branch_of_the_plain_connection_text(self):
        ev = lambda **k: {'shared_tags': [], 'shared_terms': [], 'drivers': [], 'affinity': None, **k}
        both = taste.explain_text('Gravity', 'Stargate', ev(shared_tags=['Survival'], shared_terms=['stranded', 'survive'], affinity=0.98))
        self.assertEqual(both, 'Stargate shares Survival with Gravity, and both descriptions mention stranded and survive (Qloo match 98%).')
        self.assertIn('Both descriptions mention stranded', taste.explain_text('A', 'B', ev(shared_terms=['stranded'])))
        self.assertIn("shares Survival with A; Qloo's audience data", taste.explain_text('A', 'B', ev(shared_tags=['Survival'])))
        driver = taste.explain_text('A', 'B', ev(drivers=[{'name': 'C'}]))
        self.assertIn('reaches the same audience as C', driver)
        bare = taste.explain_text('A', 'B', ev(affinity=0.64))
        self.assertIn('(Qloo match 64%)', bare)
        self.assertNotIn('match', taste.explain_text('A', 'B', ev(affinity=7.5)))

    def test_connection_text_is_not_one_repeated_template(self):
        texts = {taste.explain_text('A', 'B', {'shared_tags': [], 'shared_terms': t, 'drivers': [], 'affinity': a}) for t, a in ((['stranded'], 0.9), (['robot'], 0.5), ([], 0.4))}
        self.assertEqual(len(texts), 3)
if __name__ == '__main__':
    unittest.main()

class CompactBookMetadataTests(unittest.TestCase):

    def test_compact_entity_preserves_author(self):
        result = taste.compact_entity({'entity_id': 'B1', 'name': 'Example', 'category': 'Books', 'author': 'A. Writer'})
        self.assertEqual(result['author'], 'A. Writer')
