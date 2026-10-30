import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QLOO_API_KEY', 'test-key')
os.environ.setdefault('CURIO_DB_PATH', ':memory:')

def qloo_entity(entity_id, name, etype='urn:entity:movie', tags=(), affinity=None, drivers=None):
    entity = {'entity_id': entity_id, 'name': name, 'type': etype, 'popularity': 0.9, 'tags': [{'id': f'urn:tag:{t.lower()}', 'name': t} for t in tags], 'properties': {'short_descriptions': [{'value': f'About {name}.', 'languages': ['en']}]}}
    query = {}
    if affinity is not None:
        query['affinity'] = affinity
    if drivers:
        query['explainability'] = {'signal.interests.entities': drivers}
    if query:
        entity['query'] = query
    return entity

def insights_payload(*entities):
    return {'results': {'entities': list(entities)}}

def item(entity_id, name, category, tags=()):
    return {'entity_id': entity_id, 'name': name, 'category': category, 'tags': list(tags), 'description': ''}

class FakeResponse:

    def __init__(self, status=200, payload=None, text=''):
        self.status_code, self._payload, self.text = (status, payload if payload is not None else {}, text)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload
