# Curio — Qloo-grounded cultural discovery, now with Memory Lane

**Curio** turns a favourite into a path across movies, TV, books, podcasts and music, and shows the Qloo evidence behind every pick.

**Memory Lane** is a Curio agent for caregivers. Give it a birth year and a few things someone loved, and it plans a gentle reminiscence session (music, shows, conversation starters), then adapts to how they respond. Cultural specificity is the point: the right era and the right culture, not a generic "oldies" playlist.

## Links

- **Live demo:** https://curio-crjr.onrender.com/
- **Source repository:** https://github.com/rvighneshk/curio
- **License:** [MIT](LICENSE)

## Memory Lane (agent)

**The problem.** Reminiscence (music, shows and stories from a person's formative years) is a common way for caregivers to connect with someone living with dementia. Generic "oldies" playlists miss: what resonates depends on *when* and *where* someone grew up. This is a conversation aid for caregivers, **not medical advice or therapy**.

**What it does.** The caregiver enters a birth year, an optional region or culture note, and up to six things the person loved (artists, TV shows, films; each picked from Qloo search results, so the entity is confirmed). The agent returns a session:

- **Listen:** artists from the same world, with notable songs and an in-page Spotify player.
- **Watch & talk:** TV shows and films from the years that tend to stay with us (roughly ages 10–30).
- **Conversation starters:** open, warm questions built from Qloo's own fields (never quiz-style).
- **Why it's here:** the Qloo evidence for every item: match score, shared tags, which of their favourites it came from.
- **Reactions:** the caregiver taps 😊 😐 😟 on each item, and **Adapt the plan** re-plans from those observations.
- **Compare with Gemini alone:** the same request answered with no tools and no Qloo, side by side.

**Why it is an agent.**

1. **The model chooses the actions.** With a Gemini key, Gemini is given two real tools, `explore` (a Qloo insights query: category, seeds, release-year window, deeper cuts, older audience) and `inspect_item`. It decides which to call and with what, reads each result, and **adapts**: if a query comes back thin or off-era it widens the window, drops a filter or changes seeds.
2. **Grounded by construction.** Every pick is a handle that resolves to an entity Qloo returned. Anything the model writes that doesn't is discarded, and the session reports that every pick was verified.
3. **Reactions change the strategy, not just the list.** Loved items become new Qloo seeds, the era window narrows to where loved shows and films cluster, upset items are excluded for good, items already shown aren't repeated, and the plan says what changed and why.
4. **Never a dead end.** If Gemini is unavailable or misbehaves, a deterministic planner runs the same explore → check → adapt loop using rules, so Memory Lane still works (the session says which mode ran).

**Qloo features used.** Entity search across Artists, TV Shows and Movies; multi-signal insights (cross-domain: TV and film seeds drive music); `filter.release_year` for era; `filter.popularity.max` for deeper cuts; `signal.demographics.age` (55+) for older audiences; explainability (per-seed contribution scores shown as the reason); entity facts and IDs (notable songs, Spotify IDs) for the session cards. Qloo is the *only* source of items.

**Honest limits.** Qloo is strongest on well-covered eras and markets; where it returns too little for a window, Memory Lane widens it and says so. Artists carry no release year, so music is era-matched through the TV and film seeds. Descriptions come from Qloo and may contain errors. The LLM-only comparison uses the model's own stated release years, which can themselves be wrong.

**Privacy.** Don't enter names or sensitive details. Birth year, region and favourites stay in your browser and a short-lived server session; Gemini only sees the items you pick.

## The problem (Curio discovery)

More choice does not always make choosing easier. Entertainment discovery can become a loop of browsing, comparing, and postponing a decision. Familiar recommendation systems can also keep people close to the categories and tastes they already know.

Curio reframes discovery around a simple question: **Where could something I already love take me next?** A favourite becomes the starting point for cultural connections across categories, with user feedback shaping the journey.

## Features

### Personalised onboarding

Choose favourite items from five supported categories:

- Movies
- TV Shows
- Books
- Podcasts
- Artists

These favourites give Curio starting signals for recommendations.

![Curio onboarding — selecting favourite cultural items](docs/screenshots/onboarding.png)

### Open a Portal

Choose a favourite as the starting point for exploration. A portal uses that item as a signal for finding cultural affinities, rather than limiting discovery to more items of the same kind.

Some entities returned by search cannot be used by Qloo as insight signals. When Qloo rejects a selected seed for that reason, Curio shows a friendly notice that the portal is temporarily unavailable instead of exposing the provider's raw error response. This is a graceful failure, not a guarantee that every searchable entity can produce recommendations.

![Curio portals — choosing a favourite as a discovery starting point](docs/screenshots/portals.png)

### Cross-category recommendations

Explore recommendations across the supported categories. Qloo supplies candidate entities; Curio applies its discovery and ranking logic to help surface useful next steps. Depending on the workflow, candidates can be filtered or scored using tags, descriptive overlap, affinity signals, popularity preferences, and exclusions.

![Curio cross-category recommendation cards](docs/screenshots/recommendations.png)

### Like or skip

- **Like** an item to add it to your collection and Cultural Journey.
- **Skip** an item to record that it is not a fit for the current exploration.
- Continue from discoveries while Curio accounts for previously seen or excluded items where applicable.

### Visual Cultural Journey

Liked discoveries form a visual journey that shows how exploration can move from one item to another across categories.

![Curio visual Cultural Journey](docs/screenshots/cultural-journey.png)

### Taste Profile

Curio can interpret the user's favourites and recent journey as a Taste Profile. When Gemini is configured and available, it helps explain recurring themes. The profile is an interpretation of supplied cultural data, not a definitive psychological assessment.

![Curio Taste Profile](docs/screenshots/taste-profile.png)

### Take Me Deeper

Continue exploring from a selected item with a multi-hop discovery path. Curio queries Qloo as it explores subsequent steps, avoids revisiting items, and scores candidates using available evidence such as shared tags, shared descriptive terms, and affinity. Gemini can optionally write short explanations for the connections; the path itself is grounded in Qloo-returned candidates.

![Curio Take Me Deeper exploration](docs/screenshots/take-me-deeper.png)

### Plan My Next Five

Get a focused sequence of five potential discoveries instead of an open-ended browsing session. Qloo provides the candidate pool. Curio's Python workflow validates and filters results, and Gemini can optionally help rank and explain the candidates. A deterministic Qloo-based fallback supports the feature when Gemini is unavailable or its response cannot be used.

![Curio Plan My Next Five](docs/screenshots/plan-my-next-five.png)

## How the discovery agent works

Curio uses a **hybrid, Qloo-grounded agent workflow**:

1. **Retrieve:** the backend searches Qloo for entities and recommendation candidates in supported categories.
2. **Explore:** deterministic Python logic manages discovery steps, category selection, exclusions, evidence scoring, and fallback behaviour.
3. **Interpret (optional):** Gemini can rank a supplied candidate set, explain a taste profile, or narrate connections between supplied path items.
4. **Validate:** model output is checked against the supplied Qloo candidates or source entities before it is accepted. Gemini is not allowed to introduce arbitrary new recommendation IDs into these workflows.
5. **Present:** the app returns recommendations, explanations, and trace information for the interface.

Gemini does not independently choose and invoke arbitrary tools in an open-ended loop. The application orchestrates the workflow and decides when to call Qloo or Gemini. If Gemini is not configured or a call fails, supported features use deterministic or Qloo-only fallback behaviour where implemented.

Qloo recommendations represent inferred cultural affinities; they are not guarantees that every person who likes one item will like another.

## Technology stack

- **Python** — backend and discovery orchestration
- **FastAPI** — HTTP API and application server
- **Jinja2, HTML, CSS, JavaScript** — rendered interface and interactions
- **Qloo API** — entity search and cultural recommendation candidates
- **Google Gemini API (`google-genai`)** — optional interpretation, ranking, and narration
- **SQLite** — persisted session data

## Run locally

### Requirements

- Python 3.10 or newer recommended
- A Qloo API key for live search and recommendation workflows
- A Gemini API key for Gemini-assisted features (optional; fallback behaviour is available for supported workflows)
- Git

### 1. Clone the repository

```bash
git clone https://github.com/rvighneshk/curio.git
cd curio
```

### 2. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows Command Prompt**

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

For running Curio:

```bash
python -m pip install -r requirements.txt
```

For local development and the full API test suite, install the development dependencies instead (this includes the runtime requirements and `httpx` for FastAPI's test client):

```bash
python -m pip install -r requirements-dev.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and set `QLOO_API_KEY`. Add `GEMINI_API_KEY` if you want Gemini-assisted features. The application loads environment variables with `python-dotenv`.

**Minimum configuration:**

```dotenv
QLOO_API_KEY=your_qloo_api_key
GEMINI_API_KEY=your_gemini_api_key
```

`GEMINI_API_KEY` can be left empty if you do not want Gemini-assisted features. Never commit a real `.env` file or API credentials.

#### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `QLOO_API_KEY` | Authentication for Qloo API calls | None; required for live Qloo requests |
| `QLOO_BASE_URL` | Qloo API base URL | `https://hackathon.api.qloo.com` |
| `GEMINI_API_KEY` | Enables Gemini-assisted workflows | Empty / not configured |
| `GEMINI_MODEL` | Primary Gemini model ID | `gemini-3.5-flash-lite` |
| `GEMINI_FALLBACK_MODELS` | Comma-separated model IDs to try if the configured model is unavailable | `gemini-3.1-flash-lite,gemini-2.5-flash` |
| `CURIO_DB_PATH` | SQLite database file path | `./data/curio.db` relative to the application directory |
| `CURIO_SESSION_TTL_DAYS` | Age threshold for pruning stored sessions at startup | `30` |
| `CURIO_QLOO_RPM` | Per-client Qloo request limit per minute | `60` |
| `CURIO_AI_RPM` | Per-client AI request limit per minute | `10` |
| `CURIO_AI_DAILY_CAP` | Daily AI request cap configured by the app | `500` |
| `CURIO_TRUST_PROXY` | Trust `X-Forwarded-For` for client IPs when set to `1` | `0` |
| `CURIO_CORS_ORIGINS` | Comma-separated allowed cross-origin origins | Empty (same-origin behaviour) |
| `QLOO_CACHE_TTL` | Qloo response cache lifetime in seconds | `900` |
| `QLOO_GEMS_MAX` | Popularity ceiling for the “gems” filter | `0.5` |
| `QLOO_POPULAR_MIN` | Popularity floor for the “popular” filter | `0.7` |

### 5. Start the application

From the repository root, run:

```bash
python -m uvicorn main:app --reload
```

Open http://127.0.0.1:8000 in your browser. The health endpoint is available at http://127.0.0.1:8000/health.

## Run the tests

Install the development dependencies first:

```bash
python -m pip install -r requirements-dev.txt
```

Then run the test suite from the repository root:

```bash
python -m unittest discover -s tests -q
```

To make Python display `ResourceWarning` messages during the run:

```bash
python -W always::ResourceWarning -m unittest discover -s tests -q
```

A small browser-side smoke test for Memory Lane rendering (needs only Node):

```bash
node tests/js/memory_smoke.js
```

To exercise Memory Lane against the real services (needs `QLOO_API_KEY`; add `GEMINI_API_KEY` to test the tool-calling loop):

```bash
python scripts/memory_live_check.py classic-hollywood   # or malay-pop
```

Tests exercise application behaviour and mocked integrations; passing tests do not by themselves verify that live Qloo or Gemini credentials and external services are working.

## API overview

The FastAPI application exposes routes for the interface and its workflows. Useful route groups include:

- `GET /health` — service and Qloo configuration status
- `GET /api/categories` and `GET /api/search` — supported categories and entity search
- `/api/onboarding/favourites` — read and manage onboarding favourites
- `/api/recommendations` and `/api/recommendations/actions` — retrieve recommendations and record user actions
- `/api/collection` and `/api/journey/{journey_id}` — collection and journey data
- `/api/gemini/taste-profile` — taste-profile generation
- `/api/deeper/path` — multi-hop discovery path
- `/api/agent/path` — guided discovery plan

Some longer-running AI-assisted workflows also expose `/stream` endpoints. Request models and required headers are defined in `main.py`; session-aware endpoints use the `X-Session-ID` header.

### Memory Lane endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/memory/presets` | Example profiles |
| POST | `/api/memory/preset/{key}` | Resolve a preset into real Qloo entities |
| POST | `/api/memory/session` / `/session/stream` | Plan a session (`adapt: true` uses saved reactions); the stream variant sends live progress |
| POST | `/api/memory/reaction` | Record 😊 / 😐 / 😟 for an item |
| POST | `/api/memory/baseline` | The same request answered by Gemini alone, for comparison |
| POST | `/api/memory/reset` | Clear Memory Lane state |

## Deployment notes

Curio includes deployment configuration in `Dockerfile` and `render.yaml`. Configure secrets in the hosting provider rather than in source control.

**Render free-plan note:** the current Render configuration stores SQLite at `/tmp/curio.db`. Render free instances use ephemeral storage, so session data—including favourites, collections, and Cultural Journey state—may be lost when the instance restarts or redeploys. This is an expected hosting limitation, not a permanent-storage guarantee. Curio's demo remains usable, but don't rely on the free instance to preserve saved session data. For durable storage, use a deployment with a persistent writable disk/volume and set `CURIO_DB_PATH` to a path on that volume (for example, `/data/curio.db`).

Other deployment considerations:

- Ensure the Qloo key is present in the server environment.
- Set the Gemini key if Gemini-assisted features are desired.
- Configure `CURIO_CORS_ORIGINS` only for the origins that need cross-origin access.
- Set `CURIO_TRUST_PROXY=1` only when the app is behind a trusted proxy that supplies `X-Forwarded-For` correctly.
- Observe provider rate limits and costs; the app's own request limits do not replace provider-side controls.

**Live demo:** https://curio-crjr.onrender.com/

## Privacy and credentials

- Keep API keys out of the repository and browser code.
- Curio stores session state in SQLite. Configure retention and persistent storage to match the deployment's needs.
- Favourites, liked or skipped items, and journey data are used to support the discovery experience.
- Gemini receives compacted favourites, journey items, and recommendation evidence for the AI-assisted feature being run. Avoid submitting sensitive personal information.

## License

Curio is released under the MIT License. See [LICENSE](LICENSE) for the full terms. Third-party libraries, external APIs, and assets remain subject to their respective licences and terms.

## Built for the Qloo Agentic Hackathon 2026

Curio explores how cultural intelligence and AI-assisted interpretation can help people move beyond decision fatigue. **You already know what you love. Let it open a portal to what you'll love next.**

---

*Curio — Every favourite is a portal.*


## Vibe Check

Vibe Check turns natural-language requests into structured discovery intent. Gemini extracts requested categories, reference works, genres, moods, themes and constraints. Curio resolves reference works through Qloo and uses them as primary discovery signals, with the user's existing favourites as supporting context. Recommendations remain grounded in Qloo entities; Curio broadens retrieval when metadata is incomplete and avoids padding results with unrelated items. A deterministic parser provides a fallback when Gemini is unavailable.

## Memory Lane

Memory Lane is a caregiver-focused reminiscence experience built around birth year, cultural context, favourite items, generated sessions and caregiver reactions. Gemini helps plan the session using Qloo-grounded results, with a Qloo-only fallback when the Gemini planner is temporarily unavailable. Caregiver reactions can be used to adapt later sessions.

## Personal discovery

Personal discovery uses the taste profile to power Plan My Next Five and Take Me Deeper. Users can save favourites, like or skip recommendations, explore cultural connections and import loved discoveries into Memory Lane.
