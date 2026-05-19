from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import cv2


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
MARGIN_RIGHT = 80
MARGIN_TOP = 52
AUTO_CROP_REFERENCE_TOP = 112
AUTO_CROP_REFERENCE_HEIGHT = 1296
FONT = cv2.FONT_HERSHEY_DUPLEX
FONT_SCALE = 1.5
THICKNESS = 2
OUTLINE_THICKNESS = 4
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
JOBS: dict[str, dict] = {}


def send_common_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", os.environ.get("CORS_ORIGIN", "*"))
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Expose-Headers", "X-Filename")


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    send_common_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def read_file_response(handler: BaseHTTPRequestHandler, path: Path, download_name: str | None = None) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(404)
        return

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(path.stat().st_size))
    if download_name:
        handler.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
    send_common_headers(handler)
    handler.end_headers()
    with path.open("rb") as src:
        shutil.copyfileobj(src, handler.wfile)


def video_response(handler: BaseHTTPRequestHandler, path: Path, download_name: str) -> None:
    if not path.exists() or not path.is_file():
        json_response(handler, {"ok": False, "error": "Готовый файл не найден"}, status=500)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "video/mp4")
    handler.send_header("Content-Length", str(path.stat().st_size))
    handler.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
    handler.send_header("X-Filename", download_name)
    send_common_headers(handler)
    handler.end_headers()
    with path.open("rb") as src:
        shutil.copyfileobj(src, handler.wfile)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    match = re.search(r"boundary=(.+)", content_type)
    if not match:
        raise ValueError("Не найден multipart boundary")

    boundary = match.group(1).strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    for raw_part in body.split(delimiter):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue

        header_blob, _, value = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue

        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue

        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        value = value.removesuffix(b"\r\n")

        if filename_match:
            filename = Path(filename_match.group(1)).name or "video.mp4"
            files[name] = (filename, value)
        else:
            fields[name] = value.decode("utf-8", errors="replace")

    return fields, files


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def auto_crop_top(src_height: int) -> int:
    crop_top = round(src_height * AUTO_CROP_REFERENCE_TOP / AUTO_CROP_REFERENCE_HEIGHT)
    return clamp(crop_top, 0, max(0, src_height - 2))


def ffmpeg_path() -> str | None:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def has_ffmpeg_encoder(encoder: str) -> bool:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.SubprocessError:
        return False
    return encoder in result.stdout


def ffprobe_video_info(path: Path) -> dict:
    ffprobe = ffprobe_path()
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration,nb_frames,avg_frame_rate,r_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def parse_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def update_job(job_id: str, **values: object) -> None:
    if job_id in JOBS:
        JOBS[job_id].update(values)


def process_video(
    input_path: Path,
    output_path: Path,
    date_text: str,
    start_time_text: str,
    remove_audio: bool,
    job_id: str,
) -> dict:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("Не найден ffmpeg")

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise ValueError("Не удалось открыть видео")

    src_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_fps = cap.get(cv2.CAP_PROP_FPS) or 20
    cap_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    probe = ffprobe_video_info(input_path)
    stream = (probe.get("streams") or [{}])[0]
    fmt = probe.get("format") or {}
    source_duration = parse_float(stream.get("duration")) or parse_float(fmt.get("duration"))
    total_frames = parse_int(stream.get("nb_frames")) or cap_total_frames
    fps = (total_frames / source_duration) if source_duration and total_frames else cap_fps

    crop_top = auto_crop_top(src_height)
    out_width = src_width if src_width % 2 == 0 else src_width - 1
    out_height = src_height - crop_top
    out_height = out_height if out_height % 2 == 0 else out_height - 1
    if out_width <= 0 or out_height <= 0:
        raise ValueError("После обрезки кадр получился слишком маленьким")

    try:
        start_time = datetime.strptime(start_time_text, "%H:%M:%S")
    except ValueError:
        start_time = datetime.strptime(start_time_text, "%H:%M")

    start_seconds = start_time.hour * 3600 + start_time.minute * 60 + start_time.second
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{out_width}x{out_height}",
        "-r",
        f"{fps}",
        "-i",
        "-",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
    ]

    if remove_audio:
        cmd.append("-an")
    else:
        cmd.extend(["-map", "1:a?", "-c:a", "aac", "-b:a", "128k", "-shortest"])

    if has_ffmpeg_encoder("h264_videotoolbox"):
        cmd.extend(["-c:v", "h264_videotoolbox", "-b:v", "4500k", "-allow_sw", "1"])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"])

    cmd.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)])

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame = frame[crop_top : crop_top + out_height, 0:out_width].copy()
            total = int(start_seconds + (frame_idx / fps))
            hh = (total // 3600) % 24
            mm = (total % 3600) // 60
            ss = total % 60
            text = f"{date_text} {hh:02d}:{mm:02d}:{ss:02d}"

            (tw, th), baseline = cv2.getTextSize(text, FONT, FONT_SCALE, THICKNESS)
            x = out_width - tw - MARGIN_RIGHT
            y = MARGIN_TOP + th
            x = clamp(x, 0, max(0, out_width - tw))
            y = clamp(y, th + 2, max(th + 2, out_height - baseline - 2))

            cv2.putText(
                frame,
                text,
                (x, y),
                FONT,
                FONT_SCALE,
                (30, 30, 30),
                OUTLINE_THICKNESS + THICKNESS,
                cv2.LINE_AA,
            )
            cv2.putText(frame, text, (x, y), FONT, FONT_SCALE, (255, 255, 255), THICKNESS, cv2.LINE_AA)
            if proc.stdin is None:
                raise RuntimeError("ffmpeg pipe закрыт")
            proc.stdin.write(frame.tobytes())
            frame_idx += 1
            if total_frames:
                progress = 12 + int((frame_idx / total_frames) * 83)
                update_job(job_id, progress=min(progress, 95), message="Обрабатываю кадры...")
    finally:
        cap.release()
        if proc.stdin is not None:
            proc.stdin.close()

    update_job(job_id, progress=98, message="Сохраняю готовый файл...")
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg не смог сохранить видео")

    return {
        "width": out_width,
        "height": out_height,
        "fps": fps,
        "frames": frame_idx,
        "duration": source_duration,
        "sourceWidth": src_width,
        "sourceHeight": src_height,
        "totalFrames": total_frames,
        "cropTop": crop_top,
        "overlay": {
            "marginRight": MARGIN_RIGHT,
            "marginTop": MARGIN_TOP,
            "fontScale": FONT_SCALE,
            "thickness": THICKNESS,
            "outlineThickness": OUTLINE_THICKNESS,
        },
    }


def run_job(job_id: str, input_path: Path, output_path: Path, date_text: str, start_time: str, remove_audio: bool) -> None:
    try:
        update_job(job_id, status="processing", progress=10, message="Начинаю обработку...")
        meta = process_video(input_path, output_path, date_text, start_time, remove_audio, job_id)
        update_job(
            job_id,
            status="done",
            progress=100,
            message="Готово",
            outputUrl=f"/outputs/{output_path.name}",
            filename=output_path.name,
            meta=meta,
        )
    except Exception as exc:
        update_job(job_id, status="error", progress=100, message=str(exc), error=str(exc))


class VideoDateHandler(BaseHTTPRequestHandler):
    server_version = "VideoDateWeb/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        send_common_headers(self)
        self.end_headers()

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/":
            read_file_response(self, STATIC_DIR / "index.html")
            return
        if path == "/app.css":
            read_file_response(self, STATIC_DIR / "app.css")
            return
        if path == "/app.js":
            read_file_response(self, STATIC_DIR / "app.js")
            return
        if path.startswith("/outputs/"):
            name = Path(path.removeprefix("/outputs/")).name
            read_file_response(self, OUTPUT_DIR / name, download_name=name)
            return
        if path.startswith("/jobs/"):
            job_id = Path(path.removeprefix("/jobs/")).name
            job = JOBS.get(job_id)
            if not job:
                json_response(self, {"ok": False, "error": "Задача не найдена"}, status=404)
                return
            json_response(self, {"ok": True, **job})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/process":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            max_upload = int(os.environ.get("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
            if length > max_upload:
                raise ValueError(f"Видео слишком большое. Максимум: {max_upload // 1024 // 1024} MB")
            content_type = self.headers.get("Content-Type", "")
            fields, files = parse_multipart(content_type, self.rfile.read(length))
            if "video" not in files:
                raise ValueError("Загрузите видео")

            filename, data = files["video"]
            suffix = Path(filename).suffix.lower()
            if suffix not in VIDEO_EXTS:
                suffix = ".mp4"

            date_text = fields.get("date", "").strip()
            start_time = fields.get("time", "").strip()
            if not date_text or not start_time:
                raise ValueError("Укажите дату и время старта")
            datetime.strptime(date_text, "%Y-%m-%d")
            remove_audio = fields.get("removeAudio", "0") == "1"

            job_id = uuid.uuid4().hex[:12]
            work_dir = Path(tempfile.gettempdir()) if is_vercel() else UPLOAD_DIR
            out_dir = Path(tempfile.gettempdir()) if is_vercel() else OUTPUT_DIR
            input_path = work_dir / f"{job_id}{suffix}"
            output_path = out_dir / f"ready-{job_id}.mp4"
            input_path.write_bytes(data)

            if is_vercel():
                process_video(input_path, output_path, date_text, start_time, remove_audio, job_id)
                video_response(self, output_path, output_path.name)
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                return

            JOBS[job_id] = {
                "status": "queued",
                "progress": 5,
                "message": "Видео загружено",
                "outputUrl": None,
                "filename": None,
                "meta": None,
            }
            threading.Thread(
                target=run_job,
                args=(job_id, input_path, output_path, date_text, start_time, remove_audio),
                daemon=True,
            ).start()
            json_response(self, {"ok": True, "jobId": job_id})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=400)


def main() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), VideoDateHandler)
    print(f"Мобильный сайт запущен: http://localhost:{port}")
    print("Остановить: Ctrl+C")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
