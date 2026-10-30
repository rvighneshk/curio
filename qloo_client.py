import html
import os
import re
import threading
import time
from typing import Any
from urllib.parse import quote
import requests
from dotenv import load_dotenv
load_dotenv()
BASE_URL = os.getenv('QLOO_BASE_URL', 'https://hackathon.api.qloo.com').rstrip('/')
ENTITY_TYPES = {'Movies': 'urn:entity:movie', 'TV Shows': 'urn:entity:tv_show', 'Books': 'urn:entity:book', 'Podcasts': 'urn:entity:podcast', 'Artists': 'urn:entity:artist'}
TIMEOUT = (5, 25)
MAX_SIGNALS = 8
MAX_EXCLUDES = 40
RETRY_STATUS = {429, 502, 503, 504}
CACHE_TTL = int(os.getenv('QLOO_CACHE_TTL', '900'))
CACHE_MAX = 512
_sleep = time.sleep
_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

class QlooError(RuntimeError):
    pass

class QlooBadRequest(QlooError):
    pass

def _headers() -> dict[str, str]:
    key = os.getenv('QLOO_API_KEY')
    if not key:
        raise QlooError('QLOO_API_KEY is not configured on the server.')
    return {'X-Api-Key': key, 'Accept': 'application/json'}

def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()

def _cache_key(path: str, params: dict[str, Any]) -> tuple:
    return (path, tuple(sorted(((k, str(v)) for k, v in params.items()))))

def _cache_get(key: tuple) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        if hit[0] < time.time():
            _CACHE.pop(key, None)
            return None
        return hit[1]

def _cache_put(key: tuple, value: dict[str, Any]) -> None:
    if CACHE_TTL <= 0:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.time() + CACHE_TTL, value)

def _get(path: str, params: dict[str, Any], *, use_cache: bool=True) -> dict[str, Any]:
    key = _cache_key(path, params)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    last_error: QlooError | None = None
    for attempt in range(2):
        try:
            response = requests.get(f'{BASE_URL}{path}', headers=_headers(), params=params, timeout=TIMEOUT)
        except requests.Timeout as exc:
            last_error = QlooError('Qloo request timed out. Try again.')
            last_error.__cause__ = exc
        except requests.RequestException as exc:
            last_error = QlooError(f'Could not connect to Qloo ({exc.__class__.__name__}).')
            last_error.__cause__ = exc
        else:
            if response.status_code in RETRY_STATUS:
                last_error = QlooError(f'Qloo returned HTTP {response.status_code}. Try again shortly.')
            elif response.status_code in (400, 422):
                raise QlooBadRequest(f'Qloo rejected the request: {response.text[:300].strip()}')
            elif not response.ok:
                raise QlooError(f'Qloo returned HTTP {response.status_code}: {response.text[:300].strip()}')
            else:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise QlooError('Qloo returned invalid JSON.') from exc
                if not isinstance(data, dict):
                    raise QlooError('Qloo returned an unexpected response format.')
                if use_cache:
                    _cache_put(key, data)
                return data
        if attempt == 0:
            _sleep(0.6)
    raise last_error or QlooError('Qloo request failed.')

def _extract_entities(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = data.get('results')
    if isinstance(results, dict):
        entities = results.get('entities')
        if isinstance(entities, list):
            return [item for item in entities if isinstance(item, dict)]
        if isinstance(results.get('items'), list):
            return [item for item in results['items'] if isinstance(item, dict)]
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    if isinstance(data.get('entities'), list):
        return [item for item in data['entities'] if isinstance(item, dict)]
    return []

def search_entities(query: str, category: str, limit: int=10) -> list[dict[str, Any]]:
    if category not in ENTITY_TYPES:
        raise ValueError('Unsupported category.')
    if not query.strip():
        return []
    data = _get('/search', {'query': query.strip(), 'types': ENTITY_TYPES[category], 'take': min(max(limit, 1), 20)})
    entities = _extract_entities(data)
    return [normalize_entity(entity, category, ENTITY_TYPES[category]) for entity in entities if entity.get('entity_id') and entity.get('name')]
GEMS_MAX = float(os.getenv('QLOO_GEMS_MAX', '0.5'))
POPULAR_MIN = float(os.getenv('QLOO_POPULAR_MIN', '0.7'))

def get_recommendations(signal_ids: list[str], category: str, limit: int=10, exclude_ids: list[str] | None=None, explain: bool=True, trending: bool=False, signal_names: dict[str, str] | None=None, signal_tags: list[str] | None=None, popularity: str | None=None, meta: dict[str, Any] | None=None, year_min: int | None=None, year_max: int | None=None, popularity_max: float | None=None, age_bracket: str | None=None) -> list[dict[str, Any]]:
    if category not in ENTITY_TYPES:
        raise ValueError('Unsupported category.')
    target_type = ENTITY_TYPES[category]
    signals = list(dict.fromkeys((str(s).strip() for s in signal_ids if s and str(s).strip())))[:MAX_SIGNALS]
    if not signals:
        return []
    excludes = [e for e in dict.fromkeys(exclude_ids or []) if e and e not in signals][-MAX_EXCLUDES:]
    base = {'filter.type': target_type, 'signal.interests.entities': ','.join(signals), 'take': min(max(limit, 1), 20)}
    groups: dict[str, dict[str, Any]] = {}
    if explain:
        groups['explainability'] = {'feature.explainability': 'true'}
    if signal_tags:
        groups['goal'] = {'signal.interests.tags': ','.join(dict.fromkeys(signal_tags))}
    if popularity == 'gems':
        groups['popularity'] = {'filter.popularity.max': GEMS_MAX}
    elif popularity == 'popular':
        groups['popularity'] = {'filter.popularity.min': POPULAR_MIN}
    if year_min is not None or year_max is not None:
        era: dict[str, Any] = {}
        if year_min is not None:
            era['filter.release_year.min'] = int(year_min)
        if year_max is not None:
            era['filter.release_year.max'] = int(year_max)
        groups['era'] = era
    if popularity_max is not None and 'popularity' not in groups:
        groups['popcap'] = {'filter.popularity.max': round(float(popularity_max), 2)}
    if age_bracket:
        groups['audience'] = {'signal.demographics.age': str(age_bracket)}
    if trending:
        groups['trending'] = {'bias.trends': 'high'}
    if excludes:
        groups['exclude'] = {'filter.exclude.entities': ','.join(excludes)}
    active = list(groups)
    dropped: list[str] = []

    def fetch() -> dict[str, Any]:
        while True:
            params = dict(base)
            for name in active:
                params.update(groups[name])
            try:
                return _get('/v2/insights', params)
            except QlooBadRequest as exc:
                message = str(exc).casefold()
                invalid_signals = 'signal.interests.entities' in message and ('invalid' in message or 'valid signal' in message)
                if invalid_signals and len(signals) > 1:
                    raise
                if not active:
                    raise
                culprit = next((n for n in active if any((key.casefold() in message for key in groups[n]))), active[0])
                active.remove(culprit)
                dropped.append(culprit)
    names = signal_names or {}
    seen = set(signals) | set(excludes)
    try:
        while True:
            data = fetch()
            collected = []
            for entity in _extract_entities(data):
                normalized = normalize_entity(entity, category, target_type)
                normalized['qloo'] = extract_evidence(entity, names)
                identity = normalized.get('entity_id') or normalized.get('name', '').casefold()
                if identity and identity not in seen:
                    seen.add(identity)
                    collected.append(normalized)
            relaxable = [n for n in ('audience', 'popcap', 'popularity', 'goal') if n in active]
            if collected or not relaxable:
                break
            active.remove(relaxable[0])
            dropped.append(relaxable[0])
            seen = set(signals) | set(excludes)
    except QlooBadRequest as exc:
        message = str(exc).casefold()
        invalid_signals = 'signal.interests.entities' in message and ('invalid' in message or 'valid signal' in message)
        if not invalid_signals or len(signals) < 2:
            raise
        collected_by_id: dict[str, dict[str, Any]] = {}
        successful = 0
        failures = []
        for signal in signals:
            try:
                partial_meta: dict[str, Any] = {}
                partial = get_recommendations([signal], category, limit=limit, exclude_ids=excludes, explain=explain, trending=trending, signal_names=names, signal_tags=signal_tags, popularity=popularity, meta=partial_meta)
                successful += 1
                for item in partial:
                    identity = str(item.get('entity_id') or item.get('name', '')).casefold()
                    if identity and identity not in collected_by_id:
                        collected_by_id[identity] = item
            except (QlooError, ValueError) as single_exc:
                failures.append(f'{names.get(signal, signal)}: {single_exc}')
        if not successful:
            raise QlooBadRequest('Qloo rejected every individual taste signal for this category. ' + '; '.join(failures[:2])) from exc
        collected = list(collected_by_id.values())[:min(max(limit, 1), 20)]
        dropped.append('invalid_signal_fallback')
        if meta is not None:
            meta['signal_fallback'] = {'requested': len(signals), 'successful': successful, 'failed': len(signals) - successful}
    if meta is not None:
        meta['dropped'] = dropped
    return collected

def search_tags(query: str, limit: int=3) -> list[dict[str, str]]:
    term = query.strip()
    if not term:
        return []
    try:
        data = _get('/v2/tags', {'filter.query': term, 'take': limit})
    except QlooError:
        return []
    return _parse_tags(data)[:limit]

def _parse_tags(data: dict[str, Any]) -> list[dict[str, str]]:
    results = data.get('results')
    candidates: Any = []
    if isinstance(results, dict):
        candidates = results.get('tags') or results.get('entities') or []
    elif isinstance(results, list):
        candidates = results
    if not candidates and isinstance(data.get('tags'), list):
        candidates = data['tags']
    tags = []
    for tag in candidates if isinstance(candidates, list) else []:
        if not isinstance(tag, dict):
            continue
        tag_id = next((tag.get(k) for k in ('id', 'tag_id', 'entity_id') if isinstance(tag.get(k), str) and tag.get(k)), None)
        if tag_id:
            tags.append({'tag_id': tag_id, 'name': str(tag.get('name') or tag_id)})
    return tags

def get_entities(entity_ids: list[str]) -> list[dict[str, Any]]:
    ids = [i for i in dict.fromkeys(entity_ids) if i][:20]
    if not ids:
        return []
    try:
        data = _get('/entities', {'entity_ids': ','.join(ids)})
    except QlooError:
        return []
    return [normalize_entity(e) for e in _extract_entities(data) if e.get('entity_id') and e.get('name')]

def tag_names(raw_tags: Any, limit: int=12) -> list[str]:
    names: list[str] = []
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        name = tag.get('name') if isinstance(tag, dict) else tag
        if isinstance(name, str) and name.strip() and (name.strip().casefold() not in {n.casefold() for n in names}):
            names.append(name.strip())
        if len(names) >= limit:
            break
    return names

def extract_evidence(entity: dict[str, Any], signal_names: dict[str, str] | None=None) -> dict[str, Any]:
    query = entity.get('query') if isinstance(entity.get('query'), dict) else {}
    affinity = query.get('affinity')
    popularity = entity.get('popularity')
    raw = query.get('explainability')
    if raw in (None, '', [], {}):
        raw = entity.get('explainability')
    return {'affinity': float(affinity) if isinstance(affinity, (int, float)) else None, 'popularity': float(popularity) if isinstance(popularity, (int, float)) else None, 'drivers': _parse_explainability(raw, signal_names or {})}

def _parse_explainability(raw: Any, names: dict[str, str]) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        items: list[Any] = []
        for value in raw.values():
            items.extend(value if isinstance(value, list) else [value])
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    drivers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entity_id = next((item.get(k) for k in ('entity_id', 'id', 'entity') if isinstance(item.get(k), str)), None)
        name = item.get('name') if isinstance(item.get('name'), str) else names.get(entity_id or '')
        if not name:
            continue
        weight = next((item.get(k) for k in ('weight', 'score', 'affinity', 'value') if isinstance(item.get(k), (int, float))), None)
        drivers.append({'entity_id': entity_id, 'name': name, 'weight': weight})
    drivers.sort(key=lambda d: d['weight'] if d['weight'] is not None else 0, reverse=True)
    return drivers[:5]

def _first_value(value):
    if isinstance(value, list):
        for entry in value:
            result = _first_value(entry)
            if result not in (None, '', [], {}):
                return result
        return None
    if isinstance(value, dict):
        if isinstance(value.get('value'), (str, int, float)):
            return value['value']
        if isinstance(value.get('url'), str):
            return value['url']
        return None
    return value

def _author_value(properties: dict[str, Any]) -> str | None:
    for key in ('author', 'authors', 'written_by', 'book_author'):
        value = properties.get(key)
        values = value if isinstance(value, list) else [value]
        names = []
        for entry in values:
            if isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
            elif isinstance(entry, dict):
                candidate = entry.get('value') or entry.get('name') or entry.get('title')
                if isinstance(candidate, str) and candidate.strip():
                    names.append(candidate.strip())
        if names:
            return ', '.join(dict.fromkeys(names))
    return None

def _first_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((entry for entry in value if isinstance(entry, dict)), None)
    return None

def _clean_description(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    value = re.sub('(?i)<br\\s*/?>', '\n', value)
    value = re.sub('<[^>]*>', ' ', value)
    value = html.unescape(value)
    return re.sub('\\s+', ' ', value).strip()

def _description_value(properties: dict[str, Any]) -> str:
    short_descriptions = properties.get('short_descriptions')
    entries = short_descriptions if isinstance(short_descriptions, list) else [short_descriptions]
    for entry in entries:
        if isinstance(entry, str):
            cleaned = _clean_description(entry)
            if cleaned:
                return cleaned
            continue
        if not isinstance(entry, dict):
            continue
        language = entry.get('language', entry.get('languages', []))
        languages = [language] if isinstance(language, str) else language
        if not isinstance(languages, list):
            languages = []
        english = any((isinstance(lang, str) and lang.strip().lower().replace('_', '-').startswith('en') for lang in languages))
        if english:
            cleaned = _clean_description(entry.get('value') or entry.get('description'))
            if cleaned:
                return cleaned
    return _clean_description(properties.get('description'))

def _provider_ratings(external: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ratings = {}
    for provider in ('imdb', 'rottentomatoes'):
        record = _first_record(external.get(provider))
        if not record:
            continue
        values = {field: record[field] for field in ('user_rating', 'user_rating_count', 'critic_rating', 'critic_rating_count') if record.get(field) is not None}
        if values:
            ratings[provider] = values
    return ratings

def _provider_links(external: dict[str, Any], category: str) -> list[dict[str, str]]:
    links = []

    def add_record(provider: str, label: str, fallback_url: str | None=None) -> None:
        record = _first_record(external.get(provider))
        if not record:
            return
        direct_url = next((record.get(field) for field in ('url', 'link', 'href', 'web_url') if isinstance(record.get(field), str) and record[field].startswith(('https://', 'http://'))), None)
        if direct_url:
            links.append({'label': label, 'url': direct_url})
        elif fallback_url:
            links.append({'label': label, 'url': fallback_url})
    imdb = _first_record(external.get('imdb'))
    imdb_id = imdb.get('id') if imdb else None
    add_record('imdb', 'IMDb', f"https://www.imdb.com/title/{quote(imdb_id, safe='')}/" if isinstance(imdb_id, str) and imdb_id.startswith('tt') else None)
    if category == 'Movies':
        letterboxd = _first_record(external.get('letterboxd'))
        letterboxd_id = letterboxd.get('id') if letterboxd else None
        add_record('letterboxd', 'Letterboxd', f"https://letterboxd.com/film/{quote(letterboxd_id.strip(), safe='')}/" if isinstance(letterboxd_id, str) and letterboxd_id.strip() else None)
    rotten = _first_record(external.get('rottentomatoes'))
    rotten_id = rotten.get('id') if rotten else None
    rotten_path = 'tv' if category == 'TV Shows' else 'm'
    add_record('rottentomatoes', 'Rotten Tomatoes', f"https://www.rottentomatoes.com/{rotten_path}/{quote(rotten_id.strip(), safe='')}/" if isinstance(rotten_id, str) and rotten_id.strip() else None)
    netflix = _first_record(external.get('netflix'))
    netflix_id = netflix.get('id') if netflix else None
    add_record('netflix', 'Netflix', f'https://www.netflix.com/title/{netflix_id}' if isinstance(netflix_id, str) and netflix_id.isdigit() else None)
    seen = set()
    return [link for link in links if link['url'] not in seen and (not seen.add(link['url']))]

def _review_links(properties: dict[str, Any]) -> list[dict[str, str]]:
    websites = properties.get('websites')
    entries = websites if isinstance(websites, list) else [websites]
    links = []
    seen = set()
    for entry in entries:
        if isinstance(entry, dict):
            url = entry.get('url') or entry.get('value')
            label = entry.get('name') or entry.get('label') or 'Official website'
        elif isinstance(entry, str):
            url, label = (entry, 'Website')
        else:
            continue
        if isinstance(url, str) and url.startswith('https://') and (url not in seen):
            links.append({'label': str(label), 'url': url})
            seen.add(url)
    return links

def _string_list(value: Any, limit: int=5) -> list[str]:
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for entry in values:
        if isinstance(entry, dict):
            entry = entry.get('value') or entry.get('name')
        if isinstance(entry, str) and entry.strip() and (entry.strip() not in out):
            out.append(entry.strip())
        if len(out) >= limit:
            break
    return out

def _clip(text: Any, limit: int) -> str:
    value = _clean_description(text)
    if len(value) <= limit:
        return value
    cut = value[:limit]
    stop = max(cut.rfind('. '), cut.rfind('; '))
    return cut[:stop + 1] if stop > limit * 0.5 else cut.rsplit(' ', 1)[0] + '…'

def _entity_facts(properties: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    lists = {'notable_songs': ('notable_songs', 5), 'collaborators': ('collaborators', 4), 'fan_traits': ('adjectives_for_fans', 4), 'themes': ('lyrical_themes', 4), 'genres': ('primary_genres', 3), 'instruments': ('instruments', 3)}
    for out_key, (prop, limit) in lists.items():
        values = _string_list(properties.get(prop), limit)
        if values:
            facts[out_key] = values
    for out_key, prop, limit in (('known_for', 'known_for', 220), ('biography', 'biography', 320), ('themes_text', 'lyrical_themes_description', 220)):
        text = _clip(properties.get(prop), limit)
        if text:
            facts[out_key] = text
    for out_key, prop in (('born', 'date_of_birth'), ('died', 'year_of_death'), ('active_from', 'start_year'), ('active_to', 'end_year'), ('birthplace', 'place_of_birth')):
        value = _first_value(properties.get(prop))
        if value not in (None, '', [], {}):
            facts[out_key] = str(value)[:60]
    return facts

def _spotify_id(external: dict[str, Any]) -> str:
    record = _first_record(external.get('spotify'))
    value = record.get('id') if record else None
    return value if isinstance(value, str) and re.fullmatch('[A-Za-z0-9]{10,40}', value) else ''

def normalize_entity(entity: dict[str, Any], category: str | None=None, fallback_type: str | None=None) -> dict[str, Any]:
    properties = entity.get('properties') if isinstance(entity.get('properties'), dict) else {}
    description = _description_value(properties)
    image = _first_value(properties.get('image')) or ''
    external = entity.get('external') if isinstance(entity.get('external'), dict) else {}
    tags = tag_names(entity.get('tags'))
    actual_category = category or _category_from_type(entity.get('type') or fallback_type)
    fields = {'content_rating': _first_value(properties.get('content_rating')), 'production_company': _first_value(properties.get('production_company')), 'release_country': _first_value(properties.get('release_country')), 'release_date': _first_value(properties.get('release_date')), 'release_year': _first_value(properties.get('release_year')), 'duration': _first_value(properties.get('duration')), 'short_description': description, 'format': _first_value(properties.get('format')), 'isbn10': _first_value(properties.get('isbn10')), 'isbn13': _first_value(properties.get('isbn13')), 'page_count': _first_value(properties.get('page_count')), 'publication_date': _first_value(properties.get('publication_date')), 'publication_year': _first_value(properties.get('publication_year')), 'author': _author_value(properties)}
    return {'entity_id': entity.get('entity_id'), 'name': str(entity.get('name') or 'Untitled'), 'type': entity.get('type') or fallback_type or (ENTITY_TYPES.get(category) if category else ''), 'category': actual_category, 'description': description, 'short_description': description, 'image': image if isinstance(image, str) else '', 'properties': properties, 'external': external, 'ratings': _provider_ratings(external), 'provider_links': _provider_links(external, actual_category), 'review_links': _review_links(properties), 'tags': tags, 'source': 'qloo', 'facts': _entity_facts(properties), 'spotify_id': _spotify_id(external), **fields}

def _category_from_type(entity_type: str) -> str:
    return next((name for name, value in ENTITY_TYPES.items() if value == entity_type), 'Other')
