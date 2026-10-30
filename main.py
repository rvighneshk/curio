import json
import os
import ipaddress
import logging
import queue
import re
import time
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Literal
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import agent as discovery_agent
import qloo_client
import taste
import deeper
import memory
import taste_agent
import intent as vibe_intent
from qloo_client import ENTITY_TYPES, QlooBadRequest, QlooError, search_entities
from ratelimit import DailyCap, RateLimiter
from store import SessionStore
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv('CURIO_DB_PATH', str(BASE_DIR / 'data' / 'curio.db'))
TRUST_PROXY = os.getenv('CURIO_TRUST_PROXY', '0') == '1'
app = FastAPI(title='Curio API', version='1.0.0', description='Qloo-grounded cultural discovery.')
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
_origins = [o.strip() for o in os.getenv('CURIO_CORS_ORIGINS', '').split(',') if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=False, allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'], allow_headers=['Content-Type', 'X-Session-ID'])
qloo_limiter = RateLimiter(int(os.getenv('CURIO_QLOO_RPM', '60')))
ai_limiter = RateLimiter(int(os.getenv('CURIO_AI_RPM', '10')))
ai_daily = DailyCap(int(os.getenv('CURIO_AI_DAILY_CAP', '500')))

def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get('x-forwarded-for', '')
        if forwarded:
            for candidate in forwarded.split(','):
                candidate = candidate.strip()
                try:
                    return str(ipaddress.ip_address(candidate))
                except ValueError:
                    continue
    return request.client.host if request.client else 'unknown'

def rate_qloo(request: Request) -> None:
    if not qloo_limiter.allow(f'q:{client_ip(request)}'):
        raise HTTPException(status_code=429, detail='Too many requests. Wait a moment and try again.')

def rate_ai(request: Request) -> None:
    if not ai_limiter.allow(f'a:{client_ip(request)}'):
        raise HTTPException(status_code=429, detail='Too many AI requests. Wait a minute and try again.')

@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response
_store = SessionStore(DB_PATH)
_store.prune(int(os.getenv('CURIO_SESSION_TTL_DAYS', '30')))
_sessions: dict[str, dict[str, Any]] = {}
_state_lock = RLock()
SESSION_ID_RE = re.compile('^[A-Za-z0-9_.-]{24,128}$')
MAX_CACHED_SESSIONS = 300
MAX_EXCLUSIONS = 500

def new_session() -> dict[str, Any]:
    return {'favourites': {category: [] for category in ENTITY_TYPES}, 'exclusions': {}, 'collection': {'liked': [], 'saved': [], 'skipped_ids': []}, 'journeys': {}, 'active_parents': {}, 'profile_cache': None, 'memory': memory.new_state()}

def get_session_id(session_id: str | None) -> str:
    value = (session_id or '').strip()
    if not SESSION_ID_RE.match(value):
        raise HTTPException(status_code=400, detail='Send a client-generated X-Session-ID header (24-128 letters, digits, - _ .).')
    return value

def session(session_id: str) -> dict[str, Any]:
    cached = _sessions.get(session_id)
    if cached is not None:
        return cached
    data = new_session()
    data.update({k: v for k, v in (_store.get(session_id) or {}).items() if k in data})
    for category in ENTITY_TYPES:
        data['favourites'].setdefault(category, [])
    for key in ('liked', 'saved', 'skipped_ids'):
        data['collection'].setdefault(key, [])
    if len(_sessions) >= MAX_CACHED_SESSIONS:
        _sessions.pop(next(iter(_sessions)), None)
    _sessions[session_id] = data
    return data

def save(session_id: str) -> None:
    _store.put(session_id, _sessions[session_id])

def exclusions(s: dict[str, Any], journey_id: str) -> list[str]:
    return s['exclusions'].setdefault(journey_id, [])

def add_exclusion(s: dict[str, Any], journey_id: str, entity_id: str) -> None:
    items = exclusions(s, journey_id)
    if entity_id not in items:
        items.append(entity_id)
        del items[:-MAX_EXCLUSIONS]

def journey_nodes(s: dict[str, Any], journey_id: str) -> list[dict[str, Any]]:
    return s['journeys'].setdefault(journey_id, [])

def all_favourites(s: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for items in s['favourites'].values() for item in items]

def find_item(s: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
    pools = [all_favourites(s), s['collection']['liked'], *s['journeys'].values()]
    for pool in pools:
        for item in pool:
            if str(item.get('entity_id')) == entity_id:
                return item
    return None

def item_key(item: dict[str, Any]) -> str:
    return str(item.get('entity_id') or f"{item.get('category', '')}:{item.get('name', '')}".casefold())

def add_unique_item(items: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    key = item_key(item)
    if not key or any((item_key(existing) == key for existing in items)):
        return False
    items.append(item)
    return True

def make_journey_node(entity: dict[str, Any], parent_id: str | None=None, feedback: str | None=None) -> dict[str, Any]:
    entity_id = str(entity.get('entity_id') or '').strip()
    name = str(entity.get('name') or '').strip()
    if not entity_id or not name:
        raise HTTPException(status_code=422, detail='Journey entities must include entity_id and name.')
    return {'entity_id': entity_id, 'name': name, 'type': entity.get('type') or entity.get('category') or 'unknown', 'category': entity.get('category') or '', 'parent_id': parent_id, 'description': entity.get('description') or '', 'short_description': entity.get('short_description') or '', 'image': entity.get('image') or '', 'tags': taste.tags_of(entity), 'ratings': entity.get('ratings') or {}, 'provider_links': entity.get('provider_links') or [], 'review_links': entity.get('review_links') or [], 'feedback': feedback}

def journey_graph(journey: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    valid_ids = {node.get('entity_id') for node in journey}
    nodes = [{'id': n.get('entity_id'), 'label': n.get('name'), 'type': n.get('type'), 'category': n.get('category'), 'feedback': n.get('feedback'), 'image': n.get('image')} for n in journey]
    edges = [{'source': n.get('parent_id'), 'target': n.get('entity_id')} for n in journey if n.get('parent_id') and n.get('parent_id') in valid_ids]
    return {'nodes': nodes, 'edges': edges}

def ensure_tags(session_id: str) -> None:
    s = session(session_id)
    missing = [i for i in all_favourites(s) if not i.get('tags')]
    if not missing:
        return
    found = {e['entity_id']: e for e in qloo_client.get_entities([i['entity_id'] for i in missing])}
    changed = False
    for item in missing:
        tags = found.get(item['entity_id'], {}).get('tags')
        if tags:
            item['tags'] = tags
            changed = True
    if changed:
        save(session_id)

class RecommendationInput(BaseModel):
    seed_entity_id: str = Field(min_length=1, max_length=300)
    seed_category: str
    category: str
    journey_id: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=10, ge=1, le=10)
    exclude_ids: list[str] = Field(default_factory=list, max_length=200)
    popularity: Literal['any', 'gems', 'popular'] = 'any'
    trending: bool = False

class RecommendationActionInput(BaseModel):
    entity_id: str = Field(min_length=1, max_length=300)
    action: str
    journey_id: str = Field(default='default', min_length=1, max_length=300)
    parent_id: str | None = Field(default=None, max_length=300)
    entity: dict[str, Any] | None = None
    seed_entity: dict[str, Any] | None = None

class TasteProfileInput(BaseModel):
    journey_id: str | None = Field(default=None, max_length=300)
    refresh: bool = False

class DeeperPathInput(BaseModel):
    seed_entity_id: str | None = Field(default=None, max_length=300)
    seed_category: str | None = None
    source: Literal['curio', 'memory'] = 'curio'
    journey_id: str | None = Field(default=None, max_length=300)
    hops: int = Field(default=5, ge=2, le=6)
    popularity: Literal['any', 'gems', 'popular'] = 'any'

class AgentPathInput(BaseModel):
    goal: str | None = Field(default=None, max_length=200)

class VibeCheckInput(BaseModel):
    prompt: str = Field(min_length=3, max_length=vibe_intent.MAX_PROMPT_CHARS)

class MemoryRunInput(BaseModel):
    birth_year: int = Field(ge=1900, le=2100)
    region: str = Field(default='', max_length=120)
    seeds: list[dict[str, Any]] = Field(min_length=1, max_length=memory.MAX_SEEDS)
    adapt: bool = False

class MemoryReactionInput(BaseModel):
    entity: dict[str, Any]
    reaction: Literal['love', 'ok', 'upset']

class FavouriteInput(BaseModel):
    entity_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=300)
    category: str
    type: str = ''
    description: str = ''
    short_description: str = ''
    image: str = ''
    tags: list[Any] = Field(default_factory=list)
    ratings: dict[str, Any] = Field(default_factory=dict)
    provider_links: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    review_links: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    external: dict[str, Any] = Field(default_factory=dict)

@app.get('/')
def home(request: Request):
    return templates.TemplateResponse(request=request, name='index.html', context={})

@app.get('/health')
def health() -> dict[str, Any]:
    return {'status': 'ok', 'service': 'curio-api', 'qloo_configured': bool(os.getenv('QLOO_API_KEY')), 'gemini_configured': taste_agent.gemini_configured()}

@app.get('/api/categories')
def categories() -> dict[str, list[str]]:
    return {'categories': list(ENTITY_TYPES)}

@app.get('/api/search', dependencies=[Depends(rate_qloo)])
def search(q: str=Query(min_length=1, max_length=160), category: str=Query(), limit: int=Query(default=10, ge=1, le=20)) -> dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail='Search query cannot be blank.')
    if category not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail={'message': 'Unsupported category.', 'allowed_categories': list(ENTITY_TYPES)})
    try:
        results = search_entities(query, category, limit)
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    results = [taste.compact_entity(r) | {'category': r.get('category')} for r in results]
    return {'query': query, 'category': category, 'count': len(results), 'results': results}

@app.get('/api/onboarding/favourites')
def get_favourites(x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        favourites = session(sid)['favourites']
        return {'favourites': favourites, 'count': sum(map(len, favourites.values()))}

@app.post('/api/onboarding/favourites')
def add_favourite(favourite: FavouriteInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    if favourite.category not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail={'message': 'Unsupported category.', 'allowed_categories': list(ENTITY_TYPES)})
    item = taste.compact_entity(favourite.model_dump())
    item['category'] = favourite.category
    if not item['entity_id'].strip() or not item['name'].strip():
        raise HTTPException(status_code=422, detail='Favourite must have an entity ID and name.')
    with _state_lock:
        favourites = session(sid)['favourites']
        category_items = favourites[favourite.category]
        existing = next((e for e in category_items if e['entity_id'] == item['entity_id']), None)
        if existing:
            return {'status': 'already_selected', 'favourite': existing, 'favourites': favourites}
        if len(category_items) >= 3:
            raise HTTPException(status_code=409, detail=f'You can select up to 3 favourites in {favourite.category}.')
        category_items.append(item)
        save(sid)
        return {'status': 'added', 'favourite': item, 'favourites': favourites}

@app.delete('/api/onboarding/favourites/{category}/{entity_id}')
def remove_favourite(category: str, entity_id: str, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    if category not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail='Unsupported category.')
    with _state_lock:
        favourites = session(sid)['favourites']
        before = len(favourites[category])
        favourites[category] = [i for i in favourites[category] if i['entity_id'] != entity_id]
        if len(favourites[category]) == before:
            raise HTTPException(status_code=404, detail='Favourite not found in this session.')
        save(sid)
        return {'status': 'removed', 'favourites': favourites}

@app.post('/api/recommendations', dependencies=[Depends(rate_qloo)])
def recommendations(request: RecommendationInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    for label, value in (('seed', request.seed_category), ('recommendation', request.category)):
        if value not in ENTITY_TYPES:
            raise HTTPException(status_code=422, detail={'message': f'Unsupported {label} category.', 'allowed_categories': list(ENTITY_TYPES)})
    seed_id = request.seed_entity_id.strip()
    if not seed_id:
        raise HTTPException(status_code=422, detail='Seed entity ID cannot be blank.')
    journey_id = (request.journey_id or seed_id).strip()
    with _state_lock:
        s = session(sid)
        favourites = all_favourites(s)
        seed = find_item(s, seed_id) or {'entity_id': seed_id, 'name': seed_id, 'category': request.seed_category}
        signals = [seed]
        blocked = list(dict.fromkeys([*request.exclude_ids, *exclusions(s, journey_id), *(str(i['entity_id']) for i in favourites), seed_id]))
    names = {str(i.get('entity_id')): str(i.get('name')) for i in signals}
    meta: dict[str, Any] = {}
    try:
        candidates = qloo_client.get_recommendations([seed_id], request.category, min(request.limit * 3, 20), exclude_ids=blocked, explain=True, trending=request.trending, signal_names=names, popularity=None if request.popularity == 'any' else request.popularity, meta=meta)
    except QlooBadRequest as exc:
        message = str(exc).casefold()
        invalid_seed = 'signal.interests.entities' in message and ('invalid' in message or 'valid signal' in message)
        if invalid_seed:
            return {'seed_entity_id': seed_id, 'seed_category': request.seed_category, 'category': request.category, 'popularity': request.popularity, 'relaxed': [], 'count': 0, 'limit': request.limit, 'signals': [{'entity_id': i.get('entity_id'), 'name': i.get('name'), 'category': i.get('category')} for i in signals], 'results': [], 'notice': "This title isn't connecting to Qloo's taste graph right now. Try another portal or come back to this one later."}
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    results, seen = ([], set(blocked))
    for item in candidates:
        entity_id = item.get('entity_id')
        if not entity_id or entity_id in seen or item.get('category') != request.category:
            continue
        seen.add(entity_id)
        item['evidence'] = taste.build_evidence(signals, item)
        results.append(taste.compact_entity(item))
    results = taste.prefer_on_topic(seed, results)[:request.limit]
    return {'seed_entity_id': seed_id, 'seed_category': request.seed_category, 'category': request.category, 'popularity': request.popularity, 'relaxed': meta.get('dropped', []), 'count': len(results), 'limit': request.limit, 'signals': [{'entity_id': i.get('entity_id'), 'name': i.get('name'), 'category': i.get('category')} for i in signals], 'results': results}

@app.post('/api/recommendations/actions')
def recommendation_action(request: RecommendationActionInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    entity_id = request.entity_id.strip()
    journey_id = request.journey_id.strip()
    if not entity_id or not journey_id:
        raise HTTPException(status_code=422, detail='Entity ID and journey ID cannot be blank.')
    if request.action not in {'skip', 'like', 'save', 'explore', 'remove', 'unlike'}:
        raise HTTPException(status_code=422, detail='Action must be skip, like, save, explore, unlike or remove.')
    with _state_lock:
        s = session(sid)
        collection = s['collection']
        journey = journey_nodes(s, journey_id)
        parent_id = request.parent_id or s['active_parents'].get(journey_id)
        if request.action == 'remove':
            if entity_id in exclusions(s, journey_id):
                exclusions(s, journey_id).remove(entity_id)
            save(sid)
            return {'status': 'updated', 'entity_id': entity_id, 'action': request.action, 'journey_id': journey_id}
        if request.action == 'unlike':
            collection['liked'] = [i for i in collection['liked'] if str(i.get('entity_id')) != entity_id]
            removed = next((n for n in journey if str(n.get('entity_id')) == entity_id), None)
            if removed:
                for node in journey:
                    if str(node.get('parent_id')) == entity_id:
                        node['parent_id'] = removed.get('parent_id')
                journey[:] = [n for n in journey if str(n.get('entity_id')) != entity_id]
            save(sid)
            return {'status': 'unliked', 'entity_id': entity_id, 'journey_id': journey_id, 'collection': collection, 'journey': journey}
        add_exclusion(s, journey_id, entity_id)
        if request.action == 'skip':
            if entity_id not in collection['skipped_ids']:
                collection['skipped_ids'].append(entity_id)
            for node in journey:
                if node['entity_id'] == entity_id:
                    node['feedback'] = 'skipped'
            save(sid)
            return {'status': 'skipped', 'entity_id': entity_id, 'journey_id': journey_id}
        entity = request.entity
        if not isinstance(entity, dict):
            raise HTTPException(status_code=422, detail="This action requires the full entity object in 'entity'.")
        if str(entity.get('entity_id')) != entity_id:
            raise HTTPException(status_code=422, detail='entity.entity_id must match entity_id.')
        entity = taste.compact_entity(entity)

        def ensure_parent() -> None:
            if parent_id and (not any((n['entity_id'] == parent_id for n in journey))):
                seed_entity = request.seed_entity
                if isinstance(seed_entity, dict) and str(seed_entity.get('entity_id')) == parent_id:
                    journey.append(make_journey_node(taste.compact_entity(seed_entity)))
                else:
                    raise HTTPException(status_code=422, detail='Parent must already exist in this journey or be provided as seed_entity.')
        if request.action == 'like':
            add_unique_item(collection['liked'], entity)
            existing = next((n for n in journey if n['entity_id'] == entity_id), None)
            if existing:
                existing['feedback'] = 'liked'
            else:
                ensure_parent()
                journey.append(make_journey_node(entity, parent_id=parent_id, feedback='liked'))
            save(sid)
            return {'status': 'liked', 'entity_id': entity_id, 'journey_id': journey_id, 'collection': collection, 'journey': journey, 'graph': journey_graph(journey)}
        if request.action == 'save':
            add_unique_item(collection['saved'], entity)
            save(sid)
            return {'status': 'saved', 'entity_id': entity_id, 'journey_id': journey_id, 'collection': collection}
        if not any((n['entity_id'] == entity_id for n in journey)):
            ensure_parent()
            journey.append(make_journey_node(entity, parent_id=parent_id))
        s['active_parents'][journey_id] = entity_id
        save(sid)
        return {'status': 'exploring', 'entity_id': entity_id, 'journey_id': journey_id, 'active_parent_id': entity_id, 'journey': journey, 'graph': journey_graph(journey)}

@app.post('/api/session/reset')
def reset_journey(x_session_id: str | None=Header(default=None)) -> dict[str, str]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        s = session(sid)
        fresh = new_session()
        for key in ('exclusions', 'collection', 'journeys', 'active_parents'):
            s[key] = fresh[key]
        save(sid)
    return {'status': 'reset'}

@app.get('/api/collection')
def get_collection(x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        c = session(sid)['collection']
        return {'liked': c['liked'], 'saved': c['saved'], 'skipped_ids': c['skipped_ids'], 'liked_count': len(c['liked']), 'saved_count': len(c['saved'])}

@app.get('/api/journey/{journey_id}')
def get_journey(journey_id: str, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    jid = journey_id.strip()
    if not jid:
        raise HTTPException(status_code=422, detail='Journey ID cannot be blank.')
    with _state_lock:
        s = session(sid)
        journey = journey_nodes(s, jid)
        return {'journey_id': jid, 'journey': journey, 'graph': journey_graph(journey), 'active_parent_id': s['active_parents'].get(jid)}

def _journey_for(s: dict[str, Any], journey_id: str | None) -> list[dict[str, Any]]:
    if journey_id:
        return list(journey_nodes(s, journey_id))
    return [node for nodes in s['journeys'].values() for node in nodes]

def _build_taste_profile(request: TasteProfileInput, sid: str, progress=None) -> dict[str, Any]:

    def log(title: str, detail: str) -> None:
        if progress:
            progress(title, detail)
    log('Checking your cultural signals', "Loading onboarding favourites and the selected journey's liked discoveries.")
    with _state_lock:
        if not all_favourites(session(sid)):
            raise HTTPException(status_code=409, detail='Add at least one onboarding favourite before generating a taste profile.')
    log('Identifying recurring themes', 'Checking Qloo tags and metadata for patterns across your favourites.')
    ensure_tags(sid)
    with _state_lock:
        s = session(sid)
        favourites = list(all_favourites(s))
        journey = _journey_for(s, request.journey_id)
        collection_liked = list(s['collection']['liked'])
        journey_liked = [n for n in journey if n.get('feedback') == 'liked']
        liked_by_id: dict[str, dict[str, Any]] = {}
        for item in [*collection_liked, *journey_liked]:
            entity_id = str(item.get('entity_id') or '').strip()
            fallback_key = f"{item.get('category', '')}:{str(item.get('name') or '').casefold()}"
            key_id = entity_id or fallback_key
            if key_id and key_id not in liked_by_id:
                liked_by_id[key_id] = item
        liked = list(liked_by_id.values())
        key = taste.profile_key(favourites, liked, journey)
        cache = s.get('profile_cache')
    log('Mapping your taste signature', f'Comparing {len(favourites)} favourites, {len(collection_liked)} collection likes, and {len(journey_liked)} journey-liked nodes; {len(liked)} unique liked entities across {len(journey)} journey nodes.')
    signature = taste.taste_signature(favourites, liked)
    if not request.refresh and isinstance(cache, dict) and (cache.get('key') == key) and cache.get('profile'):
        log('Restoring saved interpretation', 'Your taste inputs have not changed, so the previously generated profile can be reused.')
        return {'profile': cache['profile'], 'signature': signature, 'source': 'gemini', 'cached': True, 'interpretation_only': True}
    profile, origin = (taste.fallback_profile(signature), 'qloo')
    if taste_agent.gemini_configured() and ai_daily.allow():
        log('Interpreting recurring themes', 'Gemini is summarizing the supplied entities and Qloo tags; it cannot add entities.')
        try:
            generated = taste_agent.create_taste_profile(favourites, journey, progress=log)
            if generated.get('signals') and generated.get('title'):
                profile, origin = (generated, 'gemini')
                log('Validating your taste map', f"Checking {len(generated.get('signals', []))} signals and {len(generated.get('connections', []))} connections against your supplied discoveries.")
            else:
                log('Using Qloo-grounded fallback', 'Gemini returned an incomplete profile, so the profile is built from the available Qloo signature.')
        except taste_agent.TasteAgentError as exc:
            log('Using Qloo-grounded fallback', f'Gemini was unavailable ({str(exc)[:180]}). The profile is built from the available Qloo signature.')
    else:
        log('Using Qloo-grounded fallback', 'Gemini is not configured or the AI usage limit was reached; using the available Qloo signature.')
    if origin == 'gemini':
        with _state_lock:
            session(sid)['profile_cache'] = {'key': key, 'profile': profile, 'saved_at': time.time()}
            save(sid)
        log('Saving the interpretation', 'The generated profile is cached for reuse until your taste inputs change.')
    log('Taste profile ready', f"Prepared {len(profile.get('signals', []))} signals and {len(profile.get('connections', []))} connections from {origin}.")
    return {'profile': profile, 'signature': signature, 'source': origin, 'cached': False, 'interpretation_only': origin == 'gemini'}

@app.post('/api/gemini/taste-profile', dependencies=[Depends(rate_ai)])
def taste_profile(request: TasteProfileInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    return _build_taste_profile(request, get_session_id(x_session_id))

@app.post('/api/gemini/taste-profile/stream', dependencies=[Depends(rate_ai)])
def taste_profile_stream(request: TasteProfileInput, x_session_id: str | None=Header(default=None)):
    sid = get_session_id(x_session_id)
    return _event_stream(lambda progress: _build_taste_profile(request, sid, progress))

def _event_stream(operation):
    events: queue.Queue = queue.Queue()
    trace: list[dict[str, Any]] = []
    trace_lock = RLock()

    def progress(title: str, detail: str) -> None:
        entry = {'step': len(trace) + 1, 'title': str(title)[:120], 'detail': str(detail)[:320], 'at': time.time()}
        with trace_lock:
            trace.append(entry)
        events.put({'event': 'progress', 'title': entry['title'], 'detail': entry['detail'], 'step': entry['step']})

    def run() -> None:
        try:
            result = operation(progress)
            if isinstance(result, dict):
                with trace_lock:
                    result.setdefault('live_trace', list(trace))
            events.put({'event': 'result', 'data': result})
        except HTTPException as exc:
            events.put({'event': 'error', 'message': str(exc.detail), 'status': exc.status_code})
        except Exception:
            logging.getLogger('curio').exception('Unexpected error while handling a streamed request')
            events.put({'event': 'error', 'message': 'Something went wrong while Curio was processing this request. Please try again.', 'status': 500})
        finally:
            events.put(None)

    def generate():
        yield 'retry: 1500\n\n'
        thread = Thread(target=run, daemon=True)
        thread.start()
        while True:
            try:
                item = events.get(timeout=12)
            except queue.Empty:
                yield ': keep-alive\n\n'
                continue
            if item is None:
                break
            event_name = item.pop('event')
            yield f"event: {event_name}\ndata: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}\n\n"
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})

def _deeper_context(request: DeeperPathInput, sid: str, progress=None) -> tuple[dict[str, Any], list[str], str]:
    if request.source == 'memory':
        entity_id = str(request.seed_entity_id or '').strip()
        if not entity_id:
            raise HTTPException(status_code=422, detail='Choose a Memory Lane item to explore.')
        if progress:
            progress('Verifying the loved discovery', 'Checking that this item was marked Lit up and confirming its Qloo entity.')
        with _state_lock:
            s = session(sid)
            reaction_row = s['memory'].get('reactions', {}).get(entity_id)
            if not reaction_row or reaction_row.get('reaction') != 'love':
                raise HTTPException(status_code=409, detail='Mark this Memory Lane item as Lit up before taking it deeper.')
            stored = dict(reaction_row.get('entity') or {})
            category = str(stored.get('category') or request.seed_category or '')
            if request.seed_category and category != request.seed_category:
                raise HTTPException(status_code=422, detail="The selected item's category does not match its saved reaction.")
            if category not in qloo_client.ENTITY_TYPES:
                raise HTTPException(status_code=422, detail='This item category cannot be explored with Qloo.')
            if str(stored.get('entity_id') or '') != entity_id or not stored.get('name'):
                raise HTTPException(status_code=422, detail='The saved Memory Lane item is incomplete. Please react to it again.')
            journey_id = (request.journey_id or f'memory:{entity_id}').strip()
            memory_state = s['memory']
            memory_blocked = [*[str(value) for value in memory_state.get('shown', [])], *[str(key) for key in memory_state.get('reactions', {}).keys()]]
            blocked = [*s['collection']['skipped_ids'], *exclusions(s, journey_id), *[str(item.get('entity_id')) for item in all_favourites(s)], *[str(item.get('entity_id')) for item in s['collection'].get('liked', [])], *memory_blocked]
        try:
            canonical_rows = qloo_client.get_entities([entity_id])
            canonical = next((item for item in canonical_rows if str(item.get('entity_id')) == entity_id), None)
            if canonical is None:
                matches = qloo_client.search_entities(str(stored['name']), category, limit=10)
                canonical = next((item for item in matches if str(item.get('entity_id')) == entity_id), None)
        except QlooError as exc:
            raise HTTPException(status_code=502, detail='Qloo could not verify this starting item. Please try again.') from exc
        if canonical is None:
            raise HTTPException(status_code=422, detail='Qloo could not verify this starting item. Try another loved discovery.')
        if canonical.get('category') != category:
            raise HTTPException(status_code=422, detail='Qloo returned a different category for this item, so it cannot be used as the starting point.')
        start = taste.compact_entity({**stored, **canonical, 'category': category})
        blocked = [value for value in blocked if value and value != entity_id]
        return (start, blocked, journey_id)
    with _state_lock:
        s = session(sid)
        favourites = list(all_favourites(s))
        if not favourites:
            raise HTTPException(status_code=409, detail='Add at least one onboarding favourite before taking a deeper path.')
        start = (find_item(s, request.seed_entity_id) if request.seed_entity_id else None) or favourites[0]
        journey_id = (request.journey_id or start['entity_id']).strip()
        blocked = [*s['collection']['skipped_ids'], *exclusions(s, journey_id), *(str(i['entity_id']) for i in favourites)]
        start = dict(start)
    ensure_tags(sid)
    with _state_lock:
        start = dict(find_item(session(sid), str(start['entity_id'])) or start)
    return (start, blocked, journey_id)

def _run_deeper_path(request: DeeperPathInput, sid: str, progress=None) -> dict[str, Any]:
    start, blocked, _journey_id = _deeper_context(request, sid, progress)
    try:
        result = deeper.build_path(start, blocked, request.hops, popularity=None if request.popularity == 'any' else request.popularity, progress=progress)
    except deeper.DeeperPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result['steps'] and taste_agent.gemini_configured() and ai_daily.allow():
        if progress:
            progress('Explaining cultural bridges', 'Connecting the selected discoveries using their Qloo-grounded tags and metadata.')
        try:
            deeper.apply_narration(result, taste_agent.narrate_path(str(start.get('name', '')), deeper.narration_payload(result), taste.description_of(start)))
            result['bridge_source'] = 'gemini' if result.get('mode') == 'gemini' else 'qloo'
        except taste_agent.TasteAgentError:
            result['bridge_source'] = 'qloo'
            result['trace'].append({'step': len(result['trace']) + 1, 'tool': 'fallback', 'summary': 'Used Qloo-grounded tags and metadata for the cultural connections.'})
            if progress:
                progress('Explaining cultural bridges', 'Using Qloo tags and metadata to explain the connections.')
    else:
        result['bridge_source'] = 'qloo'
        if progress:
            progress('Explaining cultural bridges', 'Preparing explanations from Qloo results and available metadata.')
    result['starting_entity'] = taste.compact_entity(start)
    result['source_mode'] = request.source
    return result

@app.post('/api/deeper/path', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def deeper_path(request: DeeperPathInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    return _run_deeper_path(request, sid)

@app.post('/api/deeper/path/stream', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def deeper_path_stream(request: DeeperPathInput, x_session_id: str | None=Header(default=None)):
    sid = get_session_id(x_session_id)

    def operation(progress):
        result = _run_deeper_path(request, sid, progress)
        progress('Finalising your path', f"Prepared {len(result['steps'])} Qloo-backed discoveries.")
        return result
    return _event_stream(operation)

@app.post('/api/agent/path', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def agent_path(request: AgentPathInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        if not all_favourites(session(sid)):
            raise HTTPException(status_code=409, detail='Add at least one favourite so the agent has something to start from.')
    ensure_tags(sid)
    with _state_lock:
        s = session(sid)
        favourites, liked = (list(all_favourites(s)), list(s['collection']['liked']))
        skipped = list(s['collection']['skipped_ids'])
    use_gemini = taste_agent.gemini_configured() and ai_daily.allow()
    try:
        result = discovery_agent.run_discovery_agent(favourites, liked, skipped, goal=request.goal, use_gemini=use_gemini)
    except discovery_agent.AgentPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result

@app.post('/api/agent/path/stream', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def agent_path_stream(request: AgentPathInput, x_session_id: str | None=Header(default=None)):
    sid = get_session_id(x_session_id)

    def operation(progress):
        progress('Reading your cultural signals', 'Checking favourites, liked discoveries, and your optional goal.')
        with _state_lock:
            if not all_favourites(session(sid)):
                raise HTTPException(status_code=409, detail='Add at least one favourite so the agent has something to start from.')
        ensure_tags(sid)
        with _state_lock:
            s = session(sid)
            favourites, liked = (list(all_favourites(s)), list(s['collection']['liked']))
            skipped = list(s['collection']['skipped_ids'])
        use_gemini = taste_agent.gemini_configured() and ai_daily.allow()
        try:
            result = discovery_agent.run_discovery_agent(favourites, liked, skipped, goal=request.goal, use_gemini=use_gemini, progress=progress)
        except discovery_agent.AgentPlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except QlooError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result
    return _event_stream(operation)

def _run_vibe_check(request: VibeCheckInput, sid: str, progress=None) -> dict[str, Any]:
    prompt = request.prompt.strip()
    use_gemini = taste_agent.gemini_configured() and ai_daily.allow()
    if progress:
        progress('Interpreting your vibe', 'Turning your description into moods, themes, categories and Qloo search phrases.')
    try:
        parsed = vibe_intent.parse_vibe_prompt(prompt, mode='personal', use_gemini=use_gemini, progress=progress)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if progress:
        progress('Finding cultural starting points', 'Resolving the interpreted search phrases to real Qloo entities.')
    resolved = vibe_intent.resolve_qloo_seeds(parsed)
    with _state_lock:
        s = session(sid)
        favourites = list(all_favourites(s))
        liked = list(s['collection']['liked'])
        skipped = list(s['collection']['skipped_ids'])
    seeds = [*resolved, *favourites]
    if not seeds:
        raise HTTPException(status_code=422, detail="Qloo couldn't resolve a starting point from that description. Try naming a book, film, artist, show or podcast, or make the prompt more specific.")
    if progress:
        progress("Searching Qloo's cultural graph", f"Exploring {', '.join(parsed['categories'])} from {len(seeds)} grounded starting point(s).")
    try:
        qloo_goal_parts = [*parsed.get('required_genres', []), *parsed.get('reference_attributes', []), *parsed.get('mood', []), *parsed.get('themes', []), *parsed.get('reference_terms', [])]
        qloo_goal = ' '.join(dict.fromkeys((str(part).strip() for part in qloo_goal_parts if str(part).strip()))) or prompt
        result = discovery_agent.run_discovery_agent(seeds, liked, skipped, goal=qloo_goal, use_gemini=use_gemini, progress=progress, categories=parsed['categories'], required_genres=parsed.get('required_genres', []))
    except discovery_agent.AgentPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QlooError as exc:
        if parsed.get('required_genres'):
            genres = ', '.join(parsed['required_genres'])
            categories = ', '.join(parsed.get('categories') or [])
            detail = f"Qloo couldn't find suitable {genres} recommendations in {categories}. Try another description or a broader genre. Curio won't fill the list with unrelated picks."
            raise HTTPException(status_code=422, detail=detail) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result['intent'] = parsed
    if parsed.get('requested_media') == 'Songs / tracks' and 'Artists' in parsed.get('categories', []):
        result['category_note'] = "Qloo's supported music entity type is Artists, so these results are artists with a similar sound—not individual song tracks."
    result['seed_count'] = len(seeds)
    result['reference_count'] = len(resolved)
    return result

@app.post('/api/vibe-check', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def vibe_check(request: VibeCheckInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    return _run_vibe_check(request, get_session_id(x_session_id))

@app.post('/api/vibe-check/stream', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def vibe_check_stream(request: VibeCheckInput, x_session_id: str | None=Header(default=None)):
    sid = get_session_id(x_session_id)
    return _event_stream(lambda progress: _run_vibe_check(request, sid, progress))

def _clean_memory_seeds(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seeds = []
    for item in raw:
        if not isinstance(item, dict) or item.get('category') not in memory.MEMORY_CATEGORIES:
            continue
        if not item.get('entity_id') or not item.get('name'):
            continue
        seeds.append(taste.compact_entity(item) | {'category': item['category']})
    return seeds

def _run_memory(request: MemoryRunInput, sid: str, progress=None) -> dict[str, Any]:
    seeds = _clean_memory_seeds(request.seeds)
    if not seeds:
        raise HTTPException(status_code=422, detail='Pick at least one artist, show or film the person loved.')
    profile = {'birth_year': request.birth_year, 'region': request.region.strip()}
    with _state_lock:
        state = session(sid)['memory']
        if not request.adapt:
            state['reactions'], state['shown'] = ({}, [])
        reactions, shown = (dict(state['reactions']), list(state['shown']))
    use_gemini = taste_agent.gemini_configured() and ai_daily.allow()
    try:
        result = memory.run_memory_agent(profile, seeds, reactions, shown, use_gemini=use_gemini, progress=progress)
    except memory.MemoryLaneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    with _state_lock:
        state = session(sid)['memory']
        state.update(profile=profile, seeds=seeds)
        state['shown'] = list(dict.fromkeys([*state['shown'], *result.pop('shown_ids', [])]))[-300:]
        state['last_names'] = [i['entity']['name'] for i in [*result['music'], *result['screen']]]
        save(sid)
    result['reaction_counts'] = {r: sum((1 for v in reactions.values() if v.get('reaction') == r)) for r in memory.REACTIONS}
    return result

@app.get('/api/memory/presets')
def memory_presets() -> dict[str, Any]:
    return {'presets': [{'key': k, 'label': v['label'], 'blurb': v['blurb'], 'birth_year': v['birth_year']} for k, v in memory.PRESETS.items()]}

@app.post('/api/memory/preset/{key}', dependencies=[Depends(rate_qloo)])
def memory_preset(key: str) -> dict[str, Any]:
    try:
        return memory.resolve_preset(key)
    except memory.MemoryLaneError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QlooError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@app.post('/api/memory/session', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def memory_session(request: MemoryRunInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    return _run_memory(request, get_session_id(x_session_id))

@app.post('/api/memory/session/stream', dependencies=[Depends(rate_ai), Depends(rate_qloo)])
def memory_session_stream(request: MemoryRunInput, x_session_id: str | None=Header(default=None)):
    sid = get_session_id(x_session_id)
    return _event_stream(lambda progress: _run_memory(request, sid, progress))

@app.post('/api/memory/reaction')
def memory_reaction(request: MemoryReactionInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        state = session(sid)['memory']
        try:
            counts = memory.record_reaction(state, request.entity, request.reaction)
        except memory.MemoryLaneError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        save(sid)
    return {'status': 'recorded', 'reaction_counts': counts}

@app.post('/api/memory/baseline', dependencies=[Depends(rate_ai)])
def memory_baseline(request: MemoryRunInput, x_session_id: str | None=Header(default=None)) -> dict[str, Any]:
    sid = get_session_id(x_session_id)
    if not (taste_agent.gemini_configured() and ai_daily.allow()):
        return {'available': False, 'note': "Gemini isn't available right now, so there's no LLM-only baseline to compare against."}
    with _state_lock:
        names = list(session(sid)['memory'].get('last_names', []))
    try:
        return memory.baseline_llm_only({'birth_year': request.birth_year, 'region': request.region}, _clean_memory_seeds(request.seeds), names)
    except (taste_agent.TasteAgentError, ValueError, TypeError):
        return {'available': False, 'note': "Gemini couldn't produce a baseline this time. Try again in a moment."}

@app.post('/api/memory/reset')
def memory_reset(x_session_id: str | None=Header(default=None)) -> dict[str, str]:
    sid = get_session_id(x_session_id)
    with _state_lock:
        session(sid)['memory'] = memory.new_state()
        save(sid)
    return {'status': 'reset'}
