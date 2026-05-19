# Video Date Overlay

Mobile web interface for uploading a camera video, automatically cropping the original top timestamp area, and rendering a new date/time overlay.

## Local Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python web_app/server.py
```

Open:

```text
http://localhost:8000
```

## Fast Production Deploy

For real video processing, use a VPS or container host instead of Vercel.

Recommended minimum server:

- 2 CPU cores
- 4 GB RAM
- 20 GB disk
- Ubuntu 22.04/24.04

Run with Docker:

```bash
git clone https://github.com/rooslan1802/vidosaza.git
cd vidosaza
docker compose up -d --build
```

Open:

```text
http://YOUR_SERVER_IP:8000
```

Useful commands:

```bash
docker compose logs -f
docker compose restart
git pull && docker compose up -d --build
```

Environment variables:

```text
PORT=8000
MAX_UPLOAD_MB=2048
CORS_ORIGIN=*
```

## Notes for Vercel

The app includes `api/index.py` and `vercel.json` so it can be tried on Vercel.

Video processing can be slow or fail on serverless hosting because large uploads, OpenCV, `ffmpeg`, and execution time are constrained. A VPS or local Mac is more reliable for long videos.
