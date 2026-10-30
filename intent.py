from __future__ import annotations
import re
from typing import Any
import qloo_client
import taste
import taste_agent
PERSONAL_CATEGORIES = tuple(qloo_client.ENTITY_TYPES)
MEMORY_CATEGORIES = ('Artists', 'TV Shows', 'Movies')
MAX_PROMPT_CHARS = 500
GENRE_PATTERNS = {'horror': '\\bhorror\\b', 'thriller': '\\bthriller\\b', 'romance': '\\bromance|romantic\\b', 'comedy': '\\bcomedy|comed(y|ic)\\b', 'science fiction': '\\bscience fiction|sci[ -]?fi\\b', 'fantasy': '\\bfantasy\\b', 'documentary': '\\bdocumentar(y|ies)\\b', 'animation': '\\banimat(ed|ion)\\b', 'crime': '\\bcrime|detective|mystery\\b', 'western': '\\bwestern\\b'}

def _explicit_categories(prompt: str, allowed: tuple[str, ...]) -> list[str]:
    text = prompt.casefold()
    patterns = {'Movies': '\\b(movie|movies|film|films|cinema)\\b', 'TV Shows': '\\b(tv|television|show|shows|series|sitcom)\\b', 'Books': '\\b(book|books|novel|novels|reading|author)\\b', 'Podcasts': '\\b(podcast|podcasts|audio show)\\b', 'Artists': '\\b(music|song|songs|track|tracks|artist|artists|singer|band|album|albums|soundtrack)\\b'}
    return [category for category, pattern in patterns.items() if category in allowed and re.search(pattern, text)]

def _required_genres(prompt: str) -> list[str]:
    text = prompt.casefold()
    return [genre for genre, pattern in GENRE_PATTERNS.items() if re.search(pattern, text)]

def _strings(value: Any, limit: int=6, max_len: int=80) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        clean = re.sub('\\s+', ' ', item).strip()[:max_len]
        if clean and clean.casefold() not in {x.casefold() for x in out}:
            out.append(clean)
        if len(out) >= limit:
            break
    return out

def _fallback(prompt: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    text = prompt.casefold()
    inferred: list[str] = []
    patterns = {'Movies': '\\b(movie|movies|film|films|cinema)\\b', 'TV Shows': '\\b(tv|television|show|shows|series|sitcom)\\b', 'Books': '\\b(book|books|novel|novels|reading|author)\\b', 'Podcasts': '\\b(podcast|podcasts|audio show)\\b', 'Artists': '\\b(music|song|songs|track|tracks|artist|artists|singer|band|album|albums|soundtrack)\\b'}
    for category, pattern in patterns.items():
        if category in allowed and re.search(pattern, text):
            inferred.append(category)
    if not inferred:
        inferred = list(allowed)
    words = taste.goal_keywords(prompt, limit=6)
    seed_terms = [prompt[:100]]
    keyword_phrase = ' '.join(words[:4])
    if keyword_phrase and keyword_phrase.casefold() != prompt[:100].casefold():
        seed_terms.append(keyword_phrase)
    reference_match = re.search("\\b(?:like|similar to|in the style of|with the feel of|that feel like)\\s+([\\w'’:-]+(?:\\s+[\\w'’:-]+){0,5})", prompt, re.IGNORECASE)
    reference_terms = [reference_match.group(1).strip(' .,!?:;')] if reference_match else []
    requested_count_match = re.search('\\b(\\d{1,2})\\s+(?:recommendations?|suggestions?|picks?)\\b', text)
    return {'mood': [], 'themes': words[:4], 'categories': inferred, 'seed_terms': seed_terms[:3], 'reference_terms': reference_terms, 'reference_attributes': [], 'required_genres': _required_genres(prompt), 'requested_count': min(int(requested_count_match.group(1)), 10) if requested_count_match else 5, 'constraints': [], 'discovery_goal': 'cross-category exploration' if len(inferred) > 1 else 'focused discovery', 'parser': 'deterministic'}

def parse_vibe_prompt(prompt: str, mode: str='personal', use_gemini: bool=True, progress=None) -> dict[str, Any]:
    raw = re.sub('\\s+', ' ', str(prompt or '')).strip()[:MAX_PROMPT_CHARS]
    if not raw:
        raise ValueError("Describe the mood, theme or kind of discovery you're looking for.")
    allowed = MEMORY_CATEGORIES if mode == 'memory' else PERSONAL_CATEGORIES
    parsed: dict[str, Any] | None = None
    if use_gemini and taste_agent.gemini_configured():
        try:
            parsed = taste_agent.interpret_vibe_prompt(raw, list(allowed), progress=progress)
        except taste_agent.TasteAgentError:
            parsed = None
    if not isinstance(parsed, dict):
        parsed = _fallback(raw, allowed)
    categories = []
    for category in _strings(parsed.get('categories'), limit=len(allowed), max_len=40):
        if category in allowed and category not in categories:
            categories.append(category)
    explicit_categories = _explicit_categories(raw, allowed)
    if explicit_categories:
        categories = explicit_categories
    if not categories:
        categories = _fallback(raw, allowed)['categories']
    result = {'original_prompt': raw, 'mood': _strings(parsed.get('mood'), 5), 'themes': _strings(parsed.get('themes'), 6), 'categories': categories, 'seed_terms': _strings(parsed.get('seed_terms'), 6), 'reference_terms': _strings(parsed.get('reference_terms'), 4), 'reference_attributes': _strings(parsed.get('reference_attributes'), 8), 'constraints': _strings(parsed.get('constraints'), 6), 'discovery_goal': str(parsed.get('discovery_goal') or 'cultural discovery').strip()[:100], 'requested_count': max(1, min(int(parsed.get('requested_count') or 5), 10)), 'parser': 'gemini' if parsed.get('parser') == 'gemini' else 'deterministic', 'required_genres': list(dict.fromkeys([*_required_genres(raw), *_strings(parsed.get('required_genres'), 6)]))}
    fallback = _fallback(raw, allowed)
    if not result['seed_terms']:
        result['seed_terms'] = fallback['seed_terms']
    result['reference_terms'] = list(dict.fromkeys([*fallback['reference_terms'], *result['reference_terms']]))[:4]
    if result['requested_count'] == 5 and fallback['requested_count'] != 5:
        result['requested_count'] = fallback['requested_count']
    return result

def resolve_qloo_seeds(intent: dict[str, Any], limit_per_category: int=2) -> list[dict[str, Any]]:
    categories = [c for c in intent.get('categories', []) if c in PERSONAL_CATEGORIES]
    references = list(intent.get('reference_terms') or [])
    terms = list(intent.get('seed_terms') or [])[:4]
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def search(term: str, category: str, role: str, max_hits: int) -> int:
        count = 0
        try:
            results = qloo_client.search_entities(str(term), category, 8)
        except (qloo_client.QlooError, ValueError):
            return 0
        if role == 'reference':
            target = str(term).casefold().strip()
            results = sorted(results, key=lambda item: 0 if str(item.get('name') or '').casefold().strip() == target else 1 if str(item.get('name') or '').casefold().strip().startswith(target) else 2)
        for item in results:
            entity_id = str(item.get('entity_id') or '')
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            compact = taste.compact_entity({**item, 'category': category}) | {'category': category, 'vibe_role': role}
            found.append(compact)
            count += 1
            if count >= max_hits:
                break
        return count
    for category in categories:
        for term in references:
            search(term, category, 'reference', 1)
    for category in categories:
        count = sum((1 for item in found if item.get('category') == category and item.get('vibe_role') == 'supporting'))
        for term in terms:
            if count >= limit_per_category:
                break
            count += search(term, category, 'supporting', limit_per_category - count)
    return found

def resolve_intent_tags(intent: dict[str, Any]) -> list[str]:
    terms = [*intent.get('mood', []), *intent.get('themes', []), *intent.get('seed_terms', [])]
    ids: list[str] = []
    for term in terms[:12]:
        try:
            matches = qloo_client.search_tags(str(term), 1)
        except (qloo_client.QlooError, ValueError):
            continue
        if matches:
            tag_id = str(matches[0].get('tag_id') or '')
            if tag_id and tag_id not in ids:
                ids.append(tag_id)
    return ids[:8]
