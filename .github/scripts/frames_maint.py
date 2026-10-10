import json, os, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
FRAMES = Path("frames")
QINDEX = FRAMES / "quarantine" / "index.json"
KEEP_ALL_DAYS = 10
DAY_START, DAY_END = 9 * 60, 17 * 60   # Pacific minutes; older days keep only 9 AM–5 PM
NOON_UTC_MIN = 20 * 60                 # same "closest to noon" rule the Daily Timelapse uses
MAX_WIDTH = 1600
JPEG_QUALITY = 75


def local_minutes(day, t):
    hh, mm = map(int, t.split("-"))
    loc = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc).astimezone(PT)
    return loc.hour * 60 + loc.minute


def utc_minutes(t):
    hh, mm = map(int, t.split("-"))
    return hh * 60 + mm


def prune(today, dry_run=False):
    cutoff = today - timedelta(days=KEEP_ALL_DAYS - 1)
    quarantine = json.loads(QINDEX.read_text()) if QINDEX.exists() else []
    quarantined = {(q["date"], q["time"]) for q in quarantine}
    removed = []
    for folder in sorted(FRAMES.glob("????-??-??")):
        day = date.fromisoformat(folder.name)
        lodge = folder / "lodge"
        if day >= cutoff or not lodge.is_dir():
            continue
        times = sorted(p.stem for p in lodge.glob("*.jpg"))
        keep = {t for t in times if DAY_START <= local_minutes(day, t) <= DAY_END}
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"date": folder.name, "lodge": []}
        if not keep and times:
            # Never empty a day: keep the frame the timelapse would show, preferring non-quarantined ones.
            pool = [t for t in manifest.get("lodge", []) if t in times] or times
            keep = {min(pool, key=lambda t: abs(utc_minutes(t) - NOON_UTC_MIN))}
        drop = [t for t in times if t not in keep]
        if not drop:
            continue
        removed += [(folder.name, t) for t in drop]
        if dry_run:
            continue
        for t in drop:
            (lodge / f"{t}.jpg").unlink()
        manifest["lodge"] = [t for t in manifest.get("lodge", []) if t in keep]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    if removed and not dry_run and quarantine:
        gone = set(removed)
        kept = [q for q in quarantine if (q["date"], q["time"]) not in gone]
        if len(kept) != len(quarantine):
            QINDEX.write_text(json.dumps(kept, indent=2) + "\n")
    return removed, quarantined


def resize(path):
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True  # partial downloads still get shrunk; corruption is judged before this
    with Image.open(path) as im:
        if im.width <= MAX_WIDTH:
            return False
        h = round(im.height * MAX_WIDTH / im.width)
        out = im.convert("RGB").resize((MAX_WIDTH, h), Image.LANCZOS)
    out.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    today = datetime.now(timezone.utc).astimezone(PT).date()
    if cmd in ("prune", "prune-dry-run"):
        removed, quarantined = prune(today, dry_run=cmd == "prune-dry-run")
        print(f"{'Would remove' if cmd == 'prune-dry-run' else 'Removed'} {len(removed)} frames "
              f"({sum(r in quarantined for r in removed)} quarantined)")
        by_month = {}
        for d, _ in removed:
            by_month[d[:7]] = by_month.get(d[:7], 0) + 1
        for m, n in sorted(by_month.items()):
            print(f"  {m}: {n}")
    elif cmd == "resize":
        for p in sys.argv[2:]:
            print(f"{p}: {'resized' if resize(p) else 'already small'}")
    elif cmd == "resize-all":
        files = sorted(FRAMES.glob("????-??-??/lodge/*.jpg"))
        before = sum(f.stat().st_size for f in files)
        n = sum(resize(f) for f in files)
        after = sum(f.stat().st_size for f in files)
        print(f"Resized {n} of {len(files)} frames: {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
    else:
        sys.exit("usage: frames_maint.py prune | prune-dry-run | resize FILE... | resize-all")
