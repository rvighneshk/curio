from __future__ import annotations
import json
import os
import re
from typing import Any, Callable
from dotenv import load_dotenv
load_dotenv()
MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
FALLBACK_MODELS = [m.strip() for m in os.getenv('GEMINI_FALLBACK_MODELS', 'gemini-3.1-flash-lite,gemini-2.5-flash').split(',') if m.strip()]
_MODEL_MISSING = ('not found', '404', 'not supported', 'is not available', 'unknown model')

class TasteAgentError(RuntimeError):
    pass

def gemini_configured() -> bool:
    return bool(os.getenv('GEMINI_API_KEY'))

def _client():
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise TasteAgentError('GEMINI_API_KEY is not configured on the server.')
    try:
        from google import genai
    except ImportError as exc:
        raise TasteAgentError('The google-genai package is not installed.') from exc
    return genai.Client(api_key=api_key)

def _redact(message: str) -> str:
    key = os.getenv('GEMINI_API_KEY', '')
    return message.replace(key, '[redacted]') if key else message

def _models() -> list[str]:
    return list(dict.fromkeys([MODEL, *FALLBACK_MODELS]))

def _call_with_model_fallback(call: Callable[[Any, str], Any]) -> Any:
    client = _client()
    last: Exception | None = None
    try:
        for model in _models():
            try:
                return call(client, model)
            except TasteAgentError:
                raise
            except Exception as exc:
                last = exc
                if any((marker in str(exc).lower() for marker in _MODEL_MISSING)):
                    continue
                raise TasteAgentError(f'Gemini request failed ({exc.__class__.__name__}): {_redact(str(exc))[:300]}') from exc
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            close()
    raise TasteAgentError(f"No configured Gemini model is available ({', '.join(_models())}): {_redact(str(last))[:200]}")

def _generate(prompt: str, json_mode: bool=False, progress: Callable[[str, str], None] | None=None) -> str:

    def call(client, model):
        config = None
        if json_mode:
            from google.genai import types
            config = types.GenerateContentConfig(response_mime_type='application/json', temperature=0.3)
        if config is None:
            stream = client.models.generate_content_stream(model=model, contents=prompt)
        else:
            stream = client.models.generate_content_stream(model=model, contents=prompt, config=config)
        chunks: list[str] = []
        received = 0
        next_update = 160
        for chunk in stream:
            piece = getattr(chunk, 'text', None)
            if not isinstance(piece, str) or not piece:
                continue
            chunks.append(piece)
            received += len(piece)
            if progress and received >= next_update:
                progress('Interpreting recurring themes', f'Gemini is identifying recurring themes from the supplied entities and Qloo tags ({received} characters received).')
                next_update = received + 160
        text = ''.join(chunks)
        if not text.strip():
            raise TasteAgentError('Gemini returned an empty response.')
        return text
    return _call_with_model_fallback(call)

def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub('^```(?:json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
    start = text.find('{')
    if start == -1:
        raise TasteAgentError('Gemini returned a response that could not be parsed.')
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise TasteAgentError('Gemini returned invalid structured data.') from exc
    if not isinstance(value, dict):
        raise TasteAgentError('Gemini returned an unexpected response format.')
    return value

def interpret_vibe_prompt(prompt: str, allowed_categories: list[str], progress: Callable[[str, str], None] | None=None) -> dict[str, Any]:
    allowed = [str(c) for c in allowed_categories]
    payload = {'prompt': str(prompt or '')[:500], 'allowed_categories': allowed}
    instruction = "You are Curio's Vibe Check intent interpreter. Convert the user's request into a compact discovery plan. Return JSON only with keys: mood (array of short phrases), themes (array), categories (array chosen ONLY from allowed_categories), seed_terms (array of concise Qloo search phrases), reference_terms (exact named works explicitly referenced by the user, such as a film title after 'like' or 'similar to'), reference_attributes (specific genres, story traits, atmosphere, setting, style or sound that make the reference distinctive), required_genres (array of genre labels inferred from the prompt/reference), requested_count (integer, default 5), constraints (array), discovery_goal (short string). Carefully distinguish the requested medium/category from words like 'recommendations' or 'night in'. For songs/tracks, use the supported Artists category as the Qloo music signal but preserve requested_media as songs/tracks; never claim artist recommendations are songs. Identify named cultural works as reference_terms, not generic keywords. For 'movies that feel like The Conjuring', extract The Conjuring as a reference, Movies as the category, and supernatural/paranormal horror, haunted locations, investigations and suspense as attributes/genres where appropriate. Do not substitute the user's favourites for the reference. Do not recommend, name or invent candidate works. Do not infer personal traits. Preserve explicit constraints. If ambiguous, choose the smallest useful category set. Keep arrays concise (at most eight entries). Input JSON:\\n" + json.dumps(payload, ensure_ascii=False)
    data = _extract_json(_generate(instruction, json_mode=True, progress=progress))
    return {'mood': data.get('mood', []), 'themes': data.get('themes', []), 'categories': data.get('categories', []), 'seed_terms': data.get('seed_terms', []), 'reference_terms': data.get('reference_terms', []), 'reference_attributes': data.get('reference_attributes', []), 'required_genres': data.get('required_genres', []), 'requested_media': data.get('requested_media'), 'requested_count': data.get('requested_count', 5), 'constraints': data.get('constraints', []), 'discovery_goal': data.get('discovery_goal', ''), 'parser': 'gemini'}

def _compact(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in items:
        tags = item.get('tags') if isinstance(item.get('tags'), list) else []
        output.append({'name': item.get('name', ''), 'category': item.get('category', ''), 'description': str(item.get('description', ''))[:350], 'qloo_tags': [str(tag) for tag in tags][:8], 'source': item.get('source', '')})
    return output

def create_taste_profile(favourites: list[dict[str, Any]], journey: list[dict[str, Any]], progress: Callable[[str, str], None] | None=None) -> dict[str, Any]:
    payload = {'favourites': _compact(favourites), 'journey': _compact(journey[-20:])}
    prompt = "You are Curio's cultural taste analyst. Analyze only the supplied entities. Write all user-facing text in clear English. Return JSON with keys: title, summary, signals (array of 3-6 short strings), connections (array of objects with from, to, reason). Do not make psychological claims. Do not invent entities in connections. Use only titles present in the input. Each entity includes qloo_tags, tags that Qloo's taste graph attached to it; base signals and connection reasons on shared qloo_tags where you can, and do not invent tags. Input JSON:\n" + json.dumps(payload, ensure_ascii=False)
    data = _extract_json(_generate(prompt, json_mode=True, progress=progress))
    allowed_names: dict[str, str] = {}
    for item in [*favourites, *journey]:
        name = str(item.get('name') or '').strip()
        if name:
            allowed_names.setdefault(name.casefold(), name[:140])
    connections = []
    raw_connections = data.get('connections')
    if isinstance(raw_connections, list):
        for connection in raw_connections:
            if not isinstance(connection, dict):
                continue
            source = str(connection.get('from') or '').strip()
            target = str(connection.get('to') or '').strip()
            source_name = allowed_names.get(source.casefold())
            target_name = allowed_names.get(target.casefold())
            reason = connection.get('reason')
            if not source_name or not target_name or source_name.casefold() == target_name.casefold():
                continue
            if not isinstance(reason, str) or not reason.strip():
                continue
            connections.append({'from': source_name, 'to': target_name, 'reason': reason.strip()[:400]})
            if len(connections) >= 6:
                break
    raw_signals = data.get('signals')
    signals = [s.strip()[:100] for s in raw_signals if isinstance(s, str) and s.strip()][:6] if isinstance(raw_signals, list) else []
    return {'title': str(data.get('title') or '')[:140], 'summary': str(data.get('summary') or '')[:700], 'signals': signals, 'connections': connections}

def rank_candidates(taste_summary: dict[str, Any], goal: str | None, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {'taste': taste_summary, 'goal': (goal or '').strip()[:200] or None, 'candidates': candidates}
    prompt = 'You are Curio\'s planning agent. Qloo, a cultural taste graph, has returned the candidate discoveries below for one person. Choose five and order them as a discovery journey, so each step feels like a natural next stop after the previous one. Prefer variety across categories, but two from one category is fine if clearly better. If a goal is given, favour candidates that fit it. Use ONLY ids from the candidates and never add anything else. For each pick write one sentence (max 25 words) saying why it suits this person, grounded in something concrete from its \'about\' text or shared_tags or shared_terms (a theme, setting or mood). Vary the sentence structure, do not mention Qloo or \'taste graph\', and invent no facts. Return JSON only: {"theme": "max 8 words", "steps": [{"id": "<candidate id>", "note": "<one sentence>"}]}. Input JSON:\n' + json.dumps(payload, ensure_ascii=False)
    data = _extract_json(_generate(prompt, json_mode=True))
    candidate_ids = {str(candidate.get('id')) for candidate in candidates if isinstance(candidate, dict) and candidate.get('id') not in (None, '')}
    raw_steps = data.get('steps')
    steps = []
    seen_ids: set[str] = set()
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            candidate_id = str(step.get('id') or '')
            if not candidate_id or candidate_id not in candidate_ids or candidate_id in seen_ids:
                continue
            note = step.get('note')
            steps.append({'id': candidate_id, 'note': ' '.join(note.split())[:220] if isinstance(note, str) else ''})
            seen_ids.add(candidate_id)
            if len(steps) >= 5:
                break
    theme = data.get('theme')
    return {'theme': ' '.join(theme.split())[:80] if isinstance(theme, str) else '', 'steps': steps}

def narrate_path(start_name: str, hops: list[dict[str, Any]], start_about: str='') -> dict[str, Any]:
    prompt = f"""Qloo, a cultural taste graph, built a chain of discoveries: each item below was returned by Qloo as a match for the item before it, based on overlapping audiences. For every hop, in order, write ONE sentence (max 30 words) saying why someone who enjoyed the 'from' item might enjoy the 'to' item. Ground each sentence in something concrete from the two descriptions (from_about, to_about) or the shared_tags / shared_terms: a theme, setting, mood, format or subject. If the two descriptions have little in common, say honestly that the link comes from overlapping audiences and name how the two differ. Vary the structure: do not start two sentences the same way and do not reuse a template. Do not mention Qloo, 'taste graph' or the words 'shared tag' in the sentences. Invent no facts beyond the supplied text. Also write a short title for the whole chain (max 8 words). Return JSON only: {{"title": "...", "bridges": ["<sentence for hop 1>", "..."]}} with exactly {len(hops)} bridges. Input JSON:\n""" + json.dumps({'start': start_name, 'start_about': start_about[:240], 'hops': hops}, ensure_ascii=False)
    data = _extract_json(_generate(prompt, json_mode=True))
    bridges = data.get('bridges')
    cleaned = []
    if isinstance(bridges, list):
        for bridge in bridges:
            if isinstance(bridge, str):
                value = ' '.join(bridge.split()).strip()
                cleaned.append(value[:300] if value else '')
            else:
                cleaned.append('')
    return {'title': str(data.get('title') or '')[:120], 'bridges': cleaned[:len(hops)]}
