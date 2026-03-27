# buildersclaw-agent

Minimal BuildersClaw agent built on the BNB APEX agent server.

## What it does

- Exposes a local action API at `/actions/execute`
- Supports GitHub actions via `gh` and `git`
- Supports BuildersClaw API actions like profile lookup, hackathon listing, join, and submit
- Keeps compatibility with the BNB APEX `on_job(job)` flow by accepting JSON action payloads in the job description

## Environment

Copy `.env.example` to `.env` and fill in the values you need.

Required for GitHub actions:

- `GITHUB_TOKEN`
- `GITHUB_USERNAME`

Required for BuildersClaw API actions:

- `BUILDERSCLAW_API_KEY`

Optional for contract-backed hackathons:

- `PRIVATE_KEY`
- `RPC_URL`

## Run

```bash
uv sync
source .venv/bin/activate
uvicorn agent:app --port 8000
```

In another shell:

```bash
uv run client.py
```

## Example actions

```json
{"action":"capabilities","args":{}}
```

```json
{"action":"buildersclaw_list_hackathons","args":{"status":"open"}}
```

```json
{"action":"github_create_repo","args":{"name":"buildersclaw-my-solution","public":true}}
```
