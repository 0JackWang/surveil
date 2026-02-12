# ⚡ HyperDash Monitor

A self-hosted dashboard that tracks **top 100 Hyperliquid leaderboard traders' positions** and shows L/S ratio trends with **4-hour snapshots** — permanently stored.

## What it does

- Every 4 hours, fetches the top 100 traders from Hyperliquid's leaderboard
- Computes per-coin Long/Short ratios from their actual positions
- Stores snapshots in a JSON file (survives restarts)
- Serves a dashboard showing the L/S trend grid with ↗↘— arrows for positioning changes
- Click any coin to see its full history across all snapshots

## Quick Start (Local)

```bash
python server.py
```

Open http://localhost:8080. First snapshot takes ~30 seconds. The server will automatically take a new snapshot every 4 hours.

**Manual snapshot:** Click "📸 Snapshot Now" or visit http://localhost:8080/api/snapshot/now

## Deploy to Railway (Free Tier — Recommended)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app), click "New Project" → "Deploy from GitHub Repo"
3. Add a **Volume** mounted at `/data` (this stores your snapshots permanently)
4. Set environment variable: `DATA_FILE=/data/snapshots.json`
5. Deploy — Railway will auto-detect the Dockerfile

**Cost:** Railway's free tier gives you 500 hours/month — enough to run 24/7.

## Deploy to Render (Free Tier)

1. Push to GitHub
2. Go to [render.com](https://render.com), create a new **Web Service**
3. Connect your repo, Dockerfile is auto-detected
4. Add a **Disk** mounted at `/data`
5. Deploy

## Deploy with Docker

```bash
docker build -t hyperdash .
docker run -d -p 8080:8080 -v hyperdash-data:/data hyperdash
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML |
| `GET /api/snapshots` | All stored snapshots as JSON |
| `GET /api/snapshot/now` | Trigger a manual snapshot |

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Server port |
| `DATA_FILE` | `snapshots.json` | Path to snapshot storage file |

## No dependencies

Zero pip packages required. Uses only Python standard library.
