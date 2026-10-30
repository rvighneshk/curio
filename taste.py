from __future__ import annotations
import hashlib
import re
from collections import Counter, defaultdict
from typing import Any
KEEP_FIELDS = ('entity_id', 'name', 'type', 'category', 'description', 'short_description', 'image', 'ratings', 'provider_links', 'review_links', 'tags', 'source', 'qloo', 'evidence', 'content_rating', 'production_company', 'release_country', 'release_date', 'release_year', 'duration', 'format', 'isbn10', 'isbn13', 'page_count', 'publication_date', 'publication_year', 'author', 'facts', 'spotify_id')

def compact_entity(item: dict[str, Any]) -> dict[str, Any]:
    out = {key: item[key] for key in KEEP_FIELDS if item.get(key) not in (None, '', [], {})}
    out['entity_id'] = str(item.get('entity_id') or '')
    out['name'] = str(item.get('name') or '')
    out['tags'] = tags_of(item)
    return out

def tags_of(item: dict[str, Any]) -> list[str]:
    raw = item.get('tags')
    names = []
    for tag in raw if isinstance(raw, list) else []:
        name = tag.get('name') if isinstance(tag, dict) else tag
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names

def _fold(values: list[str]) -> dict[str, str]:
    return {v.casefold(): v for v in values}

def taste_signature(favourites: list[dict[str, Any]], liked: list[dict[str, Any]], top: int=10) -> dict[str, Any]:
    weights: Counter[str] = Counter()
    labels: dict[str, str] = {}
    by_category: dict[str, set[str]] = defaultdict(set)
    carriers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for weight, group in ((2, favourites), (1, liked)):
        for item in group:
            key = str(item.get('entity_id') or item.get('name'))
            if key in seen and weight == 1:
                continue
            seen.add(key)
            category = str(item.get('category') or 'Other')
            for tag in tags_of(item):
                folded = tag.casefold()
                labels.setdefault(folded, tag)
                weights[folded] += weight
                by_category[folded].add(category)
                if all((c.get('entity_id') != item.get('entity_id') for c in carriers[folded])):
                    carriers[folded].append(item)
    top_tags = [{'tag': labels[t], 'weight': w, 'categories': sorted(by_category[t])} for t, w in weights.most_common(top)]
    threads = []
    for tag, w in weights.most_common():
        cats = sorted(by_category[tag])
        if len(cats) >= 2:
            threads.append({'tag': labels[tag], 'categories': cats, 'items': [{'name': c.get('name'), 'category': c.get('category')} for c in carriers[tag][:4]]})
        if len(threads) >= 5:
            break
    coverage = Counter((str(i.get('category')) for i in [*favourites, *liked]))
    return {'top_tags': top_tags, 'threads': threads, 'coverage': dict(coverage), 'item_count': len(seen)}

def signature_tag_set(signature: dict[str, Any], limit: int=12) -> set[str]:
    return {t['tag'].casefold() for t in signature.get('top_tags', [])[:limit]}

def build_evidence(signal_items: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any]:
    qloo = candidate.get('qloo') if isinstance(candidate.get('qloo'), dict) else {}
    cand_tags = _fold(tags_of(candidate))
    shared: Counter[str] = Counter()
    per_seed: list[tuple[int, str]] = []
    for seed in signal_items:
        overlap = [t for t in tags_of(seed) if t.casefold() in cand_tags]
        per_seed.append((len(overlap), str(seed.get('name') or '')))
        for tag in overlap:
            shared[tag.casefold()] += 1
    shared_tags = [cand_tags[t] for t, _ in shared.most_common(6)]
    terms: Counter[str] = Counter()
    for seed in signal_items:
        terms.update(description_overlap(seed, candidate, limit=6))
    shared_terms = [w for w, _ in sorted(terms.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[:4]]
    drivers = list(qloo.get('drivers') or [])
    drivers_source = 'qloo'
    if not drivers:
        drivers_source = 'tags'
        drivers = [{'entity_id': None, 'name': n, 'weight': None} for c, n in sorted(per_seed, reverse=True) if c and n][:3]
    return {'shared_tags': shared_tags, 'shared_terms': shared_terms, 'drivers': drivers[:3], 'drivers_source': drivers_source if drivers else None, 'affinity': qloo.get('affinity'), 'popularity': qloo.get('popularity'), 'signal_count': len(signal_items)}

def explain_text(source_name: str, candidate_name: str, evidence: dict[str, Any]) -> str:
    tags = evidence.get('shared_tags') or []
    terms = evidence.get('shared_terms') or []
    drivers = [d.get('name') for d in evidence.get('drivers') or [] if d.get('name')]
    affinity = evidence.get('affinity')
    pct = f' (Qloo match {round(affinity * 100)}%)' if isinstance(affinity, (int, float)) and 0 <= affinity <= 1 else ''
    if tags and terms:
        return f'{candidate_name} shares {_join(tags[:3])} with {source_name}, and both descriptions mention {_join(terms[:3])}{pct}.'
    if tags:
        extra = f' and with {drivers[0]}' if drivers and drivers[0] != source_name else ''
        return f"{candidate_name} shares {_join(tags[:3])} with {source_name}{extra}; Qloo's audience data links their fans{pct}."
    if terms:
        return f"Both descriptions mention {_join(terms[:3])}, and Qloo's audience data links {candidate_name} to {source_name}{pct}."
    if drivers and drivers[0] != source_name:
        return f'{candidate_name} reaches the same audience as {_join(drivers[:2])}{pct}; no shared tags or themes came back to say more.'
    return f"Qloo's audience data links {candidate_name} to {source_name}{pct}; no shared tags or themes came back to say more."

def _join(values: list[str]) -> str:
    values = [str(v) for v in values if v]
    if len(values) <= 1:
        return ''.join(values)
    return ', '.join(values[:-1]) + ' and ' + values[-1]

def fallback_profile(signature: dict[str, Any]) -> dict[str, Any]:
    tags = [t['tag'] for t in signature.get('top_tags', [])]
    if not tags:
        return {'title': 'Your taste is just getting started', 'summary': 'Like a few discoveries and Curio will start to see patterns in your taste.', 'signals': [], 'connections': []}
    title = f'A pull toward {_join(tags[:2])}' if len(tags) > 1 else f'A pull toward {tags[0]}'
    cats = signature.get('coverage', {})
    summary = f"Across {signature.get('item_count', 0)} picks in {len(cats)} categories, the strongest patterns Qloo found are {_join(tags[:4])}."
    connections = []
    for thread in signature.get('threads', [])[:4]:
        items = thread.get('items', [])
        if len(items) >= 2:
            connections.append({'from': items[0]['name'], 'to': items[1]['name'], 'reason': f"Both are tagged '{thread['tag']}' in Qloo, across {_join(thread['categories'])}."})
    return {'title': title, 'summary': summary, 'signals': tags[:6], 'connections': connections}
_STOPWORDS = {'a', 'an', 'the', 'and', 'or', 'of', 'to', 'for', 'with', 'that', 'this', 'very', 'some', 'something', 'anything', 'more', 'less', 'like', 'want', 'give', 'show', 'find', 'me', 'my', 'i', 'im', 'in', 'on', 'it', 'is', 'be', 'but', 'not', 'just', 'really', 'bit', 'little', 'kind', 'feel', 'feeling', 'vibe', 'vibes', 'stuff', 'things', 'thing', 'good', 'great', 'new', 'different', 'than', 'from', 'about'}

def goal_keywords(goal: str | None, limit: int=3) -> list[str]:
    words: list[str] = []
    for raw in (goal or '').lower().replace('-', ' ').split():
        word = ''.join((ch for ch in raw if ch.isalpha()))
        if len(word) >= 3 and word not in _STOPWORDS and (word not in words):
            words.append(word)
    return words[:limit]

def prefer_on_topic(seed: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seed_tags = {t.casefold() for t in tags_of(seed)}
    if not seed_tags:
        return list(items)
    on_topic = [i for i in items if seed_tags & {t.casefold() for t in tags_of(i)}]
    off_topic = [i for i in items if not seed_tags & {t.casefold() for t in tags_of(i)}]
    return on_topic + off_topic

def profile_key(favourites: list[dict[str, Any]], liked: list[dict[str, Any]], journey: list[dict[str, Any]]) -> str:
    parts = [f"f:{i.get('entity_id')}" for i in favourites]
    parts += [f"l:{i.get('entity_id')}" for i in liked]
    parts += [f"j:{n.get('entity_id')}:{n.get('feedback')}" for n in journey]
    return hashlib.sha1('|'.join(sorted(set(parts))).encode('utf-8')).hexdigest()[:16]
_DESC_STOP = _STOPWORDS | {'story', 'stories', 'series', 'season', 'seasons', 'episode', 'episodes', 'follow', 'follows', 'tells', 'telling', 'film', 'films', 'movie', 'movies', 'show', 'shows', 'book', 'books', 'novel', 'novels', 'podcast', 'podcasts', 'hosted', 'host', 'hosts', 'about', 'their', 'there', 'these', 'those', 'which', 'where', 'while', 'after', 'before', 'other', 'others', 'years', 'based', 'first', 'world', 'people', 'young', 'three', 'features', 'featuring', 'starring', 'directed', 'written', 'released', 'album', 'albums', 'music', 'artist', 'artists', 'singer', 'band', 'songs', 'song', 'american', 'british', 'across', 'through', 'around', 'between', 'during', 'becomes', 'become', 'return', 'returns', 'finds', 'begins', 'begin', 'takes', 'never', 'always', 'family', 'friends', 'friend', 'stars', 'star', 'weekly', 'daily', 'career', 'known', 'famous', 'popular', 'classic'}

def _terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall('[a-z]{5,}', (text or '').lower()):
        if len(word) > 5 and word.endswith('s') and (not word.endswith(('ss', 'is', 'us'))):
            word = word[:-1]
        if word not in _DESC_STOP:
            terms.add(word)
    return terms

def description_of(item: dict[str, Any]) -> str:
    return str(item.get('description') or item.get('short_description') or '').strip()

def description_overlap(a: dict[str, Any], b: dict[str, Any], limit: int=3) -> list[str]:
    common = _terms(description_of(a)) & _terms(description_of(b))
    return sorted(common, key=lambda w: (-len(w), w))[:limit]
