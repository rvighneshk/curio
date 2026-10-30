from __future__ import annotations
from typing import Any, Callable
import qloo_client
import taste
import taste_agent
from qloo_client import ENTITY_TYPES, MAX_SIGNALS, QlooError
PATH_LENGTH = 5
MAX_PER_CATEGORY = 2
HANDLE_PREFIX = {'Movies': 'mov', 'TV Shows': 'tv', 'Books': 'book', 'Podcasts': 'pod', 'Artists': 'art'}
GENRE_ALIASES = {'horror': ('horror', 'supernatural', 'paranormal', 'haunted', 'ghost', 'occult', 'demonic', 'possession', 'slasher'), 'thriller': ('thriller', 'suspense', 'psychological', 'crime', 'mystery', 'conspiracy'), 'romance': ('romance', 'romantic', 'love story', 'relationship'), 'comedy': ('comedy', 'comedic', 'humor', 'humour', 'funny'), 'science fiction': ('science fiction', 'sci-fi', 'science-fiction', 'space', 'dystopian', 'futuristic'), 'fantasy': ('fantasy', 'magical', 'mythical', 'sword and sorcery'), 'crime': ('crime', 'detective', 'police', 'murder', 'investigation')}

class AgentPlanError(RuntimeError):
    pass

def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = (set(), [])
    for item in items:
        key = str(item.get('entity_id') or item.get('name'))
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out

def _ref_of(step: dict[str, Any]) -> str:
    for key in ('id', 'entity_id', 'handle', 'name', 'title'):
        if isinstance(step.get(key), (str, int)) and str(step[key]).strip():
            return str(step[key]).strip()
    return ''

class AgentContext:

    def __init__(self, favourites: list[dict[str, Any]], liked: list[dict[str, Any]], skipped_ids: list[str] | None=None, recommend: Callable[..., list[dict[str, Any]]] | None=None, goal: str | None=None, search_tags: Callable[..., list[dict[str, str]]] | None=None, progress: Callable[[str, str], None] | None=None, categories: list[str] | None=None, required_genres: list[str] | None=None):
        self.favourites = _dedupe(favourites)
        self.liked = _dedupe(liked)
        self.seeds = _dedupe([*self.favourites, *self.liked])
        self.excluded = {str(i) for i in skipped_ids or []} | {str(s.get('entity_id')) for s in self.seeds}
        self.recommend = recommend or qloo_client.get_recommendations
        self.search_tags = search_tags or qloo_client.search_tags
        self.goal = (goal or '').strip()[:200]
        self.goal_tags: list[dict[str, str]] = []
        self.goal_applied: set[str] = set()
        self.goal_dropped: set[str] = set()
        self.goal_note: str | None = None
        self.entities: dict[str, dict[str, Any]] = {}
        self.by_category: dict[str, list[dict[str, Any]]] = {}
        self.handles: dict[str, str] = {}
        self._handle_of: dict[str, str] = {}
        self.trace: list[dict[str, Any]] = []
        self.signature: dict[str, Any] | None = None
        self.progress = progress
        requested = [c for c in categories or list(ENTITY_TYPES) if c in ENTITY_TYPES]
        self.categories = list(dict.fromkeys(requested)) or list(ENTITY_TYPES)
        self.max_per_category = max(MAX_PER_CATEGORY, (PATH_LENGTH + len(self.categories) - 1) // len(self.categories))
        self.required_genres = [str(g).casefold() for g in required_genres or [] if str(g).strip()]

    def log(self, tool: str, summary: str, **details: Any) -> None:
        event = {'step': len(self.trace) + 1, 'tool': tool, 'summary': summary}
        if details:
            event['details'] = details
        self.trace.append(event)
        if self.progress:
            title = {'resolve_goal': 'Interpreting your goal', 'analyze_taste': 'Reading your cultural signals', 'discover': "Searching Qloo's cultural graph", 'rank': 'Planning your next five', 'fallback': 'Using the Qloo-grounded fallback', 'validate': 'Validating your discoveries'}.get(tool, 'Working on your discovery')
            self.progress(title, summary)

    def resolve_goal(self) -> None:
        if not self.goal:
            return
        words = taste.goal_keywords(self.goal)
        found: list[dict[str, str]] = []
        for word in words:
            matches = self.search_tags(word, 1)
            if matches:
                found.append(matches[0])
        if found:
            self.goal_tags = found
            self.goal_note = f"Goal applied through Qloo tags: {', '.join((t['name'] for t in found))}."
        elif words:
            self.goal_note = "No Qloo tag matched your goal, so it wasn't applied."
        else:
            self.goal_note = "Your goal had no searchable words, so it wasn't applied."
        self.log('resolve_goal', self.goal_note)

    def goal_status(self) -> str | None:
        if not self.goal:
            return None
        if self.goal_tags and self.goal_dropped and (not self.goal_applied):
            return "Qloo couldn't combine your goal with your taste, so it wasn't applied."
        if self.goal_tags and self.goal_dropped:
            return f"{self.goal_note} (Dropped for {', '.join(sorted(self.goal_dropped))} where Qloo returned nothing.)"
        return self.goal_note

    def analyze_taste(self) -> dict[str, Any]:
        self.signature = taste.taste_signature(self.favourites, self.liked)
        tags = [t['tag'] for t in self.signature['top_tags'][:5]]
        coverage = self.signature['coverage']
        self.log('analyze_taste', f"Read {self.signature['item_count']} picks; top Qloo tags: {', '.join(tags) or 'none yet'}.")
        return {'top_tags': self.signature['top_tags'][:8], 'threads': self.signature['threads'][:3], 'items_per_category': {c: coverage.get(c, 0) for c in ENTITY_TYPES}}

    def _signals_for(self, category: str) -> list[dict[str, Any]]:
        ordered = sorted(self.seeds, key=lambda s: (0 if s.get('vibe_role') == 'reference' else 1, 0 if s.get('category') == category else 1))
        return ordered[:MAX_SIGNALS]

    def _handle(self, category: str, entity_id: str) -> str:
        if entity_id in self._handle_of:
            return self._handle_of[entity_id]
        prefix = HANDLE_PREFIX[category]
        n = 1 + sum((1 for h in self.handles if h.startswith(prefix) and h[len(prefix):].isdigit()))
        handle = f'{prefix}{n}'
        self.handles[handle], self._handle_of[entity_id] = (entity_id, handle)
        return handle

    def discover(self, category: str) -> dict[str, Any]:
        if category not in ENTITY_TYPES:
            return {'error': f"Unknown category. Use one of: {', '.join(ENTITY_TYPES)}."}
        if category not in self.categories:
            return {'error': f'{category} was not selected for this discovery.'}
        if self.signature is None:
            self.analyze_taste()
        signals = self._signals_for(category)
        names = {str(s.get('entity_id')): str(s.get('name')) for s in signals}
        meta: dict[str, Any] = {}
        try:
            found = self.recommend([str(s['entity_id']) for s in signals], category, limit=20, exclude_ids=sorted(self.excluded), explain=True, signal_names=names, signal_tags=[t['tag_id'] for t in self.goal_tags] or None, meta=meta)
        except (QlooError, ValueError) as exc:
            self.log('discover', f'{category}: Qloo error ({exc}).')
            return {'error': str(exc)}
        if self.goal_tags:
            (self.goal_dropped if 'goal' in meta.get('dropped', []) else self.goal_applied).add(category)
        sig_tags = taste.signature_tag_set(self.signature or {})
        items = []
        genre_fallback_items = []
        genre_filtered = 0
        for cand in found:
            cid = str(cand.get('entity_id') or '')
            if not cid or cid in self.excluded:
                continue
            evidence = taste.build_evidence(signals, cand)
            cand = taste.compact_entity({**cand, 'evidence': evidence})
            genre_match = False
            if self.required_genres:
                searchable = ' '.join([str(tag) for tag in cand.get('tags') or []] + [str(cand.get('description') or ''), str(cand.get('short_description') or '')]).casefold()
                genre_terms = [term for genre in self.required_genres for term in GENRE_ALIASES.get(genre, (genre,))]
                genre_match = any((term in searchable for term in genre_terms))
                if not genre_match:
                    genre_filtered += 1
            overlap = len({t.casefold() for t in cand['tags']} & sig_tags)
            genre_boost = 4.0 if genre_match else 0.0
            cand['agent_score'] = round(overlap + 0.5 * len(evidence['shared_tags']) + 0.5 * len(evidence['shared_terms']) + (evidence['affinity'] or 0) + genre_boost, 3)
            cand['handle'] = self._handle(category, cid)
            self.entities[cid] = cand
            (items if not self.required_genres or genre_match else genre_fallback_items).append(cand)
        if self.required_genres and (not items):
            items = genre_fallback_items
        items.sort(key=lambda c: c['agent_score'], reverse=True)
        self.by_category[category] = items
        best = items[0] if items else None
        self.log('discover', f'{category}: blended {len(signals)} seeds in one Qloo call' + (' with your goal tags' if self.goal_tags and category in self.goal_applied else '') + f', {len(items)} candidates' + (f"; best: {best['name']}" if best else '.') + (f' Filtered {genre_filtered} candidates without a genre match.' if genre_filtered and items is not genre_fallback_items else f' Broadened retrieval because Qloo returned no explicit genre metadata matches ({genre_filtered} candidates retained for relevance ranking).' if genre_filtered else ''), category=category, signal_count=len(signals), candidate_count=len(items), genre_filtered_count=genre_filtered, required_genres=self.required_genres, goal_tags_applied=bool(self.goal_tags and category in self.goal_applied), best_candidate={'name': best['name'], 'score': best['agent_score'], 'shared_tags': best['evidence'].get('shared_tags', [])[:5], 'shared_terms': best['evidence'].get('shared_terms', [])[:5], 'affinity': best['evidence'].get('affinity')} if best else None)
        return {'category': category, 'items': items}

    def options(self, per_category: int=3) -> list[dict[str, Any]]:
        out = []
        for items in self.by_category.values():
            for c in items[:per_category]:
                out.append({'id': c['handle'], 'category': c.get('category'), 'name': c['name'], 'about': taste.description_of(c)[:200], 'shared_tags': c['evidence']['shared_tags'], 'shared_terms': c['evidence'].get('shared_terms', []), 'affinity': c['evidence']['affinity']})
        return out

    def taste_summary(self) -> dict[str, Any]:
        sig = self.signature or {}
        return {'top_tags': [t['tag'] for t in sig.get('top_tags', [])[:8]], 'threads': [{'tag': t['tag'], 'categories': t['categories']} for t in sig.get('threads', [])[:3]], 'items_per_category': sig.get('coverage', {})}

    def resolve(self, ref: str) -> str | None:
        ref = (ref or '').strip()
        if not ref:
            return None
        low = ref.casefold()
        for handle, entity_id in self.handles.items():
            if handle.casefold() == low:
                return entity_id
        if ref in self.entities:
            return ref
        for entity_id, entity in self.entities.items():
            if entity['name'].casefold() == low:
                return entity_id
        if len(low) >= 6:
            loose = [i for i, e in self.entities.items() if low in e['name'].casefold() or e['name'].casefold() in low]
            if len(loose) == 1:
                return loose[0]
        return None

    def qloo_order(self) -> list[dict[str, Any]]:
        best = sorted((items[0] for items in self.by_category.values() if items), key=lambda e: e['agent_score'], reverse=True)
        chosen = best[:PATH_LENGTH]
        if len(chosen) < PATH_LENGTH:
            used = {e['entity_id'] for e in chosen}
            counts: dict[str, int] = {}
            for e in chosen:
                counts[e.get('category', '')] = counts.get(e.get('category', ''), 0) + 1
            pool = sorted((e for items in self.by_category.values() for e in items[1:]), key=lambda e: e['agent_score'], reverse=True)
            for e in pool:
                cat = e.get('category', '')
                if len(chosen) >= PATH_LENGTH:
                    break
                if e['entity_id'] in used or counts.get(cat, 0) >= self.max_per_category:
                    continue
                chosen.append(e)
                used.add(e['entity_id'])
                counts[cat] = counts.get(cat, 0) + 1
        return chosen

    def build_step(self, entity: dict[str, Any], order: int, note: str) -> dict[str, Any]:
        evidence = entity.get('evidence', {})
        drivers = evidence.get('drivers') or []
        anchor = drivers[0]['name'] if drivers else self.favourites[0]['name'] if self.favourites else 'your favourites'
        return {'order': order, 'entity': entity, 'evidence': evidence, 'why': taste.explain_text(anchor, entity['name'], evidence), 'note': (note or '').strip()[:220]}

def _theme(ctx: AgentContext) -> str:
    tags = [t['name'] for t in ctx.goal_tags] + [t['tag'] for t in (ctx.signature or {}).get('top_tags', [])]
    tags = list(dict.fromkeys(tags))[:2]
    return f"A path through {' and '.join(tags)}" if tags else 'A path beyond your usual orbit'

def _gemini_rank(ctx: AgentContext, baseline: list[dict[str, Any]]) -> tuple[str, list[tuple[str, str]], int]:
    data = taste_agent.rank_candidates(ctx.taste_summary(), ctx.goal, ctx.options())
    raw = data.get('steps')
    if not isinstance(raw, list) or not raw:
        raise AgentPlanError("Gemini's answer had no list of steps")
    model_picks: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for step in raw:
        if not isinstance(step, dict):
            continue
        entity_id = ctx.resolve(_ref_of(step))
        if entity_id and entity_id not in seen_ids:
            seen_ids.add(entity_id)
            model_picks.append((entity_id, str(step.get('note', ''))))
    if not model_picks:
        raise AgentPlanError(f"none of Gemini's {len(raw)} picks matched Qloo's candidates")
    picks: list[tuple[str, str]] = []
    used_ids: set[str] = set()
    used_categories: set[str] = set()
    overflow: list[tuple[str, str]] = []
    for entity_id, note in model_picks:
        entity = ctx.entities[entity_id]
        category = str(entity.get('category') or '')
        if len(picks) < PATH_LENGTH and category not in used_categories:
            picks.append((entity_id, note))
            used_ids.add(entity_id)
            used_categories.add(category)
        else:
            overflow.append((entity_id, note))
    matched = len(picks)
    by_category = sorted((entity for entity in ctx.entities.values() if entity.get('entity_id') not in used_ids), key=lambda entity: entity.get('agent_score', 0), reverse=True)
    for entity in by_category:
        if len(picks) >= PATH_LENGTH:
            break
        entity_id = str(entity.get('entity_id') or '')
        category = str(entity.get('category') or '')
        if not entity_id or category in used_categories:
            continue
        picks.append((entity_id, ''))
        used_ids.add(entity_id)
        used_categories.add(category)
    counts: dict[str, int] = {}
    for entity_id, _ in picks:
        category = str(ctx.entities[entity_id].get('category') or '')
        counts[category] = counts.get(category, 0) + 1
    for entity_id, note in [*overflow, *((str(e['entity_id']), '') for e in baseline)]:
        if len(picks) >= PATH_LENGTH:
            break
        if entity_id in used_ids or entity_id not in ctx.entities:
            continue
        category = str(ctx.entities[entity_id].get('category') or '')
        if counts.get(category, 0) >= ctx.max_per_category:
            continue
        picks.append((entity_id, note))
        used_ids.add(entity_id)
        counts[category] = counts.get(category, 0) + 1
    return (str(data.get('theme') or '').strip()[:120], picks, matched)

def run_discovery_agent(favourites: list[dict[str, Any]], liked: list[dict[str, Any]], skipped_ids: list[str] | None=None, goal: str | None=None, use_gemini: bool=True, recommend: Callable[..., list[dict[str, Any]]] | None=None, search_tags: Callable[..., list[dict[str, str]]] | None=None, progress: Callable[[str, str], None] | None=None, categories: list[str] | None=None, required_genres: list[str] | None=None) -> dict[str, Any]:
    ctx = AgentContext(favourites, liked, skipped_ids, recommend, goal=goal, search_tags=search_tags, progress=progress, categories=categories, required_genres=required_genres)
    if not ctx.seeds:
        raise AgentPlanError('Add at least one favourite so the agent has something to start from.')
    ctx.resolve_goal()
    ctx.analyze_taste()
    coverage = (ctx.signature or {}).get('coverage', {})
    errors = 0
    for category in sorted(ctx.categories, key=lambda c: coverage.get(c, 0)):
        if progress:
            progress("Searching Qloo's cultural graph", f'Retrieving {category.lower()} candidates from your taste signals.')
        if ctx.discover(category).get('error'):
            errors += 1
    if not ctx.entities:
        raise QlooError('Qloo was unavailable for every category.' if errors else 'Qloo returned no discoveries for this taste profile.')
    baseline = ctx.qloo_order()
    picks = [(e['entity_id'], '') for e in baseline]
    theme, mode, note, topped_up = ('', 'qloo-planner', None, 0)
    if use_gemini and taste_agent.gemini_configured():
        try:
            if progress:
                progress('Gemini is planning your next five', 'Ranking and explaining only the candidates returned by Qloo.')
            theme, picks, matched = _gemini_rank(ctx, baseline)
            mode, topped_up = ('gemini-agent', len(picks) - matched)
            ctx.log('rank', f'Kept {matched} Gemini-ranked pick(s), enforced category diversity, and added {topped_up} Qloo-grounded pick(s) to complete the five-item plan.', planner='gemini', gemini_matched_picks=matched, qloo_topped_up_picks=topped_up, selected=[{'name': ctx.entities[eid]['name'], 'category': ctx.entities[eid].get('category'), 'model_note_used': bool(n)} for eid, n in picks])
        except (taste_agent.TasteAgentError, AgentPlanError, KeyError, TypeError, ValueError) as exc:
            reason = (str(exc) or exc.__class__.__name__)[:220]
            note = f"Gemini couldn't rank this time ({reason}); ranked directly from Qloo scores instead."
            ctx.log('fallback', note)
    if mode == 'qloo-planner':
        ctx.log('rank', "Ordered Qloo's best candidate per category by score.", planner='qloo_score_fallback', selected=[{'name': ctx.entities[eid]['name'], 'category': ctx.entities[eid].get('category'), 'score': ctx.entities[eid].get('agent_score', 0)} for eid, _ in picks])
    steps = [ctx.build_step(ctx.entities[eid], i + 1, n) for i, (eid, n) in enumerate(picks)]
    category_counts: dict[str, int] = {}
    for step in steps:
        category = str(step['entity'].get('category') or 'Unknown')
        category_counts[category] = category_counts.get(category, 0) + 1
    ctx.log('validate', f"{len(steps)} pick{('' if len(steps) == 1 else 's')}, each resolved to an entity Qloo returned.", grounded=True, pick_count=len(steps), unique_entities=len({step['entity'].get('entity_id') for step in steps}), category_counts=category_counts)
    return {'theme': theme or _theme(ctx), 'steps': steps, 'topped_up': topped_up, 'mode': mode, 'note': note, 'goal_status': ctx.goal_status(), 'trace': ctx.trace}
