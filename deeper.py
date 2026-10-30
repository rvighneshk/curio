from __future__ import annotations
from collections import Counter
from typing import Any, Callable
import qloo_client
import taste
from qloo_client import ENTITY_TYPES, QlooBadRequest, QlooError, search_entities
DEFAULT_HOPS = 5
CATEGORY_ORDER = list(ENTITY_TYPES)
MAX_CATEGORY_TRIES = len(CATEGORY_ORDER)

class DeeperPathError(RuntimeError):
    pass

def _category_order(current: dict[str, Any], usage: Counter) -> list[str]:
    return sorted(CATEGORY_ORDER, key=lambda c: (c == current.get('category'), usage[c], CATEGORY_ORDER.index(c)))

def build_path(start: dict[str, Any], blocked_ids: list[str] | None=None, hops: int=DEFAULT_HOPS, recommend: Callable[..., list[dict[str, Any]]] | None=None, popularity: str | None=None, progress: Callable[[str, str], None] | None=None) -> dict[str, Any]:
    recommend = recommend or qloo_client.get_recommendations
    start_id = str(start.get('entity_id') or '')
    if not start_id:
        raise DeeperPathError('The starting item has no Qloo entity id.')
    blocked = {str(i) for i in blocked_ids or []} | {start_id}
    path_ids: list[str] = [start_id]
    usage: Counter = Counter()
    trace: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    errors: list[str] = []
    current = start

    def log(summary: str, **details: Any) -> None:
        entry = {'step': len(trace) + 1, 'tool': 'qloo', 'summary': summary}
        if details:
            entry['details'] = details
        trace.append(entry)
        if progress:
            is_provider_error = details.get('outcome') == 'error' or 'Qloo error (' in summary
            if is_provider_error:
                progress('Trying another cultural connection', "That route wasn't available, so Curio is checking another category.")
            else:
                progress('Following Qloo connections', summary)
    for hop in range(1, max(1, min(hops, 8)) + 1):
        if progress:
            progress('Exploring the next hop', f"Finding connection {hop} of {max(1, min(hops, 8))} from {current.get('name', 'the previous discovery')}.")
        picked: tuple[dict[str, Any], dict[str, Any], str] | None = None
        best_seen: tuple[float, dict[str, Any], dict[str, Any], str] | None = None
        with_results = 0
        for category in _category_order(current, usage):
            if with_results >= MAX_CATEGORY_TRIES:
                break
            meta: dict[str, Any] = {}
            try:
                found = recommend([str(current['entity_id'])], category, limit=10, exclude_ids=[*sorted(blocked - set(path_ids)), *path_ids], explain=True, signal_names={str(current['entity_id']): str(current.get('name', ''))}, popularity=popularity, meta=meta)
            except (QlooError, ValueError) as exc:
                recovered = False
                message = str(exc).casefold()
                if isinstance(exc, QlooBadRequest) and 'signal.interests.entities' in message and ('invalid' in message or 'valid signal' in message):
                    source_category = current.get('category')
                    if source_category in ENTITY_TYPES and current.get('name'):
                        try:
                            matches = search_entities(str(current['name']), source_category, limit=5)
                            current_id = str(current.get('entity_id'))
                            canonical = next((match for match in matches if str(match.get('entity_id')) == current_id), None)
                            if canonical is None and matches:
                                canonical = next((match for match in matches if str(match.get('name', '')).casefold() == str(current.get('name', '')).casefold()), None)
                            if canonical and str(canonical.get('entity_id')) != current_id:
                                old_id = current_id
                                current = {**current, **canonical}
                                path_ids[-1] = str(current['entity_id'])
                                log(f"Hop {hop}: refreshed Qloo's entity ID for {current.get('name')} before retrying {category}.", hop=hop, category=category, recovery='canonical_entity_id', recovered=True)
                                found = recommend([str(current['entity_id'])], category, limit=10, exclude_ids=[*sorted(blocked - set(path_ids)), *path_ids], explain=True, signal_names={str(current['entity_id']): str(current.get('name', ''))}, popularity=popularity, meta=meta)
                                recovered = True
                        except (QlooError, ValueError) as retry_exc:
                            errors.append(str(retry_exc))
                            log(f'Hop {hop}: refreshed-ID retry for {category} also failed ({retry_exc}).', hop=hop, category=category, recovery='canonical_entity_id', recovered=False, error=str(retry_exc))
                if not recovered:
                    errors.append(str(exc))
                    log(f"Hop {hop}: {category} from {current.get('name')}: Qloo error ({exc}).", hop=hop, category=category, source=current.get('name'), outcome='error', error=str(exc))
                    continue
            scored = []
            for index, cand in enumerate(found):
                cid = str(cand.get('entity_id') or '')
                if not cid or cid in blocked or cid in path_ids:
                    continue
                evidence = taste.build_evidence([current], cand)
                score = 2 * len(evidence['shared_tags']) + len(evidence['shared_terms']) + 0.5 * len(taste.description_overlap(start, cand)) + (evidence['affinity'] or 0)
                scored.append((-score, index, taste.compact_entity({**cand, 'evidence': evidence}), evidence))
            if not scored:
                log(f"Hop {hop}: {category} from {current.get('name')}: nothing new.", hop=hop, category=category, source=current.get('name'), outcome='no_candidates', raw_candidate_count=len(found))
                continue
            with_results += 1
            scored.sort(key=lambda row: (row[0], row[1]))
            neg_score, _, entity, evidence = scored[0]
            if best_seen is None or -neg_score > best_seen[0]:
                best_seen = (-neg_score, entity, evidence, category)
            if evidence['shared_tags'] or evidence['shared_terms']:
                picked = (entity, evidence, category)
                log(f"Hop {hop}: {category} produced a related candidate ({entity['name']}).", hop=hop, category=category, source=current.get('name'), outcome='related_candidate', candidate_count=len(scored), best_candidate=entity.get('name'), score=-neg_score, shared_tags=evidence['shared_tags'], shared_terms=evidence['shared_terms'], affinity=evidence.get('affinity'))
                break
            log(f"Hop {hop}: best {category} match ({entity['name']}) had nothing in common with {current.get('name')} beyond audience overlap; checking another category.", hop=hop, category=category, source=current.get('name'), outcome='audience_overlap_only', candidate_count=len(scored), best_candidate=entity.get('name'), score=-neg_score, shared_tags=evidence['shared_tags'], shared_terms=evidence['shared_terms'], affinity=evidence.get('affinity'))
        if not picked and best_seen:
            picked = (best_seen[1], best_seen[2], best_seen[3])
            log(f"Hop {hop}: no category had a related match; using the strongest candidate seen ({best_seen[1]['name']}).", hop=hop, category=best_seen[3], outcome='best_available_fallback', best_candidate=best_seen[1].get('name'), score=best_seen[0], shared_tags=best_seen[2]['shared_tags'], shared_terms=best_seen[2]['shared_terms'], affinity=best_seen[2].get('affinity'))
        if not picked:
            break
        entity, evidence, category = picked
        usage[category] += 1
        path_ids.append(entity['entity_id'])
        steps.append({'order': hop, 'entity': entity, 'evidence': evidence, 'from': current.get('name'), 'why': taste.explain_text(str(current.get('name', '')), entity['name'], evidence), 'note': ''})
        shared = ', '.join([*evidence['shared_tags'][:2], *evidence['shared_terms'][:2]]) or 'audience overlap only'
        log(f"Hop {hop}: asked Qloo about {current.get('name')} -> {category}: {entity['name']} ({shared}).", hop=hop, source=current.get('name'), selected_candidate=entity.get('name'), selected_entity_id=entity.get('entity_id'), category=category, shared_tags=evidence['shared_tags'], shared_terms=evidence['shared_terms'], affinity=evidence.get('affinity'))
        current = entity
    if not steps and errors:
        raise QlooError(errors[-1])
    note = None
    if len(steps) < hops:
        note = f"Qloo had no further new discoveries after {len(steps)} hop{('' if len(steps) == 1 else 's')}." if steps else 'Qloo had no new discoveries from this item.'
    return {'title': f"A path from {start.get('name', 'your pick')}", 'start': taste.compact_entity(start), 'steps': steps, 'mode': 'qloo', 'note': note, 'trace': trace}

def narration_payload(result: dict[str, Any]) -> list[dict[str, Any]]:
    hops, previous = ([], result.get('start') or {})
    for step in result['steps']:
        entity = step['entity']
        hops.append({'from': step['from'], 'from_about': taste.description_of(previous)[:240], 'to': entity['name'], 'to_about': taste.description_of(entity)[:240], 'category': entity.get('category'), 'shared_tags': step['evidence'].get('shared_tags', []), 'shared_terms': step['evidence'].get('shared_terms', [])})
        previous = entity
    return hops

def apply_narration(result: dict[str, Any], narrated: dict[str, Any]) -> dict[str, Any]:
    bridges = narrated.get('bridges') or []
    applied = 0
    for step, bridge in zip(result['steps'], bridges):
        if isinstance(bridge, str) and bridge.strip():
            step['note'] = bridge.strip()
            applied += 1
    if narrated.get('title'):
        result['title'] = narrated['title']
    if applied:
        result['mode'] = 'gemini'
        result['trace'].append({'step': len(result['trace']) + 1, 'tool': 'gemini', 'summary': f"Wrote {applied} bridge sentence{('' if applied == 1 else 's')} from the tags Qloo returned."})
    return result
