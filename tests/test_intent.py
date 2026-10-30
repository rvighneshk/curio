import unittest
from unittest import mock
from tests import helpers as h
import intent
import qloo_client

class IntentParsingTests(unittest.TestCase):

    def test_fallback_parser_restricts_memory_lane_categories(self):
        with mock.patch.object(intent.taste_agent, 'gemini_configured', return_value=False):
            parsed = intent.parse_vibe_prompt('Keep it cheerful with familiar music and classic television', mode='memory', use_gemini=True)
        self.assertEqual(parsed['parser'], 'deterministic')
        self.assertTrue(set(parsed['categories']) <= set(intent.MEMORY_CATEGORIES))
        self.assertIn('Artists', parsed['categories'])
        self.assertIn('TV Shows', parsed['categories'])
        self.assertTrue(parsed['seed_terms'])
        self.assertIn('Keep it cheerful with familiar music and classic television', parsed['seed_terms'][0])

    def test_gemini_result_is_sanitized_to_allowed_categories(self):
        reply = {'mood': [' wistful ', 'warm'], 'themes': ['family stories'], 'categories': ['Books', 'Podcasts', 'Unsupported'], 'seed_terms': ['quiet science fiction'], 'constraints': ['not too mainstream'], 'discovery_goal': 'cross-media', 'parser': 'gemini'}
        with mock.patch.object(intent.taste_agent, 'gemini_configured', return_value=True), mock.patch.object(intent.taste_agent, 'interpret_vibe_prompt', return_value=reply):
            parsed = intent.parse_vibe_prompt('A gentle story and podcast', mode='personal', use_gemini=True)
        self.assertEqual(parsed['parser'], 'gemini')
        self.assertEqual(parsed['categories'], ['Podcasts'])
        self.assertEqual(parsed['mood'], ['wistful', 'warm'])

    def test_explicit_horror_genre_is_a_hard_constraint(self):
        with mock.patch.object(intent.taste_agent, 'gemini_configured', return_value=False):
            parsed = intent.parse_vibe_prompt('horror movies', mode='personal', use_gemini=False)
        self.assertEqual(parsed['categories'], ['Movies'])
        self.assertEqual(parsed['required_genres'], ['horror'])

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(ValueError):
            intent.parse_vibe_prompt('   ')

    def test_seed_resolution_returns_only_qloo_entities(self):
        fake = [{'entity_id': 'book-1', 'name': 'Dune', 'category': 'Books', 'tags': ['Science Fiction']}]
        parsed = {'categories': ['Books'], 'seed_terms': ['science fiction']}
        with mock.patch.object(intent.qloo_client, 'search_entities', return_value=fake) as search:
            seeds = intent.resolve_qloo_seeds(parsed)
        search.assert_called_once_with('science fiction', 'Books', 8)
        self.assertEqual([x['entity_id'] for x in seeds], ['book-1'])
        self.assertEqual(seeds[0]['category'], 'Books')

    def test_reference_title_is_preserved_and_resolved_before_supporting_seeds(self):
        with mock.patch.object(intent.taste_agent, 'gemini_configured', return_value=False):
            parsed = intent.parse_vibe_prompt('Give me 5 movie recommendations for a night in that feel like The Conjuring', mode='personal', use_gemini=False)
        self.assertEqual(parsed['categories'], ['Movies'])
        self.assertEqual(parsed['reference_terms'], ['The Conjuring'])
        self.assertEqual(parsed['requested_count'], 5)
        reference = {'entity_id': 'movie-conjuring', 'name': 'The Conjuring', 'category': 'Movies', 'tags': ['Horror']}
        supporting = {'entity_id': 'movie-night', 'name': 'Night', 'category': 'Movies', 'tags': ['Night']}

        def search(term, category, limit):
            return [reference] if term == 'The Conjuring' else [supporting]
        with mock.patch.object(intent.qloo_client, 'search_entities', side_effect=search):
            seeds = intent.resolve_qloo_seeds({'categories': ['Movies'], 'reference_terms': ['The Conjuring'], 'seed_terms': ['supernatural horror']})
        self.assertEqual(seeds[0]['entity_id'], 'movie-conjuring')
        self.assertEqual(seeds[0]['vibe_role'], 'reference')
        self.assertEqual(seeds[1]['vibe_role'], 'supporting')

    def test_gemini_reference_fields_are_returned(self):
        reply = {'mood': ['eerie'], 'themes': ['paranormal investigation'], 'categories': ['Movies'], 'seed_terms': ['supernatural horror films'], 'reference_terms': ['The Conjuring'], 'reference_attributes': ['haunted locations', 'paranormal investigation', 'suspense'], 'required_genres': ['horror'], 'requested_count': 5, 'parser': 'gemini'}
        with mock.patch.object(intent.taste_agent, 'gemini_configured', return_value=True), mock.patch.object(intent.taste_agent, 'interpret_vibe_prompt', return_value=reply):
            parsed = intent.parse_vibe_prompt('Give me 5 movie recommendations that feel like The Conjuring', use_gemini=True)
        self.assertIn('The Conjuring', parsed['reference_terms'])
        self.assertIn('horror', parsed['required_genres'])
        self.assertIn('haunted locations', parsed['reference_attributes'])
if __name__ == '__main__':
    unittest.main()
