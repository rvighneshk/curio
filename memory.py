import datetime
import re
from typing import Any, Callable
from urllib.parse import quote
import qloo_client
import taste
import taste_agent
from qloo_client import MAX_SIGNALS, QlooError
MEMORY_CATEGORIES = ('Artists', 'TV Shows', 'Movies')
SCREEN_CATEGORIES = ('TV Shows', 'Movies')
HANDLE_PREFIX = {'Artists': 'art', 'TV Shows': 'tv', 'Movies': 'mov'}
REACTIONS = ('love', 'ok', 'upset')
MAX_SEEDS = 6
MAX_TOOL_CALLS = 7
MIN_RESULTS = 3
POPULARITY_CAP = 0.9
WIDEN_BY = 5
FORMATIVE_START, FORMATIVE_END = (10, 30)
MUSIC_COUNT, SCREEN_COUNT = (4, 4)
MAX_PROMPT_CHARS = 240
PRESETS: dict[str, dict[str, Any]] = {'classic-hollywood': {'label': 'Classic Hollywood & swing', 'blurb': 'Born 1942 · loved I Love Lucy, Frank Sinatra and Casablanca', 'birth_year': 1942, 'region': 'United States', 'seeds': [('Artists', 'Frank Sinatra'), ('TV Shows', 'I Love Lucy'), ('Movies', 'Casablanca')]}, 'british-rock': {'label': 'British rock & television', 'blurb': 'Born 1958 · loved The Beatles, The Rolling Stones and Doctor Who', 'birth_year': 1958, 'region': 'United Kingdom', 'seeds': [('Artists', 'The Beatles'), ('Artists', 'The Rolling Stones'), ('TV Shows', 'Doctor Who')]}}

class MemoryLaneError(RuntimeError):
    pass

def current_year() -> int:
    return datetime.date.today().year

def formative_window(birth_year: int) -> tuple[int, int]:
    start = min(int(birth_year) + FORMATIVE_START, current_year())
    end = min(int(birth_year) + FORMATIVE_END, current_year())
    return (start, max(start, end))

def age_bracket(birth_year: int) -> str | None:
    return '55_and_older' if current_year() - int(birth_year) >= 55 else None

def year_of(item: dict[str, Any]) -> int | None:
    for key in ('release_year', 'release_date', 'publication_year'):
        match = re.search('(1[89]\\d\\d|20\\d\\d)', str(item.get(key) or ''))
        if match:
            return int(match.group(1))
    return None

def _clip(text: Any, limit: int) -> str:
    value = re.sub('\\s+', ' ', str(text or '')).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    stop = max(cut.rfind('. '), cut.rfind('; '))
    return cut[:stop + 1] if stop > limit * 0.5 else cut.rsplit(' ', 1)[0] + '…'

def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = (set(), [])
    for item in items:
        key = str(item.get('entity_id') or item.get('name'))
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out

def new_state() -> dict[str, Any]:
    return {'profile': None, 'seeds': [], 'shown': [], 'reactions': {}, 'last_names': []}

def record_reaction(state: dict[str, Any], entity: dict[str, Any], reaction: str) -> dict[str, int]:
    if reaction not in REACTIONS:
        raise MemoryLaneError('Reaction must be love, ok or upset.')
    entity_id = str(entity.get('entity_id') or '').strip()
    if not entity_id or not entity.get('name'):
        raise MemoryLaneError('The reaction needs an entity with an id and a name.')
    compact = taste.compact_entity(entity)
    if entity.get('year') is not None:
        compact['year'] = entity['year']
    state['reactions'][entity_id] = {'reaction': reaction, 'entity': compact}
    counts = {r: 0 for r in REACTIONS}
    for row in state['reactions'].values():
        counts[row['reaction']] = counts.get(row['reaction'], 0) + 1
    return counts

def resolve_preset(key: str, search: Callable[..., list[dict[str, Any]]] | None=None) -> dict[str, Any]:
    preset = PRESETS.get(key)
    if not preset:
        raise MemoryLaneError('Unknown preset.')
    search = search or qloo_client.search_entities
    seeds = []
    for category, name in preset['seeds']:
        results = search(name, category, 5)
        if not results:
            continue
        exact = next((r for r in results if str(r.get('name', '')).casefold() == name.casefold()), None)
        seeds.append(taste.compact_entity(exact or results[0]) | {'category': category})
    return {'key': key, 'label': preset['label'], 'birth_year': preset['birth_year'], 'region': preset['region'], 'seeds': seeds}

def spotify_links(entity: dict[str, Any]) -> dict[str, str]:
    spotify_id = str(entity.get('spotify_id') or '')
    if spotify_id:
        return {'open': f'https://open.spotify.com/artist/{spotify_id}', 'embed': f'https://open.spotify.com/embed/artist/{spotify_id}'}
    return {'open': f"https://open.spotify.com/search/{quote(str(entity.get('name') or ''))}"}

def default_prompt(entity: dict[str, Any]) -> str:
    name = entity.get('name', 'this')
    facts = entity.get('facts') or {}
    category = entity.get('category')
    if category == 'Artists':
        songs = facts.get('notable_songs') or []
        if songs:
            return f'Shall we put on “{songs[0]}”? What does it bring to mind?'
        return f"What do you like about {name}'s music?"
    if category == 'TV Shows':
        return f'What did you enjoy about {name}? Who would you watch it with?'
    return f'What stands out about {name} for you? Tell me about a favourite moment.'
DEFAULT_TIPS = ["Let them lead: follow their reactions and skip anything that doesn't land.", 'Pause between songs and invite a story or a feeling, rather than quizzing.', "Stop whenever they seem tired or unsettled. There's nothing to finish."]

class MemoryAgent:

    def __init__(self, profile: dict[str, Any], seeds: list[dict[str, Any]], reactions: dict[str, dict[str, Any]] | None=None, shown_ids: list[str] | None=None, recommend: Callable[..., list[dict[str, Any]]] | None=None, progress: Callable[[str, str], None] | None=None):
        try:
            self.birth_year = int(profile.get('birth_year'))
        except (TypeError, ValueError) as exc:
            raise MemoryLaneError('A birth year is needed to choose the right era.') from exc
        if not 1900 <= self.birth_year <= current_year() - 1:
            raise MemoryLaneError('Birth year looks out of range.')
        self.region = str(profile.get('region') or '').strip()[:120]
        self.window = formative_window(self.birth_year)
        self.age_bracket = age_bracket(self.birth_year)
        self.recommend = recommend or qloo_client.get_recommendations
        self.progress = progress
        self.trace: list[dict[str, Any]] = []
        self.adaptations: list[str] = []
        self.calls = 0
        self.entities: dict[str, dict[str, Any]] = {}
        self.by_category: dict[str, list[dict[str, Any]]] = {c: [] for c in MEMORY_CATEGORIES}
        self.handles: dict[str, str] = {}
        self._handle_of: dict[str, str] = {}
        self._pool: dict[str, dict[str, Any]] = {}
        self.plan_text: dict[str, Any] = {}
        self.reactions = reactions or {}
        self.revisit: list[dict[str, Any]] = []
        usable = [s for s in seeds if isinstance(s, dict) and s.get('entity_id') and (s.get('category') in MEMORY_CATEGORIES)]
        self.excluded = {str(i) for i in shown_ids or []}
        loved = self._apply_reactions()
        self.seeds = _dedupe([*loved, *usable])[:MAX_SEEDS]
        if not self.seeds:
            raise MemoryLaneError('Add at least one artist, show or film the person loved.')
        self.seed_handles: list[str] = []
        for index, seed in enumerate(self.seeds, start=1):
            handle = f's{index}'
            seed_id = str(seed['entity_id'])
            self.handles[handle], self._handle_of[seed_id] = (seed_id, handle)
            self._pool[seed_id] = seed
            self.seed_handles.append(handle)
        self.excluded |= {str(s['entity_id']) for s in self.seeds}

    def log(self, tool: str, summary: str, **details: Any) -> None:
        event = {'step': len(self.trace) + 1, 'tool': tool, 'summary': summary}
        if details:
            event['details'] = details
        self.trace.append(event)
        if self.progress:
            title = {'explore': "Asking Qloo's taste graph", 'inspect': 'Reading an item', 'adapt': 'Adapting the plan', 'plan': 'Planning the session', 'fallback': 'Using the Qloo-only planner', 'validate': 'Checking every pick'}.get(tool, 'Working')
            self.progress(title, summary)

    def adapt(self, message: str) -> None:
        self.adaptations.append(message)
        self.log('adapt', message)

    def _apply_reactions(self) -> list[dict[str, Any]]:
        loved: list[dict[str, Any]] = []
        years: list[int] = []
        for entity_id, row in self.reactions.items():
            entity = dict(row.get('entity') or {})
            reaction = row.get('reaction')
            name = entity.get('name') or 'that item'
            if reaction == 'love' and entity.get('category') in MEMORY_CATEGORIES:
                loved.append(entity)
                self.revisit.append(entity)
                self.adaptations.append(f"You loved {name}, so it's now a starting point.")
                year = entity.get('year') or year_of(entity)
                if year and entity.get('category') in SCREEN_CATEGORIES:
                    years.append(int(year))
            elif reaction == 'upset':
                self.excluded.add(str(entity_id))
                self.adaptations.append(f"{name} didn't sit well, so it's removed and won't come back.")
        if len(years) >= 2:
            low, high = (min(years) - 3, max(years) + 3)
            self.window = (max(low, 1900), min(high, current_year()))
            self.adaptations.append(f'Your loved shows and films cluster around {min(years)}–{max(years)}, so the era is now {self.window[0]}–{self.window[1]}.')
        return loved

    def _by_handle(self, handle: Any) -> dict[str, Any] | None:
        entity_id = self.handles.get(str(handle or '').strip().lower())
        return self._pool.get(entity_id) if entity_id else None

    def _new_handle(self, category: str, entity_id: str) -> str:
        if entity_id in self._handle_of:
            return self._handle_of[entity_id]
        prefix = HANDLE_PREFIX[category]
        number = 1 + sum((1 for h in self.handles if h.startswith(prefix) and h[len(prefix):].isdigit()))
        handle = f'{prefix}{number}'
        self.handles[handle], self._handle_of[entity_id] = (entity_id, handle)
        return handle

    def explore(self, category: str, seed_handles: list[str] | None=None, year_min: int=0, year_max: int=0, deeper_cuts: bool=False, older_audience: bool=False) -> dict[str, Any]:
        if category not in MEMORY_CATEGORIES:
            return {'error': f"Category must be one of: {', '.join(MEMORY_CATEGORIES)}."}
        if self.calls >= MAX_TOOL_CALLS:
            return {'error': 'The tool budget is used up. Finish now with your JSON answer.'}
        signals: list[dict[str, Any]] = []
        if isinstance(seed_handles, str):
            seed_handles = [seed_handles]
        for handle in seed_handles or []:
            entity = self._by_handle(handle)
            if entity and entity not in signals:
                signals.append(entity)
        if not signals:
            return {'error': f"No valid seed handles. Valid handles: {', '.join(sorted(self.handles))}."}
        self.calls += 1
        signals = signals[:MAX_SIGNALS]
        names = {str(s['entity_id']): str(s.get('name')) for s in signals}
        use_era = category in SCREEN_CATEGORIES and (year_min or year_max)
        low = int(year_min) if use_era and year_min else None
        high = int(year_max) if use_era and year_max else None
        if low and high and (low > high):
            low, high = (high, low)
        cap = POPULARITY_CAP if deeper_cuts else None
        audience = self.age_bracket if older_audience else None
        filters = {k: v for k, v in {'year_min': low, 'year_max': high, 'popularity_max': cap, 'audience_age': audience}.items() if v}
        meta: dict[str, Any] = {}
        try:
            found = self.recommend([str(s['entity_id']) for s in signals], category, limit=12, exclude_ids=sorted(self.excluded), explain=True, signal_names=names, year_min=low, year_max=high, popularity_max=cap, age_bracket=audience, meta=meta)
        except (QlooError, ValueError) as exc:
            self.log('explore', f"{category} from {', '.join(names.values())}: Qloo error ({str(exc)[:110]}).", category=category, seeds=list(names.values()), filters=filters, outcome='error')
            return {'error': f'Qloo error: {str(exc)[:160]}'}
        window_for_score = self.window
        kept: list[dict[str, Any]] = []
        off_era = 0
        for cand in found:
            entity_id = str(cand.get('entity_id') or '')
            if not entity_id or entity_id in self.excluded:
                continue
            candidate_year = year_of(cand)
            if (low or high) and candidate_year is not None and (low and candidate_year < low or (high and candidate_year > high)):
                off_era += 1
                continue
            if entity_id in self.entities:
                kept.append(self.entities[entity_id])
                continue
            evidence = taste.build_evidence(signals, cand)
            item = taste.compact_entity({**cand, 'evidence': evidence})
            item['year'] = candidate_year
            in_window = candidate_year is not None and window_for_score[0] <= candidate_year <= window_for_score[1]
            item['agent_score'] = round((evidence.get('affinity') or 0) + 0.3 * len(evidence['shared_tags']) + 0.2 * len(evidence['shared_terms']) + (0.15 if in_window else 0), 3)
            item['handle'] = self._new_handle(category, entity_id)
            self.entities[entity_id] = item
            self._pool[entity_id] = item
            self.by_category[category].append(item)
            kept.append(item)
        self.by_category[category].sort(key=lambda e: e['agent_score'], reverse=True)
        dropped = list(meta.get('dropped') or [])
        hints = []
        if len(kept) < MIN_RESULTS:
            hints.append(f'Only {len(kept)} usable result(s). Widen the years by about {WIDEN_BY} each way, turn off deeper_cuts/older_audience, or use different seeds.')
        if 'era' in dropped:
            hints.append('Qloo did not accept the era filter for this call; results were filtered by year locally.')
        if 'audience' in dropped:
            hints.append('Qloo did not accept the older-audience signal; it was dropped.')
        if off_era:
            hints.append(f'{off_era} result(s) fell outside the requested years and were removed.')
        best = kept[0] if kept else None
        self.log('explore', f"{category} from {', '.join(names.values())}" + (f" · years {low or '…'}–{high or '…'}" if low or high else '') + (' · deeper cuts' if cap else '') + (' · 55+ audience' if audience else '') + f' → {len(kept)} usable' + (f" (best: {best['name']})" if best else ''), category=category, seeds=list(names.values()), filters=filters, usable=len(kept), off_era_removed=off_era, qloo_dropped=dropped, best=best['name'] if best else None)
        return {'category': category, 'count': len(kept), 'hint': ' '.join(hints) or None, 'results': [{'handle': c['handle'], 'name': c['name'], 'year': c.get('year'), 'about': _clip(taste.description_of(c), 140), 'tags': c['tags'][:4], 'affinity': round(c['evidence']['affinity'], 2) if isinstance(c['evidence'].get('affinity'), (int, float)) else None, 'driven_by': [d['name'] for d in c['evidence'].get('drivers', []) if d.get('name')]} for c in kept[:8]]}

    def inspect(self, handle: str) -> dict[str, Any]:
        entity = self._by_handle(handle)
        if not entity:
            return {'error': f"Unknown handle. Valid handles: {', '.join(sorted(self.handles))}."}
        self.log('inspect', f"Read the details Qloo holds for {entity['name']}.", handle=handle)
        return {'handle': handle, 'name': entity['name'], 'category': entity.get('category'), 'year': entity.get('year') or year_of(entity), 'about': _clip(taste.description_of(entity), 300), 'tags': entity.get('tags', [])[:8], 'facts': entity.get('facts') or {}}

    def plan_deterministic(self) -> None:
        low, high = self.window
        for category in SCREEN_CATEGORIES:
            result = self.explore(category, self.seed_handles, low, high)
            if result.get('count', 0) < MIN_RESULTS:
                self.adapt(f"Only {result.get('count', 0)} {category.lower()} matched {low}–{high}; widened the window by {WIDEN_BY} years each way.")
                self.explore(category, self.seed_handles, low - WIDEN_BY, min(high + WIDEN_BY, current_year()))
        screen_leads = [self.by_category[c][0]['handle'] for c in SCREEN_CATEGORIES if self.by_category[c]][:2]
        seeds = [*self.seed_handles, *screen_leads]
        result = self.explore('Artists', seeds, deeper_cuts=True, older_audience=bool(self.age_bracket))
        if result.get('count', 0) < MIN_RESULTS:
            self.adapt('Few artists came back with the deeper-cuts and older-audience settings; retried with both relaxed.')
            self.explore('Artists', seeds)

    def _prompt(self) -> str:
        low, high = self.window
        age = current_year() - self.birth_year
        lines = [f"{h} = {self._pool[self.handles[h]]['name']} ({self._pool[self.handles[h]].get('category')}" + (f', {year_of(self._pool[self.handles[h]])}' if year_of(self._pool[self.handles[h]]) else '') + ')' for h in self.seed_handles]
        reaction_block = ''
        if self.adaptations:
            reaction_block = "\nWHAT THE CAREGIVER'S REACTIONS ALREADY CHANGED (respect these):\n- " + '\n- '.join(self.adaptations) + '\n'
        return f"""You are the planning agent inside Curio Memory Lane. A caregiver wants a gentle, enjoyable reminiscence session for someone they care for (often a person living with dementia). You may ONLY recommend items returned by your tools. Tool results come from Qloo's cultural taste graph.\n\nPERSON\n- Born {self.birth_year} (about {age} years old). Formative years, when the music and TV of a life tend to stick: {low}-{high}.\n- Region / culture note: {self.region or 'not given'}\n- Starting points the caregiver chose (use these handles as seeds):\n{chr(10).join(('  ' + l for l in lines))}\n{reaction_block}\nHOW TO WORK\n1. Call explore for "TV Shows" and for "Movies" with year_min={low}, year_max={high} and all starting handles as seeds.\n2. Then call explore for "Artists" with the starting handles plus the handles of the best one or two shows or films you found, so the music reflects the same era. Use deeper_cuts=true so it isn't only the most famous names, and older_audience={('true' if self.age_bracket else 'false')}.\n3. READ each result. If a call returns fewer than {MIN_RESULTS} results, or its hint says so, ADAPT: widen the years by about {WIDEN_BY} each way, turn off deeper_cuts or older_audience, or change seeds. Remember what you changed.\n4. Optionally call inspect_item(handle) to read an item's facts before writing questions.\n5. Use at most {MAX_TOOL_CALLS} tool calls in total, then answer.\n\nFINAL ANSWER: reply with ONLY this JSON (no markdown):\n{{"title": "a short, warm title", "intro": "1-2 sentences for the caregiver", "music": ["art1", "..."], "screen": ["tv1", "mov1", "..."], "prompts": [{{"about": "art1", "question": "..."}}], "tips": ["...", "..."], "adaptation_note": "what you changed after seeing results, or empty"}}\n\nRULES\n- music: 3-{MUSIC_COUNT} artist handles. screen: 3-{SCREEN_COUNT} TV/film handles. Use only handles returned by tools.\n- prompts: one open, warm question per chosen item. Never quiz-like ("Do you remember...?"). Invite stories and feelings.\n- Mention only facts present in tool results (about, facts). Do not claim any medical or therapeutic benefit.\n- tips: 2-3 short, practical caregiver tips."""

    def plan_with_gemini(self) -> dict[str, Any]:
        agent = self

        def explore(category: str, seed_handles: list[str], year_min: int=0, year_max: int=0, deeper_cuts: bool=False, older_audience: bool=False) -> dict:
            try:
                return agent.explore(category, seed_handles, year_min, year_max, deeper_cuts, older_audience)
            except Exception as exc:
                return {'error': f'explore failed: {str(exc)[:120]}'}

        def inspect_item(handle: str) -> dict:
            try:
                return agent.inspect(handle)
            except Exception as exc:
                return {'error': f'inspect failed: {str(exc)[:120]}'}
        prompt = self._prompt()

        def call(client: Any, model: str) -> str:
            from google.genai import types
            config = types.GenerateContentConfig(tools=[explore, inspect_item], temperature=0.3, automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=MAX_TOOL_CALLS + 3))
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            return response.text or ''
        text = taste_agent._call_with_model_fallback(call)
        if not self.entities:
            raise taste_agent.TasteAgentError('Gemini answered without calling the Qloo tools')
        return taste_agent._extract_json(text)

    def _clean_plan(self, data: dict[str, Any]) -> dict[str, Any]:

        def ids_for(values: Any, categories: tuple[str, ...]) -> list[str]:
            out: list[str] = []
            for value in values if isinstance(values, list) else []:
                entity = self._by_handle(value) if isinstance(value, str) else None
                entity_id = str(entity['entity_id']) if entity else ''
                if entity_id in self.entities and self.entities[entity_id].get('category') in categories and (entity_id not in out):
                    out.append(entity_id)
            return out
        prompts: dict[str, str] = {}
        for row in data.get('prompts') if isinstance(data.get('prompts'), list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('question'), str):
                continue
            entity = self._by_handle(row.get('about'))
            if entity and str(entity['entity_id']) in self.entities:
                prompts[str(entity['entity_id'])] = _clip(row['question'], MAX_PROMPT_CHARS)
        tips = [_clip(t, 200) for t in (data.get('tips') if isinstance(data.get('tips'), list) else []) if isinstance(t, str) and t.strip()][:3]
        return {'title': _clip(data.get('title'), 90), 'intro': _clip(data.get('intro'), 300), 'music': ids_for(data.get('music'), ('Artists',)), 'screen': ids_for(data.get('screen'), SCREEN_CATEGORIES), 'prompts': prompts, 'tips': tips, 'adaptation_note': _clip(data.get('adaptation_note'), 300)}

    def _fill(self, chosen: list[str], categories: tuple[str, ...], count: int) -> list[str]:
        out = list(dict.fromkeys(chosen))[:count]
        if len(categories) == 1:
            pool = [e['entity_id'] for e in self.by_category[categories[0]]]
        else:
            columns = [[e['entity_id'] for e in self.by_category[c]] for c in categories]
            pool = [col[i] for i in range(max((len(c) for c in columns), default=0)) for col in columns if i < len(col)]
        for entity_id in pool:
            if len(out) >= count:
                break
            if entity_id not in out and entity_id not in self.excluded:
                out.append(entity_id)
        return out

    def _item(self, entity: dict[str, Any], prompt: str | None) -> dict[str, Any]:
        evidence = entity.get('evidence') or {}
        drivers = evidence.get('drivers') or []
        anchor = drivers[0]['name'] if drivers and drivers[0].get('name') else self.seeds[0]['name']
        shown = {k: v for k, v in entity.items() if k not in {'qloo', 'agent_score'}}
        if entity.get('category') == 'Artists':
            shown['links'] = spotify_links(entity)
        return {'handle': entity.get('handle'), 'entity': shown, 'evidence': evidence, 'why': taste.explain_text(anchor, entity['name'], evidence), 'prompt': prompt or default_prompt(entity)}

    def compose(self, plan: dict[str, Any] | None=None, mode: str='qloo-planner', note: str | None=None) -> dict[str, Any]:
        plan = plan or {}
        music_ids = self._fill(plan.get('music', []), ('Artists',), MUSIC_COUNT)
        screen_ids = self._fill(plan.get('screen', []), SCREEN_CATEGORIES, SCREEN_COUNT)
        prompts = plan.get('prompts', {})
        music = [self._item(self.entities[i], prompts.get(i)) for i in music_ids]
        screen = [self._item(self.entities[i], prompts.get(i)) for i in screen_ids]
        revisit = []
        for entity in self.revisit:
            shown = {k: v for k, v in entity.items() if k not in {'qloo', 'agent_score'}}
            if entity.get('category') == 'Artists':
                shown['links'] = spotify_links(entity)
            revisit.append({'handle': None, 'entity': shown, 'evidence': {}, 'why': "You loved this last time, so it's here to revisit.", 'prompt': default_prompt(entity)})
        if plan.get('adaptation_note'):
            self.adaptations.append(f"Agent: {plan['adaptation_note']}")
        picked = [*music, *screen]
        grounded = all((str(p['entity'].get('entity_id')) in self.entities for p in picked))
        self.log('validate', f'{len(picked)} picks, each resolved to an entity Qloo returned.', grounded=grounded, unique=len({p['entity'].get('entity_id') for p in picked}))
        low, high = self.window
        return {'title': plan.get('title') or 'A gentle trip down memory lane', 'intro': plan.get('intro') or f'Music and shows from the years that tend to stay with us most ({low}–{high}), chosen from what fans of their favourites also love.', 'era': {'from': low, 'to': high, 'birth_year': self.birth_year, 'region': self.region}, 'music': music, 'screen': screen, 'revisit': revisit, 'starters': [p['prompt'] for p in [*music[:3], *screen[:3]]], 'tips': plan.get('tips') or DEFAULT_TIPS, 'adaptations': self.adaptations, 'mode': mode, 'note': note, 'trace': self.trace, 'stats': {'qloo_calls': self.calls, 'entities_considered': len(self.entities), 'grounded': grounded}, 'shown_ids': [str(p['entity'].get('entity_id')) for p in picked]}

def run_memory_agent(profile: dict[str, Any], seeds: list[dict[str, Any]], reactions: dict[str, dict[str, Any]] | None=None, shown_ids: list[str] | None=None, use_gemini: bool=True, recommend: Callable[..., list[dict[str, Any]]] | None=None, progress: Callable[[str, str], None] | None=None) -> dict[str, Any]:
    agent = MemoryAgent(profile, seeds, reactions, shown_ids, recommend, progress)
    agent.log('plan', f'Born {agent.birth_year}: formative years {agent.window[0]}–{agent.window[1]}' + (f', region: {agent.region}' if agent.region else '') + f'; {len(agent.seeds)} starting point(s).')
    mode, note, plan = ('qloo-planner', None, None)
    if use_gemini and taste_agent.gemini_configured():
        try:
            if progress:
                progress('Gemini is planning the session', 'It will decide which Qloo queries to run, then adapt to what comes back.')
            plan = agent._clean_plan(agent.plan_with_gemini())
            mode = 'gemini-agent'
        except Exception as exc:
            note = "Gemini couldn't prepare the session this time. Curio has prepared your recommendations using Qloo instead, so you can continue."
            agent.log('fallback', 'Gemini planner unavailable; Qloo-only planner took over.')
    if not agent.by_category['Artists'] or not (agent.by_category['TV Shows'] or agent.by_category['Movies']):
        agent.calls = 0
        agent.plan_deterministic()
    if not agent.entities:
        raise QlooError('Qloo returned nothing for these favourites. Try different starting points.')
    return agent.compose(plan, mode, note)

def baseline_llm_only(profile: dict[str, Any], seeds: list[dict[str, Any]], grounded_names: list[str] | None=None) -> dict[str, Any]:
    if not taste_agent.gemini_configured():
        return {'available': False, 'note': "Gemini isn't configured on this server, so there's no LLM-only baseline to show."}
    birth_year = int(profile.get('birth_year'))
    low, high = formative_window(birth_year)
    loved = ', '.join((f"{s.get('name')} ({s.get('category')})" for s in seeds[:MAX_SEEDS])) or 'nothing specified'
    context = f"A caregiver wants a reminiscence session for someone born in {birth_year}{(' in ' + str(profile.get('region')) if profile.get('region') else '')} who loved: {loved}. Suggest exactly 4 musicians, 3 TV shows and 3 films suitable for this person's formative era ({low}-{high}). Use your own knowledge only: do not use tools or claim external verification. "
    schema = 'Reply with ONLY valid JSON in this exact shape: {"items":[{"category":"Artist|TV Show|Movie","name":"...","year":1955}]} . '
    instruction = 'For musicians set year to null; for shows and films give the best-known original release year. Include all 10 suggestions.'

    def parse_items(raw: str) -> list[dict[str, Any]]:
        data = taste_agent._extract_json(raw)
        parsed = []
        for row in data.get('items') if isinstance(data.get('items'), list) else []:
            if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name'].strip():
                category = str(row.get('category') or '')[:20]
                if category.casefold() not in {'artist', 'tv show', 'movie', 'tv', 'film'}:
                    continue
                year = row.get('year') if isinstance(row.get('year'), int) and (not isinstance(row.get('year'), bool)) else None
                parsed.append({'category': category, 'name': row['name'].strip()[:120], 'year': year})
        return parsed[:10]
    items = parse_items(taste_agent._generate(context + schema + instruction, json_mode=True))
    if not items:
        retry_prompt = context + 'Return at least six concrete suggestions across the three requested categories. ' + schema + instruction
        items = parse_items(taste_agent._generate(retry_prompt, json_mode=True))
    dated = [i for i in items if i['year'] and i['category'].casefold() in ('tv show', 'movie', 'tv', 'film')]
    outside = [i for i in dated if not low <= i['year'] <= high]
    folded = {n.casefold() for n in grounded_names or []}
    overlap = sum((1 for i in items if i['name'].casefold() in folded))
    note = 'Independent Gemini response; no Qloo tools were used. Years are model-stated and have not been independently verified.' if items else 'Gemini did not return usable structured suggestions after a retry. Try comparing again.'
    return {'available': True, 'items': items, 'window': [low, high], 'dated_count': len(dated), 'outside_window': len(outside), 'overlap_with_grounded': overlap, 'note': note}
