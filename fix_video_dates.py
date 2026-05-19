import argparse
import signal
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
try:
    from moviepy.editor import VideoFileClip  # moviepy 1.x
except ImportError:
    from moviepy import VideoFileClip  # moviepy 2.x

# ================= НАСТРОЙКИ (ТРОГАЙ ТОЛЬКО ЭТИ) =================
MARGIN_RIGHT = 32      # отступ от правого края (пиксели)
MARGIN_TOP = 20        # отступ от верхнего края (пиксели)
FONT = cv2.FONT_HERSHEY_DUPLEX
FONT_SCALE = 1.5       # размер текста
THICKNESS = 2          # толщина основного белого текста
OUTLINE_THICKNESS = 4  # толщина тёмной обводки
# ==================================================================

INPUT_FOLDER = Path("input_videos")
OUTPUT_FOLDER = Path("output_videos")
TIME_TABLE = Path("Сводка.xlsx")
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv"}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-one", action="store_true", help="Проверка только на одном видео")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписывать готовые файлы")
    parser.add_argument("--ignore-sigint", action="store_true", help="Игнорировать Ctrl+C во время обработки")
    return parser.parse_args()


def column_index_from_ref(cell_ref):
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def read_xlsx_rows(path):
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(".//a:si", ns):
                text = "".join((t.text or "") for t in si.findall(".//a:t", ns))
                shared.append(text)

        sheet_name = next(x for x in zf.namelist() if x.startswith("xl/worksheets/sheet"))
        sheet = ET.fromstring(zf.read(sheet_name))

    rows = []
    for row in sheet.findall(".//a:sheetData/a:row", ns):
        cur = []
        for cell in row.findall("a:c", ns):
            ref = cell.attrib.get("r", "")
            col_idx = column_index_from_ref(ref)
            while len(cur) <= col_idx:
                cur.append(None)

            cell_type = cell.attrib.get("t")
            val_node = cell.find("a:v", ns)
            if val_node is None:
                cur[col_idx] = None
                continue

            raw_value = val_node.text or ""
            if cell_type == "s":
                idx = int(raw_value)
                value = shared[idx] if 0 <= idx < len(shared) else raw_value
            else:
                try:
                    value = float(raw_value)
                except ValueError:
                    value = raw_value

            cur[col_idx] = value
        rows.append(cur)
    return rows


def parse_datetime_value(value):
    """Парсит дату+время из любого формата: строка, Excel serial, float."""
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        # Excel serial number: дни с 30.12.1899
        return datetime(1899, 12, 30) + timedelta(days=float(value))

    text = str(value).strip()
    if not text:
        return None

    # "04.05.2026 11:07" или "04.05.2026 11:07:00"
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    # Только число — Excel serial как строка
    if all(ch in "0123456789." for ch in text):
        try:
            frac = float(text)
            if frac > 1:
                return datetime(1899, 12, 30) + timedelta(days=frac)
            total = int(round(frac * 86400))
            return datetime(2026, 1, 1, total // 3600 % 24, (total % 3600) // 60, total % 60)
        except ValueError:
            pass

    return None


def build_config():
    """
    Читает таблицу формата:
      Колонка A — имя видеофайла (например 1.mp4)
      Колонка B — дата и время  (например 04.05.2026 11:07)

    Возвращает dict: имя_файла_lower -> (date_text, hour, minute, second)
    """
    if not TIME_TABLE.exists():
        raise FileNotFoundError(f"Не найден файл таблицы: {TIME_TABLE}")
    rows = read_xlsx_rows(TIME_TABLE)
    if not rows:
        return {}

    config = {}
    for row in rows:
        if len(row) < 2:
            continue
        filename_raw = row[0]
        datetime_raw = row[1]

        if filename_raw is None or datetime_raw is None:
            continue

        filename = str(filename_raw).strip()
        if not filename:
            continue

        dt = parse_datetime_value(datetime_raw)
        if dt is None:
            print(f"  ⚠️  Не удалось распознать дату для '{filename}': {datetime_raw!r}")
            continue

        date_text = dt.strftime("%Y-%m-%d")  # формат на плашке: 2026-03-04
        key = filename.lower()
        config[key] = (date_text, dt.hour, dt.minute, dt.second)
        print(f"  ✔ {filename} → {date_text} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}")

    return config


def build_overlay_renderer():
    """Рендерит текст с обводкой в правый верхний угол, без фона."""
    cache = {}

    def render_overlay(total_seconds, date_text, frame_width):
        hh = (total_seconds // 3600) % 24
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        text = f"{date_text} {hh:02d}:{mm:02d}:{ss:02d}"
        cache_key = (text, frame_width)
        if cache_key in cache:
            return cache[cache_key]

        (tw, th), baseline = cv2.getTextSize(text, FONT, FONT_SCALE, THICKNESS)
        pad = OUTLINE_THICKNESS + 2
        patch_w = tw + pad * 2
        patch_h = th + baseline + pad * 2
        patch = np.zeros((patch_h, patch_w, 4), dtype=np.uint8)

        tx = pad
        ty = th + pad

        # Тёмная обводка
        cv2.putText(patch, text, (tx, ty), FONT, FONT_SCALE,
                    (30, 30, 30, 255), OUTLINE_THICKNESS + THICKNESS, cv2.LINE_AA)
        # Белый текст поверх
        cv2.putText(patch, text, (tx, ty), FONT, FONT_SCALE,
                    (255, 255, 255, 255), THICKNESS, cv2.LINE_AA)

        x = frame_width - patch_w - MARGIN_RIGHT + pad
        y = MARGIN_TOP - pad
        cache[cache_key] = (patch, x, y)
        return cache[cache_key]

    return render_overlay


def paste_alpha(frame, patch, x, y):
    """Накладывает патч с alpha-каналом на кадр."""
    ph, pw = patch.shape[:2]
    fh, fw = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(fw, x + pw), min(fh, y + ph)
    if x0 >= x1 or y0 >= y1:
        return
    px0, py0 = x0 - x, y0 - y
    px1, py1 = px0 + (x1 - x0), py0 + (y1 - y0)
    src = patch[py0:py1, px0:px1]
    alpha = src[:, :, 3:4].astype(np.float32) / 255.0
    rgb = src[:, :, :3]
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (rgb.astype(np.float32) * alpha + roi * (1 - alpha)).astype(np.uint8)


def overlay_datetime_clip(clip, cfg):
    date_text, hour, minute, second = cfg
    start_total = hour * 3600 + minute * 60 + second
    fps = clip.fps
    frame_idx = 0
    render_overlay = build_overlay_renderer()

    def apply_frame(frame):
        nonlocal frame_idx
        frame = frame.copy()
        total = int(start_total + (frame_idx / fps))
        frame_width = frame.shape[1]
        patch, x, y = render_overlay(total, date_text, frame_width)
        paste_alpha(frame, patch, x, y)
        frame_idx += 1
        return frame

    if hasattr(clip, "fl_image"):
        return clip.fl_image(apply_frame)   # moviepy 1.x
    return clip.image_transform(apply_frame)  # moviepy 2.x


def process_video(input_path, output_path, cfg, current_index, total_count):
    date_text, hour, minute, second = cfg
    remaining = total_count - current_index
    print(
        f"[{current_index}/{total_count}] осталось {remaining} | "
        f"{input_path.name} → {output_path.as_posix()} "
        f"[{date_text} {hour:02d}:{minute:02d}:{second:02d}]"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clip = VideoFileClip(str(input_path))
    result = overlay_datetime_clip(clip, cfg)
    result.write_videofile(
        str(output_path),
        codec="libx264",
        preset="ultrafast",
        threads=8,
        audio=False,
    )
    clip.close()
    result.close()


def main():
    args = parse_args()

    print(f"📋 Читаю таблицу {TIME_TABLE} ...")
    config = build_config()
    if not config:
        print("❌ В таблице не найдено валидных строк (имя файла + дата).")
        return
    print(f"   Найдено записей: {len(config)}\n")

    videos = sorted(
        [p for p in INPUT_FOLDER.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda p: p.name.lower(),
    )

    tasks = []
    for video in videos:
        key = video.name.lower()
        if key in config:
            out = OUTPUT_FOLDER / video.relative_to(INPUT_FOLDER)
            tasks.append((video, out, config[key]))
        else:
            print(f"  ⚠️  Нет в таблице, пропускаю: {video.name}")

    if not tasks:
        print(f"\n❌ Не найдено совпадений между {INPUT_FOLDER}/ и таблицей.")
        return

    if args.test_one:
        tasks = tasks[:1]

    print(f"\n🎬 Буду обрабатывать {len(tasks)} видео ...\n")

    processed = 0
    skipped = 0
    old_sigint = None
    if args.ignore_sigint:
        old_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        for idx, (inp, out, cfg) in enumerate(tasks, start=1):
            if out.exists() and not args.overwrite:
                skipped += 1
                print(f"[{idx}/{len(tasks)}] SKIP (уже есть): {out.name}")
                continue
            try:
                process_video(inp, out, cfg, idx, len(tasks))
                processed += 1
            except KeyboardInterrupt:
                print("\n⏹ Остановлено пользователем (Ctrl+C).")
                break
    finally:
        if old_sigint is not None:
            signal.signal(signal.SIGINT, old_sigint)

    print(f"\n✅ Готово. Обработано: {processed}. Пропущено: {skipped}.")


if __name__ == "__main__":
    main()