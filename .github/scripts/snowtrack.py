import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LAT, LON, ELEV_M = 48.2858, -117.565, 1431
NWS_GRID = "https://api.weather.gov/gridpoints/OTX/141,120"
OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}&elevation={ELEV_M}"
    "&hourly=snowfall&models=ecmwf_ifs025,gfs_seamless"
    "&timezone=America/Los_Angeles&forecast_days=9"
)
REPORT_URL = "https://49.todd-0ce.workers.dev"
UA = "49app snow forecast tracker (todd@toddsackmann.com)"
PT = ZoneInfo("America/Los_Angeles")
MAX_LEAD = 7
# Day T is scored over the 24h ending 06:00 Pacific on T, matching the mountain's morning 24h report.
WINDOW_END_HOUR = 6

ROOT = Path("snowtrack")
FC_DIR = ROOT / "forecasts"
REPORTS = ROOT / "reports.json"
DATA = ROOT / "data.json"
ALERT_STATE = ROOT / "alert_state.json"
OBS_OM = ROOT / "obs_openmeteo.json"
OBS_SNOTEL = ROOT / "obs_snotel.json"
# Closest SNOTEL stations to 49° North (base 3,923 ft, summit 5,774 ft).
SNOTEL_STATIONS = {"376:WA:SNTL": "bunchgrass", "707:WA:SNTL": "quartz"}
OBS_LOOKBACK_DAYS = 14
SITE_URL = "https://509spokane.github.io/49/"
ALERT_MIN_IN = 0.1
ALERT_RISE_IN = 1.0


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception as e:
            print(f"  fetch failed ({i + 1}/{tries}) {url[:60]}: {e}")
            if i < tries - 1:
                time.sleep(10)
    return None


def fetch_nws():
    d = get_json(NWS_GRID)
    if not d:
        return None
    p = d["properties"]
    return {"updateTime": p["updateTime"], "snowfallAmount_mm": p["snowfallAmount"]["values"]}


def fetch_open_meteo():
    d = get_json(OPEN_METEO)
    if not d or "hourly" not in d:
        return None
    h = d["hourly"]
    return {
        "time_pt": h["time"],
        "ecmwf_cm": h["snowfall_ecmwf_ifs025"],
        "gfs_cm": h["snowfall_gfs_seamless"],
    }


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n")


def snapshot_forecasts(now_pt):
    # Morning-only, so every snapshot has the same lead time to its target days.
    if not 5 <= now_pt.hour < 12:
        print("Outside 5 AM-noon Pacific; no forecast snapshot this run")
        return
    path = FC_DIR / f"{now_pt.date()}.json"
    snap = load(path, {"issued": now_pt.isoformat(timespec="minutes")})
    changed = False
    if not snap.get("nws"):
        snap["nws"] = fetch_nws()
        changed |= bool(snap["nws"])
    if not snap.get("openmeteo"):
        snap["openmeteo"] = fetch_open_meteo()
        changed |= bool(snap["openmeteo"])
    if changed or not path.exists():
        save(path, snap)
        print(f"Saved forecast snapshot {path} (nws={bool(snap['nws'])}, openmeteo={bool(snap['openmeteo'])})")


def snapshot_report(now_pt):
    r = get_json(REPORT_URL)
    if not r or "totals" not in r:
        print("Mountain report unavailable")
        return
    t = r["totals"]
    reports = load(REPORTS, [])
    if reports and reports[-1]["updated"] == t.get("updated"):
        return
    reports.append({
        "fetched": now_pt.isoformat(timespec="minutes"),
        "updated": t.get("updated"),
        "12h": t.get("12h"), "24h": t.get("24h"), "48h": t.get("48h"), "72h": t.get("72h"),
        "summitDepth": t.get("summitDepth"),
    })
    save(REPORTS, reports)
    print(f"Saved new mountain report: updated {t.get('updated')}, 24h={t.get('24h')}")


def fetch_openmeteo_past():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}&elevation={ELEV_M}"
        f"&hourly=snowfall&past_days={OBS_LOOKBACK_DAYS}&forecast_days=1"
        "&timezone=America/Los_Angeles"
    )
    d = get_json(url)
    if not d or "hourly" not in d:
        return None
    return {"time_pt": d["hourly"]["time"], "snowfall_cm": d["hourly"]["snowfall"]}


def fetch_snotel():
    today = datetime.now(PT).date()
    begin = today - timedelta(days=OBS_LOOKBACK_DAYS + 1)
    triplets = ",".join(SNOTEL_STATIONS)
    url = (
        f"https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
        f"?stationTriplets={triplets}&elements=SNWD&duration=DAILY"
        f"&beginDate={begin}&endDate={today}"
    )
    d = get_json(url)
    if not d:
        return None
    depths = {name: {} for name in SNOTEL_STATIONS.values()}
    for station in d:
        name = SNOTEL_STATIONS.get(station.get("stationTriplet"))
        if not name:
            continue
        for el in station.get("data", []):
            if el.get("stationElement", {}).get("elementCode") != "SNWD":
                continue
            for v in el.get("values", []):
                if v.get("value") is not None:
                    depths[name][v["date"]] = v["value"]
    return depths


def openmeteo_past_by_date(now_pt):
    om = fetch_openmeteo_past()
    cache = load(OBS_OM, {})
    if om:
        times, vals = om["time_pt"], om["snowfall_cm"]
        for i in range(1, OBS_LOOKBACK_DAYS + 1):
            target = (now_pt.date() - timedelta(days=i - 1))
            start, end = window(target)
            # Only accept a day whose 06:00 window has fully elapsed.
            if end > now_pt:
                continue
            total = om_total_in(times, vals, start, end)
            if total is not None:
                cache[str(target)] = round(total, 2)
        save(OBS_OM, cache)
    return cache


def snotel_new_snow_by_date():
    snot = fetch_snotel()
    cache = load(OBS_SNOTEL, {})
    if snot:
        for name, series in snot.items():
            for date_s, depth in series.items():
                prev_s = str(datetime.fromisoformat(date_s).date() - timedelta(days=1))
                prev = series.get(prev_s)
                if prev is None:
                    continue
                new = round(max(0.0, depth - prev), 1)
                cache.setdefault(date_s, {})[name] = new
        save(OBS_SNOTEL, cache)
    return cache


def window(target):
    end = datetime(target.year, target.month, target.day, WINDOW_END_HOUR, tzinfo=PT)
    return end - timedelta(days=1), end


def nws_total_in(values, start, end):
    total_mm = 0.0
    for v in values:
        if v["value"] is None:
            continue
        t0_s, dur = v["validTime"].split("/")
        t0 = datetime.fromisoformat(t0_s)
        hours = int(dur.split("T")[1].rstrip("H")) if "T" in dur else 0
        days = int(dur[1:].split("D")[0]) if "D" in dur else 0
        span = timedelta(days=days, hours=hours)
        t1 = t0 + span
        overlap = (min(t1, end) - max(t0, start)).total_seconds()
        if overlap > 0:
            total_mm += v["value"] * overlap / span.total_seconds()
    return total_mm / 25.4


def om_total_in(times, vals, start, end):
    total_cm, hours = 0.0, 0
    for t_s, v in zip(times, vals):
        t = datetime.fromisoformat(t_s).replace(tzinfo=PT)  # value = snowfall over the preceding hour
        if start < t <= end and v is not None:
            total_cm += v
            hours += 1
    return total_cm / 2.54 if hours >= 20 else None


def morning_report_by_date(reports):
    by_date = {}
    for r in reports:
        try:
            u = datetime.strptime(r["updated"].strip().upper(), "%m/%d/%y %I:%M%p")
            snow = float(r["24h"])
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
        if u.hour < 12:
            by_date.setdefault(str(u.date()), {"in": snow, "updated": r["updated"]})
    return by_date


def rebuild():
    reports = load(REPORTS, [])
    obs_resort = morning_report_by_date(reports)
    obs_om = load(OBS_OM, {})
    obs_snotel = load(OBS_SNOTEL, {})
    days = {}
    snaps = sorted(FC_DIR.glob("*.json")) if FC_DIR.exists() else []
    for path in snaps:
        issue = datetime.fromisoformat(path.stem).date()
        snap = json.loads(path.read_text())
        for lead in range(1, MAX_LEAD + 1):
            target = issue + timedelta(days=lead)
            start, end = window(target)
            fc = days.setdefault(str(target), {"fc": {}})["fc"]
            if snap.get("nws"):
                last = snap["nws"]["snowfallAmount_mm"][-1]["validTime"].split("/")[0]
                if datetime.fromisoformat(last) >= end - timedelta(hours=6):
                    fc.setdefault("nws", {})[lead] = round(nws_total_in(snap["nws"]["snowfallAmount_mm"], start, end), 2)
            om = snap.get("openmeteo")
            if om:
                for src in ("ecmwf", "gfs"):
                    v = om_total_in(om["time_pt"], om[f"{src}_cm"], start, end)
                    if v is not None:
                        fc.setdefault(src, {})[lead] = round(v, 2)
    for d in set(obs_resort) | set(obs_om) | set(obs_snotel):
        sources = {}
        if d in obs_resort:
            sources["resort"] = obs_resort[d]
        if d in obs_om:
            sources["openmeteo"] = obs_om[d]
        if d in obs_snotel:
            sources["snotel"] = obs_snotel[d]
        # Pick the best single number for the "obs.in" the chart reads:
        # resort report when posted, else SNOTEL average, else OpenMeteo past-weather.
        best = None
        if "resort" in sources:
            best = sources["resort"].get("in")
        if best is None and "snotel" in sources:
            vals = [v for v in sources["snotel"].values() if v is not None]
            if vals:
                best = round(sum(vals) / len(vals), 1)
        if best is None and "openmeteo" in sources:
            best = sources["openmeteo"]
        days.setdefault(d, {"fc": {}})["obs"] = {"in": best, "sources": sources}
    save(DATA, {
        "generated": datetime.now(PT).isoformat(timespec="minutes"),
        "firstSnapshot": snaps[0].stem if snaps else None,
        "snapshotCount": len(snaps),
        "reportCount": len(reports),
        "obsSources": {
            "resort": len(obs_resort),
            "openmeteo": len(obs_om),
            "snotel": len(obs_snotel),
        },
        "days": dict(sorted(days.items())),
    })
    print(f"Rebuilt {DATA}: {len(days)} days, {len(snaps)} snapshots, "
          f"obs resort={len(obs_resort)} openmeteo={len(obs_om)} snotel={len(obs_snotel)}")


def daily_snow_forecast(now_pt):
    nws = fetch_nws()
    if not nws:
        return None
    days = {}
    for i in range(7):
        day = (now_pt + timedelta(days=i)).date()
        start = max(datetime(day.year, day.month, day.day, tzinfo=PT), now_pt)
        end = datetime(day.year, day.month, day.day, tzinfo=PT) + timedelta(days=1)
        days[str(day)] = round(nws_total_in(nws["snowfallAmount_mm"], start, end), 1)
    return days


def send_ntfy(topic, title, body):
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode(), method="POST",
        headers={"Title": title, "Tags": "snowflake", "Click": SITE_URL},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"ntfy sent ({r.status})")


def snow_alert(now_pt, topic):
    # Daytime only so alerts never arrive overnight; a pending change goes out on the next morning run.
    if not 6 <= now_pt.hour < 22:
        return
    days = daily_snow_forecast(now_pt)
    if days is None:
        return
    prev = load(ALERT_STATE, {}).get("days", {})
    snowy = {d: v for d, v in days.items() if v >= ALERT_MIN_IN}
    changed = [d for d, v in snowy.items() if d not in prev or v >= prev[d] + ALERT_RISE_IN]
    if changed:
        lines = []
        for d, v in snowy.items():
            label = datetime.fromisoformat(d).strftime("%a %b %-d")
            lines.append(f"{label}: {v:.1f}\"" + ("  (new)" if d in changed and d not in prev else "  (up)" if d in changed else ""))
        body = "\n".join(lines) + f"\nTotal: {sum(snowy.values()):.1f}\" over the next 7 days (NOAA, ~4,700 ft)"
        send_ntfy(topic, "Snow in the 49 North forecast", body)
    # Keep the highest amount already alerted on, so small wobbles don't re-alert.
    state = {d: max(v, prev.get(d, 0)) for d, v in snowy.items()}
    save(ALERT_STATE, {"updated": now_pt.isoformat(timespec="minutes"), "days": state})


if __name__ == "__main__":
    now_pt = datetime.now(timezone.utc).astimezone(PT)
    if "--rebuild-only" not in sys.argv:
        snapshot_forecasts(now_pt)
        snapshot_report(now_pt)
        openmeteo_past_by_date(now_pt)
        snotel_new_snow_by_date()
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            snow_alert(now_pt, topic)
    rebuild()
