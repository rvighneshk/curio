import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import memory
import taste_agent

def main() -> None:
    key = sys.argv[1] if len(sys.argv) > 1 else 'classic-hollywood'
    preset = memory.resolve_preset(key)
    print(f"Preset: {preset['label']} (born {preset['birth_year']}, {preset['region']})")
    for seed in preset['seeds']:
        print(f"  seed -> {seed['name']} [{seed['category']}] id={seed['entity_id'][:8]}…  <- check this is the RIGHT entity")
    profile = {'birth_year': preset['birth_year'], 'region': preset['region']}
    result = memory.run_memory_agent(profile, preset['seeds'], use_gemini=taste_agent.gemini_configured(), progress=lambda title, detail: print(f'  … {title}: {detail}'))
    print(f"\nmode: {result['mode']}" + (f"\nnote: {result['note']}" if result.get('note') else ''))
    print(f"era: {result['era']}  stats: {result['stats']}")
    print('\nTRACE')
    for step in result['trace']:
        print(f"  {step['step']:>2}. [{step['tool']}] {step['summary']}")
    for label in ('music', 'screen'):
        print(f'\n{label.upper()}')
        for item in result[label]:
            e = item['entity']
            print(f"  - {e['name']}  ({e.get('year') or 'no year'})  spotify_id={('yes' if e.get('spotify_id') else 'no')}")
            print(f"      why:    {item['why']}")
            print(f"      prompt: {item['prompt']}")
    print('\nADAPTATIONS:', result['adaptations'] or 'none')
    Path('memory_live_result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print('\nFull result saved to memory_live_result.json (paste it back if something looks off).')
if __name__ == '__main__':
    main()
