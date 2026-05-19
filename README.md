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

## Notes for Vercel

The app includes `api/index.py` and `vercel.json` so it can be tried on Vercel.

Video processing can be slow or fail on serverless hosting because large uploads, OpenCV, `ffmpeg`, and execution time are constrained. A VPS or local Mac is more reliable for long videos.
