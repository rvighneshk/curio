import unittest
from unittest import mock
from tests import helpers as h
import qloo_client as q

class QlooClientTests(unittest.TestCase):

    def setUp(self):
        q.clear_cache()
        patcher = mock.patch.object(q, '_sleep', lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_multi_seed_signal_exclusions_and_explainability_in_one_call(self):
        payload = h.insights_payload(h.qloo_entity('E1', 'Blade Runner', tags=['Sci-Fi'], affinity=0.8, drivers=[{'entity_id': 'S2', 'weight': 0.7}]))
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, payload)) as get:
            results = q.get_recommendations(['S1', 'S2', 'S1'], 'Movies', 10, exclude_ids=['X1', 'X2'], signal_names={'S1': 'Dune', 'S2': 'Arrival'})
        self.assertEqual(get.call_count, 1)
        params = get.call_args.kwargs['params']
        self.assertEqual(params['signal.interests.entities'], 'S1,S2')
        self.assertEqual(params['filter.exclude.entities'], 'X1,X2')
        self.assertEqual(params['feature.explainability'], 'true')
        self.assertEqual(params['filter.type'], 'urn:entity:movie')
        self.assertEqual(results[0]['qloo']['affinity'], 0.8)
        self.assertEqual(results[0]['qloo']['drivers'][0]['name'], 'Arrival')
        self.assertEqual(results[0]['tags'], ['Sci-Fi'])

    def test_signals_capped(self):
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, h.insights_payload())) as get:
            q.get_recommendations([f'S{i}' for i in range(20)], 'Books')
        self.assertEqual(len(get.call_args.kwargs['params']['signal.interests.entities'].split(',')), q.MAX_SIGNALS)

    def test_falls_back_when_optional_params_rejected(self):
        ok = h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))
        with mock.patch.object(q.requests, 'get', side_effect=[h.FakeResponse(400, text='bad param'), ok]) as get:
            results = q.get_recommendations(['S1'], 'Movies', exclude_ids=['X1'])
        self.assertEqual(len(results), 1)
        self.assertNotIn('feature.explainability', get.call_args_list[1].kwargs['params'])

    def test_retries_transient_errors_once(self):
        ok = h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))
        with mock.patch.object(q.requests, 'get', side_effect=[h.FakeResponse(503), ok]) as get:
            self.assertEqual(len(q.get_recommendations(['S1'], 'Movies', explain=False)), 1)
        self.assertEqual(get.call_count, 2)

    def test_gives_up_after_retry(self):
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(503)):
            with self.assertRaises(q.QlooError):
                q.get_recommendations(['S1'], 'Movies', explain=False)

    def test_responses_are_cached(self):
        payload = h.insights_payload(h.qloo_entity('E1', 'X'))
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, payload)) as get:
            q.get_recommendations(['S1'], 'Movies', explain=False)
            q.get_recommendations(['S1'], 'Movies', explain=False)
        self.assertEqual(get.call_count, 1)

    def test_seeds_are_never_returned_as_results(self):
        payload = h.insights_payload(h.qloo_entity('S1', 'Seed'), h.qloo_entity('E1', 'Other'))
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, payload)):
            results = q.get_recommendations(['S1'], 'Movies', explain=False)
        self.assertEqual([r['entity_id'] for r in results], ['E1'])

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict('os.environ', {'QLOO_API_KEY': ''}):
            with self.assertRaises(q.QlooError):
                q.search_entities('dune', 'Movies')

    def test_explainability_tolerates_odd_shapes(self):
        self.assertEqual(q._parse_explainability(None, {}), [])
        self.assertEqual(q._parse_explainability('junk', {}), [])
        drivers = q._parse_explainability([{'id': 'S1', 'score': 0.4}, {'id': 'S2'}, 'x'], {'S1': 'Dune', 'S2': 'Arrival'})
        self.assertEqual([d['name'] for d in drivers], ['Dune', 'Arrival'])

    def test_normalize_tags_are_readable_names(self):
        entity = h.qloo_entity('E1', 'X', tags=['Sci-Fi', 'sci-fi', 'Drama'])
        self.assertEqual(q.normalize_entity(entity, 'Movies')['tags'], ['Sci-Fi', 'Drama'])

    def test_normalize_book_author_and_metadata(self):
        entity = h.qloo_entity('B1', 'The Example', etype='urn:entity:book')
        entity['properties'].update({'authors': [{'value': 'A. Writer'}, {'name': 'B. Writer'}], 'publication_year': [{'value': '2024'}], 'page_count': [{'value': 320}], 'format': [{'value': 'Paperback'}], 'isbn13': [{'value': '9781234567890'}]})
        normalized = q.normalize_entity(entity, 'Books')
        self.assertEqual(normalized['author'], 'A. Writer, B. Writer')
        self.assertEqual(normalized['publication_year'], '2024')
        self.assertEqual(normalized['page_count'], 320)
        self.assertEqual(normalized['format'], 'Paperback')
        self.assertEqual(normalized['isbn13'], '9781234567890')

    def test_plain_string_description_is_cleaned(self):
        entity = h.qloo_entity('B1', 'Interstellar', etype='urn:entity:book')
        entity['properties'].pop('short_descriptions', None)
        entity['properties']['description'] = '<b>THE END OF EARTH</b><br /><br />A <i>story</i> &amp; journey.'
        normalized = q.normalize_entity(entity, 'Books')
        self.assertEqual(normalized['description'], 'THE END OF EARTH A story & journey.')
        self.assertEqual(normalized['short_description'], 'THE END OF EARTH A story & journey.')

    def test_short_description_accepts_language_field(self):
        entity = h.qloo_entity('B1', 'The Example', etype='urn:entity:book')
        entity['properties']['short_descriptions'] = [{'language': 'de', 'value': 'Deutsche Beschreibung'}, {'language': 'en', 'value': 'An English description.'}]
        normalized = q.normalize_entity(entity, 'Books')
        self.assertEqual(normalized['description'], 'An English description.')

    def test_plain_description_used_when_short_description_has_no_english_entry(self):
        entity = h.qloo_entity('B1', 'The Example', etype='urn:entity:book')
        entity['properties']['short_descriptions'] = [{'language': 'de', 'value': 'Deutsche Beschreibung'}]
        entity['properties']['description'] = 'English full description.'
        normalized = q.normalize_entity(entity, 'Books')
        self.assertEqual(normalized['description'], 'English full description.')

    def test_search_requires_known_category(self):
        with self.assertRaises(ValueError):
            q.search_entities('x', 'Games')

    def test_popularity_bands_and_goal_tags_become_query_parameters(self):
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))) as get:
            q.get_recommendations(['S1'], 'Movies', explain=False, popularity='gems', signal_tags=['urn:tag:cozy', 'urn:tag:funny'])
        params = get.call_args.kwargs['params']
        self.assertEqual(params['filter.popularity.max'], q.GEMS_MAX)
        self.assertEqual(params['signal.interests.tags'], 'urn:tag:cozy,urn:tag:funny')
        q.clear_cache()
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))) as get:
            q.get_recommendations(['S1'], 'Movies', explain=False, popularity='popular')
        self.assertEqual(get.call_args.kwargs['params']['filter.popularity.min'], q.POPULAR_MIN)

    def test_empty_popularity_filter_is_relaxed_and_reported(self):
        empty, full = (h.FakeResponse(200, h.insights_payload()), h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X'))))
        meta = {}
        with mock.patch.object(q.requests, 'get', side_effect=[empty, full]) as get:
            results = q.get_recommendations(['S1'], 'Movies', explain=False, popularity='gems', meta=meta)
        self.assertEqual(len(results), 1)
        self.assertEqual(meta['dropped'], ['popularity'])
        self.assertNotIn('filter.popularity.max', get.call_args_list[1].kwargs['params'])

    def test_rejected_parameters_are_dropped_in_order_and_reported(self):
        ok = h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))
        meta = {}
        with mock.patch.object(q.requests, 'get', side_effect=[h.FakeResponse(400, text='no'), h.FakeResponse(400, text='no'), ok]):
            q.get_recommendations(['S1'], 'Movies', signal_tags=['t'], meta=meta)
        self.assertEqual(meta['dropped'], ['explainability', 'goal'])

    def test_nothing_dropped_when_all_accepted(self):
        meta = {}
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, h.insights_payload(h.qloo_entity('E1', 'X')))):
            q.get_recommendations(['S1'], 'Movies', meta=meta)
        self.assertEqual(meta['dropped'], [])

    def test_tag_search_parses_common_shapes_and_never_raises(self):
        shapes = [{'results': {'tags': [{'id': 'urn:tag:cozy', 'name': 'Cozy'}]}}, {'results': [{'tag_id': 'urn:tag:cozy', 'name': 'Cozy'}]}, {'tags': [{'id': 'urn:tag:cozy', 'name': 'Cozy'}]}]
        for shape in shapes:
            q.clear_cache()
            with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(200, shape)):
                self.assertEqual(q.search_tags('cozy'), [{'tag_id': 'urn:tag:cozy', 'name': 'Cozy'}])
        q.clear_cache()
        with mock.patch.object(q.requests, 'get', return_value=h.FakeResponse(500)):
            self.assertEqual(q.search_tags('cozy'), [])
        self.assertEqual(q.search_tags('   '), [])
if __name__ == '__main__':
    unittest.main()
