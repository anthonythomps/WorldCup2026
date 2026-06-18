# World Cup Sweepstake Dashboard

Streamlit dashboard for a World Cup sweepstake with three prize views:

- Worst team: lowest group-stage points, then goal difference, then goals scored.
- Overall winner: highest points, then goal difference, then goals scored.
- Best combined record: combined points, goal difference, and goals scored across each person's four teams.

The app uses the Zafronix World Cup API as the source of truth and stores responses in `storage.db`. Refreshes run through conditional GETs with ETag support, so repeated refreshes are cheap when the API returns `304 Not Modified`.

## Setup

```bash
cd "/Users/anthomps/Library/CloudStorage/OneDrive-OracleCorporation/Visual Studio/WorldCup2026"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The API key is read from `ZAFRONIX_API_KEY` or `.streamlit/secrets.toml`. The local secrets file is ignored by Git.

## Configure The Draw

Edit `config.yaml`:

```yaml
sweepstake:
  Alice:
    - Team One
    - Team Two
    - Team Three
    - Team Four
```

Each person should have exactly four teams. Teams not listed in the draw still appear in the team table as `Unassigned`.

## Cache Behavior

- `GET /tournaments`
- `GET /matches?year=YYYY`
- `GET /standings?year=YYYY`

The app does not use live endpoints. It validates the cache every 10 minutes by default and also has a manual refresh button in the sidebar.
