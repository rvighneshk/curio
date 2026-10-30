# Curio — Same Cultural Intelligence. Different Journeys.

**Discover what you'll love next. Create meaningful moments with someone you care about.**

Curio is an agentic cultural discovery companion powered by **Qloo** and **Google Gemini**. Its home page brings together two experiences built on a shared cultural-intelligence foundation:

- **Personal Discovery:** Follow your taste, uncover unexpected cultural connections, and find your next favourite across culture.
- **Memory Lane:** Help caregivers plan personalised reminiscence sessions using familiar cultural references and conversation starters.

Curio explores how cultural intelligence can support both individual discovery and meaningful moments between people.

## Links

- **Live Demo:** YOUR_LIVE_DEMO_URL
- **Public Source Repository:** https://github.com/rvighneshk/curio
- **License:** [MIT License](LICENSE)

## The Problem

People have more cultural content available than ever, but an abundance of choice can make it harder to decide what to watch, read, or listen to. Conventional recommendation experiences can also keep people within familiar categories instead of helping them discover unexpected connections.

At the same time, caregivers may want to create meaningful reminiscence experiences but need help finding familiar references and conversation starters that are relevant to the person and their background.

Curio approaches these needs through two distinct experiences: one that helps people discover what they might love next, and another that helps caregivers create thoughtful moments through cultural memories.

## Two Experiences, One Cultural Intelligence Foundation

### 1. Personal Discovery — Find What You'll Love Next

Personal Discovery helps users move beyond endless browsing. A favourite movie can become a starting point for discovering a book, television series, podcast, or artist they might not have considered.

**Key features**

- **Personalised Discovery:** Start with favourite movies, TV shows, books, podcasts, and artists.
- **Vibe Check:** Describe what you feel like discovering in natural language. Gemini interprets the request and Curio uses Qloo-grounded cultural retrieval to find relevant recommendations.
- **Take Me Deeper:** Continue exploring cultural connections from a selected discovery through a multi-step journey.
- **Plan My Next Five:** Get a focused sequence of five potential discoveries to help decide what to explore next.
- **Taste Profile:** Explore an interpretation of your interests and preferences.
- **Curio Collection:** Save and organise discoveries you want to revisit.
- **Cultural Passport:** Build a personal record of your cultural exploration and the discoveries that shape your interests.

### 2. Memory Lane — Plan a Meaningful Moment Together

Memory Lane is designed for caregivers who want to create thoughtful reminiscence experiences with someone they care about. It uses familiar cultural references as conversation starters, helping caregivers plan sessions around the person's background and interests.

A caregiver can provide context such as birth year, cultural background, and favourite references. Curio uses this information to help generate a personalised reminiscence session. After the session, the caregiver can record reactions, helping inform and adapt future sessions.

**Key features**

- **Personalised Context:** Build a session around the person's background, era, and familiar interests.
- **Reminiscence Sessions:** Generate cultural prompts and conversation starters intended to encourage meaningful moments.
- **Caregiver Feedback:** Record reactions after a session and use that feedback to guide future sessions.
- **Curio Collection Connection:** Bring suitable saved discoveries into a Memory Lane session where supported.

Memory Lane is intended to support caregiver-led reminiscence and conversation. It is not a medical, diagnostic, or therapeutic tool.

## Why Qloo Makes Curio Different

Curio is not designed to rely on a language model guessing what people might enjoy. Qloo provides the cultural-intelligence foundation for discovering relationships and affinities between supported cultural entities.

In Personal Discovery, Curio uses Qloo's API for cultural entity resolution and recommendation retrieval. This gives discovery workflows a source of cultural signals beyond a generic language-model response or a simple keyword match.

Gemini complements that foundation. It helps interpret natural-language requests, structure discovery goals, organise multi-step workflows, and explain or present results. Curio validates and filters retrieved candidates so that recommendations remain grounded in the available cultural results rather than being freely invented by the language model.

Memory Lane applies the same broader idea to a different goal: using familiar cultural references to help caregivers create personalised reminiscence sessions. Its purpose is not to recommend more entertainment, but to support meaningful conversation and connection.

**Qloo contributes cultural grounding. Gemini contributes language understanding and reasoning. Curio brings them together in experiences designed around people.**

Recommendations reflect inferred cultural affinities; they are not guarantees that every person who likes one item will like another.

## How the Agentic Workflow Works

Curio is a **bounded agentic AI application**. It interprets user goals and coordinates model and API capabilities through defined, multi-step workflows.

A typical Personal Discovery workflow involves:

1. **Understand the goal:** Gemini interprets the user's natural-language request or discovery intent.
2. **Structure the request:** Curio identifies relevant categories, references, genres, moods, themes, constraints, and desired result count where applicable.
3. **Resolve cultural entities:** Qloo is used to identify supported entities and provide cultural recommendation signals.
4. **Retrieve and explore:** Curio uses Qloo-grounded results to retrieve relevant candidates and, for deeper exploration, continue through additional discovery steps.
5. **Validate and organise:** Curio filters results, manages duplicates and relevance constraints, and uses Gemini where appropriate to rank, interpret, or explain candidates.
6. **Present the journey:** Results are delivered through discovery experiences such as Vibe Check, Take Me Deeper, and Plan My Next Five.

Memory Lane follows a separate caregiver-led workflow: it uses the supplied personal context to generate a reminiscence session, records caregiver reactions, and uses that feedback to inform later sessions where supported.

Curio is not a fully autonomous system operating continuously in the background. Its agentic behaviour is bounded by user goals, defined workflows, available APIs, validation rules, and implemented fallback behaviour.

## Technology Stack

- **Python** — application logic and backend workflows.
- **FastAPI** — backend application framework.
- **Jinja** — server-rendered HTML templates.
- **HTML, CSS, and JavaScript** — frontend interface and interactions.
- **Qloo API** — cultural intelligence, entity resolution, and recommendation retrieval.
- **Google Gemini API** — natural-language understanding, interpretation, planning, and AI-assisted explanations or session generation.

## Running Curio Locally

### Prerequisites

- Python version compatible with the project's dependencies.
- Git.
- A Qloo API key for Qloo-powered features.
- A Google Gemini API key for Gemini-powered features.

### 1. Clone the Repository

```bash
git clone https://github.com/rvighneshk/curio.git
cd curio
```

### 2. Create and Activate a Virtual Environment

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

**macOS or Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure API Credentials

Configure API keys using the environment-variable names expected by the application. If the project uses a local `.env` file, it may look similar to:

```dotenv
QLOO_API_KEY=your_qloo_api_key
GEMINI_API_KEY=your_gemini_api_key
```

Use the exact variable names expected by the current code. Keep credentials private: never commit a real `.env` file or API keys to the public repository. If you provide an `.env.example`, include placeholders only.

Qloo credentials are required for live Qloo-powered functionality. Gemini-dependent features may use fallback behaviour where implemented.

### 5. Start the Application

Start the FastAPI application using the entry point configured in the repository. For example, if the FastAPI application object is named `app` in `main.py`:

```bash
uvicorn main:app --reload
```

If the module or application object has a different name, adjust the command accordingly. Open the local URL shown in the terminal.

## Deployment

Curio is designed to be deployed as a publicly accessible web application. A production deployment should:

- Run the FastAPI backend and serve the required frontend assets.
- Configure API credentials securely through the hosting provider's environment settings.
- Keep private keys on the server and out of browser-side code.
- Make both Personal Discovery and Memory Lane available to users.
- Handle external API failures and optional AI features gracefully.
- Provide access to the functional application without private permissions.

**Live Demo:** YOUR_LIVE_DEMO_URL

## Privacy and Responsible Use

Curio integrates external services to provide cultural recommendations and AI-assisted experiences.

- Never commit API credentials to the repository.
- Do not expose server-side credentials in frontend code or public assets.
- Configure production secrets through the hosting provider.
- Avoid entering sensitive personal information unless it is necessary for the experience and you understand how connected services process submitted data.
- Memory Lane is intended to support caregiver-led reminiscence and conversation. It does not provide medical advice, diagnosis, or therapy.

The availability and persistence of saved collections, Cultural Passports, profiles, and session feedback depend on the application's configured storage and deployment. Do not assume cross-device synchronisation or long-term retention unless the deployed implementation explicitly supports it.

## License

Curio is released under the MIT License. See [LICENSE](LICENSE) for the full terms. Third-party libraries, external APIs, and assets remain subject to their respective licences and terms.

## Built for the Qloo Agentic Hackathon 2026

Curio explores how cultural intelligence and agentic AI can support two different but connected experiences: discovering what you might love next and creating meaningful moments through familiar cultural memories.

**You already know what matters to you. Let it open a new journey.**

---

*Curio — Same cultural intelligence. Different journeys.*
