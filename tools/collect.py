#!/usr/bin/env python3
"""Read-only upstream adapter + independent public aggregates. Python stdlib only."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
FEED = "https://raw.githubusercontent.com/tyuio7777/hunt-briefing-feed/main/"
TREE = "https://api.github.com/repos/tyuio7777/hunt-briefing-feed/git/trees/main?recursive=1"
STEAM = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=594650"
ROOT = Path(__file__).resolve().parents[1]
DETAIL_DAYS = 90
SUMMARY_DAYS = 730

def iso(d):
    return d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

def stamp(s):
    d = datetime.fromisoformat(s.strip().replace(" UTC", "+00:00").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)

def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def get_json(url):
    # No credentials are sent to Steam/raw GitHub. One bounded retry, no open-ended loop.
    headers = {"User-Agent": "HuntBriefing-Trends/1.0", "Cache-Control": "no-cache"}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
                data = response.read(4 * 1024 * 1024 + 1)
                if len(data) > 4 * 1024 * 1024:
                    raise ValueError("Response too large")
                return json.loads(data)
        except (OSError, ValueError) as ex:
            if attempt:
                raise
            delay = 2
            if isinstance(ex, urllib.error.HTTPError) and ex.code in (403, 429):
                retry = ex.headers.get("Retry-After", "2")
                if not retry.isdigit() or int(retry) > 30:
                    raise  # do not retry earlier than the server asks
                delay = max(2, int(retry))
            time.sleep(delay)

def safe_feed_url(url, suffix):
    return (isinstance(url, str) and url.startswith(FEED + "feed/") and
            re.fullmatch(r"[A-Za-z0-9_/-]+\." + suffix, url[len(FEED):]) is not None and
            ".." not in url)

def url_key(url):
    """Conservative article identity, listing/home pages excluded. No network scraping."""
    if not isinstance(url, str):
        return None
    p = urllib.parse.urlsplit(url.strip())
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        return None
    host = p.hostname.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    query = dict(urllib.parse.parse_qsl(p.query))
    if host in ("youtube.com", "m.youtube.com", "youtu.be"):
        video = path.strip("/") if host == "youtu.be" else query.get("v")
        if not video and path.startswith(("/shorts/", "/live/")):
            video = path.split("/")[2]
        return "https://youtube.com/watch?v=" + video if video and re.fullmatch(r"[\w-]{11}", video) else None
    if host.endswith("reddit.com"):
        m = re.search(r"/comments/([A-Za-z0-9]+)(?:/|$)", path)
        return "https://reddit.com/comments/" + m[1].lower() if m else None
    if host in ("twitter.com", "x.com", "mobile.twitter.com"):
        m = re.search(r"/status/(\d+)", path)
        return "https://x.com/i/status/" + m[1] if m else None
    parts = [x for x in path.split("/") if x]
    if not parts or path.lower().endswith(("/announcements", "/news", "/history", "/videos", "/posts")):
        return None
    if host == "steamcommunity.com" and "/detail/" not in path:
        return None
    if host == "steamdb.info" or host == "discord.gg" or host == "twitch.tv":
        return None
    if host == "store.steampowered.com" and "/news/app/" not in path:
        return None  # store products are not articles
    if len(parts) < 2 and not any(k in query for k in ("slug", "id", "article")):
        return None
    q = sorted((k, v) for k, v in query.items() if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref"))
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(q), ""))

def items(doc):
    sections = doc.get("sections")
    if not isinstance(sections, list):
        raise ValueError("Unknown briefing sections format")
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("Invalid section")
        for item in section.get("items", [section]):
            title = item.get("title", "")
            text = " ".join(str(item.get(k, "") or "") for k in ("title", "summary", "body"))
            provenance = item.get("category") or section.get("category") or section.get("label", "")
            tags = " ".join(item.get("badges", [])) + " " + str(provenance)
            if re.search("rumor|leak|루머|유출|추측", tags, re.I):
                origin = "unconfirmed"
            elif re.search("official|공식", tags, re.I):
                origin = "official"
            else:
                origin = "community"
            urls = item.get("source_urls", []) + [s.get("url", "") for s in item.get("sources", []) if isinstance(s, dict)]
            yield {"title": title, "text": text, "origin": origin, "urls": urls}

def topic_registry(snapshots, rules, overrides):
    grouped, excluded = {}, []
    aliases = overrides.get("url_aliases", {})
    for snapshot in sorted(snapshots, key=lambda x: stamp(x["issue"]["published_at"])):
        issue, doc = snapshot["issue"], snapshot["document"]
        for item in items(doc):
            if not item["urls"]:
                excluded.append({"briefing_id": issue["id"], "title": item["title"], "reason": "no_source"})
            for url in item["urls"]:
                key = url_key(url)
                if not key:
                    excluded.append({"briefing_id": issue["id"], "title": item["title"], "url": url, "reason": "unidentified_article"})
                    continue
                # aliases are explicit operator-verified reprint/translation groups, not same-topic merging.
                key = aliases.get(key, key)
                article = grouped.setdefault(key, {"id": hashlib.sha256(key.encode()).hexdigest()[:24], "url": key,
                    "title": item["title"], "first_included_at": issue["published_at"], "topics": [],
                    "origins": [], "briefings": [], "evidence": [], "rule_version": rules["version"]})
                if issue["id"] not in [b["id"] for b in article["briefings"]]:
                    article["briefings"].append(issue)
                if item["origin"] not in article["origins"]:
                    article["origins"].append(item["origin"])
                for topic in rules["topics"]:
                    matches = [t for t in topic["terms"] if t.casefold() in item["text"].casefold()]
                    if matches:
                        if topic["id"] not in article["topics"]:
                            article["topics"].append(topic["id"])
                        evidence = {"topic": topic["id"], "briefing_id": issue["id"], "title": item["title"], "terms": matches}
                        if evidence not in article["evidence"]:
                            article["evidence"].append(evidence)
    allowed = {x["id"] for x in rules["topics"]}
    for article in grouped.values():
        override = overrides.get("articles", {}).get(article["url"])
        if override:
            if not override.get("reason") or not set(override["topics"]) <= allowed:
                raise ValueError("Invalid article override")
            article["topics"] = sorted(set(override["topics"]))
            article["manual_reason"] = override["reason"]
    return list(grouped.values()), excluded

def topics_feed(registry, excluded, rules, now):
    today = now.astimezone(KST).date()
    start, prior = today - timedelta(days=6), today - timedelta(days=13)
    def in_period(a, lo, hi):
        return lo <= stamp(a["first_included_at"]).astimezone(KST).date() < hi
    current = [a for a in registry if in_period(a, start, today + timedelta(days=1))]
    previous = [a for a in registry if in_period(a, prior, start)]
    rows = []
    for topic in rules["topics"] + [{"id": "unclassified", "name": "미분류"}]:
        def match(a):
            return not a["topics"] if topic["id"] == "unclassified" else topic["id"] in a["topics"]
        selected, past = [a for a in current if match(a)], [a for a in previous if match(a)]
        rows.append({"id": topic["id"], "name": topic["name"], "count": len(selected), "previous_count": len(past),
            "share": len(selected) / len(current) if current else None,
            "previous_share": len(past) / len(previous) if previous else None,
            "articles": selected})
    return {"schema_version": "1.0", "generated_at": iso(now), "status": "ok", "scope": "briefing_sources",
        "from_date": str(start), "through_date": str(today), "previous_from_date": str(prior),
        "total": len(current), "previous_total": len(previous), "small_sample": min(len(current), len(previous)) < 10,
        "excluded_total": len(excluded), "registry_total": len(registry), "topics": rows,
        "latest_briefing_at": max((b["published_at"] for a in registry for b in a["briefings"]), default=None)}

def normalize_event(raw, issue=None, checked_at=None):
    required = ("event_id", "title", "summary", "start_date", "end_date", "source_url", "status")
    if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in required):
        raise ValueError("Event fields missing")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", raw["event_id"]) or raw["status"] not in ("confirmed", "tentative", "cancelled"):
        raise ValueError("Invalid event identity/status")
    source = urllib.parse.urlsplit(raw["source_url"])
    if source.scheme not in ("http", "https") or not source.hostname or source.username:
        raise ValueError("Invalid event source")
    event = copy.deepcopy(raw)
    start, end = date.fromisoformat(raw["start_date"]), date.fromisoformat(raw["end_date"])
    if end < start:
        raise ValueError("Reversed event dates")
    ks, ke = start, end
    for field, d in (("start_at", start), ("end_at", end)):
        value = raw.get(field)
        if value:
            t = stamp(value)
            kd = t.astimezone(KST).date()
            if d not in (t.date(), kd):
                raise ValueError("Timestamp/date mismatch")
            event[field] = iso(t)
            if field == "start_at": ks = kd
            else: ke = kd
        else: event[field] = None
    if event["start_at"] and not event["end_at"]:
        ke += ks - start
    if ke < ks or (event["start_at"] and event["end_at"] and stamp(event["end_at"]) < stamp(event["start_at"])):
        raise ValueError("Reversed event times")
    event.update(start_date=str(ks), end_date=str(ke), briefings=[issue] if issue else [],
        checked_at=checked_at or (issue or {}).get("published_at"), supplemental=issue is None)
    event.setdefault("kind", "other")
    event.setdefault("participation", "참여·보상 조건은 공식 출처에서 확인해 주세요.")
    event.setdefault("platforms", "플랫폼별 조건 확인 필요")
    event.setdefault("date_basis", "timestamp_kst" if event["start_at"] else "announced_date")
    return event

def events_feed(snapshots, supplements, now):
    merged, invalid = {}, 0
    aliases = supplements.get("event_aliases", {})
    # Newest record wins, including cancelled; old active events remain in the archive.
    for snap in sorted(snapshots, key=lambda s: stamp(s["issue"]["published_at"]), reverse=True):
        for raw in snap["document"].get("calendar_events", []):
            try: event = normalize_event(raw, snap["issue"])
            except (ValueError, TypeError):
                invalid += 1
                continue
            key = aliases.get(event["event_id"], event["event_id"])
            event["event_id"] = key
            if key not in merged:
                merged[key] = event
            elif snap["issue"]["id"] not in [b["id"] for b in merged[key]["briefings"]]:
                merged[key]["briefings"].append(snap["issue"])
    for raw in supplements["calendar_events"]:
        if not raw.get("verified_at") or not raw.get("evidence"):
            raise ValueError("Supplement requires verification time and evidence")
        event = normalize_event(raw, checked_at=raw["verified_at"])
        key = aliases.get(event["event_id"], event["event_id"])
        event["event_id"] = key
        old = merged.get(key)
        # A newer dated briefing must override an older supplement, especially cancellations.
        if old is None or stamp(event["checked_at"]) > stamp(old["checked_at"]):
            event["briefings"] = old["briefings"] if old else []
            merged[key] = event
    for key, extra in supplements.get("event_details", {}).items():
        if key in merged:
            for field in ("kind", "participation", "platforms"):
                if field in extra: merged[key][field] = extra[field]
    notices = supplements.get("server_notices", [])
    for notice in notices:
        if (notice.get("region") not in ("global", "asia", "us-west") or
            notice.get("status") not in ("degraded", "outage", "maintenance", "resolved") or
            not notice.get("evidence") or not notice.get("title") or not notice.get("id") or
            not url_key(notice.get("source_url")) or
            not timedelta(0) < stamp(notice["expires_at"]) - stamp(notice["verified_at"]) <= timedelta(days=7)):
            raise ValueError("Invalid verified server notice")
    return {"schema_version": "1.0", "generated_at": iso(now), "status": "ok", "invalid_event_count": invalid,
        "server_notices": notices,
        "events": sorted(merged.values(), key=lambda x: (x["start_date"], x["event_id"]))}

def slot(d):
    # Actual acquisition window, NOT a claim that GitHub ran at the scheduled minute.
    shifted = int(d.timestamp()) - 13 * 60
    return iso(datetime.fromtimestamp((shifted // 1800) * 1800 + 13 * 60, UTC))

def player_record(payload, observed, trigger="manual"):
    r = payload.get("response", {})
    value = r.get("player_count")
    if r.get("result") != 1 or type(value) is not int or not 0 <= value <= 100_000_000:
        raise ValueError("Invalid Steam count; never substitute zero")
    return {"observed_at": iso(observed), "slot": slot(observed), "count": value,
        "trigger": trigger, "schedule": "13,43 * * * *", "run_id": os.environ.get("GITHUB_RUN_ID")}

def player_feed(records, now, attempt_ok=True, collection_started_at=None):
    unique = {}
    for r in sorted(records, key=lambda x: stamp(x["observed_at"])):
        if type(r.get("count")) is not int or r["count"] < 0 or stamp(r["observed_at"]) > now + timedelta(minutes=1):
            raise ValueError("Invalid stored observation")
        unique.setdefault(r["slot"], r)
    ordered = list(unique.values())
    points = [r for r in ordered if stamp(r["observed_at"]) >= now - timedelta(days=30)]
    since = collection_started_at or (ordered[0]["observed_at"] if ordered else None)
    heat = []
    ready = since is not None and now - stamp(since) >= timedelta(days=28)
    for weekday in range(7):
        for hour in range(24):
            cell = [r for r in points if stamp(r["observed_at"]) >= now - timedelta(days=28)
                and stamp(r["observed_at"]).astimezone(KST).weekday() == weekday
                and stamp(r["observed_at"]).astimezone(KST).hour == hour]
            dates = {stamp(r["observed_at"]).astimezone(KST).date() for r in cell}
            value = statistics.median(r["count"] for r in cell) if ready and len(cell) >= 6 and len(dates) >= 3 else None
            heat.append({"weekday": weekday, "hour": hour, "count": len(cell), "median": value})
    return {"schema_version": "1.0", "generated_at": iso(now), "status": "ok" if attempt_ok else "error",
        "source_url": STEAM, "scope": "steam_global_online", "interval_minutes": 30,
        "collection_started_at": since, "last_observed_at": ordered[-1]["observed_at"] if ordered else None,
        "points": points, "heatmap_ready": ready, "heatmap": heat}

def acquire_snapshots(root):
    tree = get_json(TREE)
    if tree.get("truncated"):
        raise ValueError("Upstream file tree truncated")
    hashes = {x["path"]: x["sha"] for x in tree["tree"] if x["type"] == "blob"}
    state_path = root / "state/source-state.json"
    previous = read(state_path, {"sha": None, "issues": {}})
    if previous["sha"] == tree["sha"]:
        return [read(p) for i in previous["issues"] if (p := root / "state/issues" / (i + ".json")).exists()], tree["sha"]
    idx, latest = get_json(FEED + "feed/index.json"), get_json(FEED + "feed/latest.json")
    issues = {i["id"]: i for i in idx["issues"]}
    if latest.get("issue"): issues[latest["issue"]["id"]] = latest["issue"]
    pending, merged = {}, dict(previous["issues"])
    for iid, issue in issues.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", iid) or not safe_feed_url(issue.get("json_url"), "json") or not safe_feed_url(issue.get("html_url"), "html"):
            raise ValueError("Invalid upstream issue reference")
        stamp(issue["published_at"])
        path = issue["json_url"][len(FEED):]
        sha = hashes.get(path)
        if not sha:
            raise ValueError("Missing issue JSON in tree")
        cache = root / "state/issues" / (iid + ".json")
        if not cache.exists() and previous["issues"].get(iid) == sha:
            continue  # Previously compacted unchanged source; do not download it every run.
        old = read(cache)
        if old and previous["issues"].get(iid) == sha:
            document = old["document"]
        else:
            document = get_json(issue["json_url"])
            list(items(document))  # reject unknown shapes instead of empty success
        pending[iid] = {"issue": issue, "document": document}
        merged[iid] = sha
    # No writes until ALL changed upstream files were downloaded successfully.
    for iid, data in pending.items(): write(root / "state/issues" / (iid + ".json"), data)
    write(state_path, {"sha": tree["sha"], "issues": merged})
    return [value for i in merged if (value := pending.get(i) or read(root / "state/issues" / (i + ".json"))) is not None], tree["sha"]

def compact_articles(root, registry, aliases, now):
    # Keep a small identity -> earliest-inclusion index, not old article bodies/evidence.
    known = read(root / 'state/article-first-seen.json', {'articles': {}})['articles']
    previous = read(root / 'data/article-registry.json', {'articles': []})['articles']
    merged = {}
    for url, first in list(known.items()) + [(a['url'], a['first_included_at']) for a in previous + registry]:
        key = aliases.get(url, url)
        if key not in merged or stamp(first) < stamp(merged[key]): merged[key] = first
    retained = []
    cutoff = now.astimezone(KST).date() - timedelta(days=DETAIL_DAYS - 1)
    for original in registry:
        article = copy.deepcopy(original)
        article['first_included_at'] = merged[article['url']]
        if stamp(article['first_included_at']).astimezone(KST).date() >= cutoff:
            retained.append(article)
    return retained, {'schema_version': '1.0', 'articles': dict(sorted(merged.items()))}

def compact_events(root, document, aliases, now):
    old = read(root / 'state/event-registry.json')
    if old is None:
        old = {'heads': {}, 'events': read(root / 'data/opportunities.json', {'events': []})['events']}
    heads, candidates = {}, {}
    current_ids = {e['event_id'] for e in document['events'] if e['supplemental']}
    for old_id, value in old['heads'].items():
        key = aliases.get(old_id, old_id)
        if value.get('supplemental') and key not in current_ids: continue
        if key not in heads or stamp(value['checked_at']) > stamp(heads[key]['checked_at']): heads[key] = value
    # Stored regular events preserve long-running campaigns beyond the raw-source retention window.
    # Removed manual supplements are not resurrected from the stored registry.
    for original in [e for e in old['events'] if not e['supplemental']] + document['events']:
        event = copy.deepcopy(original)
        key = aliases.get(event['event_id'], event['event_id']); event['event_id'] = key
        head = heads.get(key)
        if head and stamp(event['checked_at']) < stamp(head['checked_at']): continue
        event['briefings'] = sorted(event['briefings'], key=lambda b: stamp(b['published_at']), reverse=True)[:25]
        candidates[key] = event
        heads[key] = {k: event[k] for k in ('checked_at', 'status', 'end_date', 'supplemental')}
    cutoff = now.astimezone(KST).date() - timedelta(days=DETAIL_DAYS - 1)
    retained = [e for e in candidates.values() if date.fromisoformat(e['end_date']) >= cutoff]
    result = {**document, 'events': sorted(retained, key=lambda e: (e['start_date'], e['event_id']))}
    return result, {'schema_version': '1.0', 'heads': dict(sorted(heads.items())), 'events': result['events']}

def prune_source_cache(root, now):
    cutoff = now.astimezone(KST).date() - timedelta(days=DETAIL_DAYS - 1)
    for path in (root / 'state/issues').glob('*.json'):
        if stamp(read(path)['issue']['published_at']).astimezone(KST).date() < cutoff:
            path.unlink()  # Only the independent collector's copy; the source feed is read-only.

def retain_observations(root, records, now, collection_started_at):
    today = now.astimezone(KST).date()
    cutoff, summary_cutoff = today - timedelta(days=DETAIL_DAYS - 1), today - timedelta(days=SUMMARY_DAYS - 1)
    old = read(root / 'data/player-daily.json', {'days': []})
    summaries = {d['date']: d for d in old['days'] if date.fromisoformat(d['date']) >= summary_cutoff}
    unique = {}
    for record in sorted(records, key=lambda r: stamp(r['observed_at'])):
        if type(record['count']) is not int or record['count'] < 0 or stamp(record['observed_at']) > now + timedelta(minutes=1):
            raise ValueError('Invalid observation; retention aborted')
        unique.setdefault(record['slot'], record)
    kept, expired = [], {}
    for record in unique.values():
        day = stamp(record['observed_at']).astimezone(KST).date()
        if day >= cutoff: kept.append(record)
        elif day >= summary_cutoff: expired.setdefault(str(day), []).append(record)
    for day, rows in expired.items():
        if day in summaries: continue  # Finalized whole days: restart cannot double count leftover rows.
        counts = [r['count'] for r in rows]
        summaries[day] = {'date': day, 'samples': len(rows), 'expected_samples': 48,
            'minimum': min(counts), 'maximum': max(counts), 'sum': sum(counts),
            'mean': sum(counts) / len(counts), 'first_observed_at': rows[0]['observed_at'],
            'last_observed_at': rows[-1]['observed_at']}
    daily = {'schema_version': '1.0', 'timezone': 'Asia/Seoul', 'retention_days': SUMMARY_DAYS,
        'days': [summaries[k] for k in sorted(summaries)]}
    # Save summaries before replacing raw files; interruption cannot discard the only remaining copy.
    write(root / 'data/player-daily.json', daily)
    write(root / 'state/retention.json', {'schema_version': '1.0', 'collection_started_at': collection_started_at,
        'detail_days': DETAIL_DAYS, 'summary_days': SUMMARY_DAYS})
    months = {}
    for record in kept:
        months.setdefault(stamp(record['observed_at']).astimezone(UTC).strftime('%Y-%m'), []).append(record)
    folder = root / 'observations'; folder.mkdir(parents=True, exist_ok=True)
    for month, rows in months.items():
        path = folder / (month + '.jsonl')
        text = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)
        if not path.exists() or path.read_text(encoding='utf-8') != text:
            temp = path.with_suffix('.tmp'); temp.write_text(text, encoding='utf-8'); temp.replace(path)
    for path in folder.glob('*.jsonl'):
        if path.stem not in months: path.unlink()
    return kept, daily

def storage_status(root, now):
    size = sum(p.stat().st_size for folder in ('data', 'state', 'observations') for p in (root / folder).rglob('*')
        if p.is_file() and p.name != 'storage-status.json')
    event_count = len(read(root / 'data/opportunities.json', {'events': []})['events'])
    warnings = []
    if size >= 100 * 1024 * 1024: warnings.append('Generated working files exceed 100 MiB; review storage.')
    if event_count >= 2500: warnings.append('Calendar approaches the 3000 event client limit; review event identities.')
    for warning in warnings: print('::warning::' + warning)
    write(root / 'data/storage-status.json', {'schema_version': '1.0', 'generated_bytes': size,
        'events': event_count, 'source_cache_files': len(list((root / 'state/issues').glob('*.json'))),
        'detail_days': DETAIL_DAYS, 'summary_days': SUMMARY_DAYS, 'warnings': warnings})

def run(root, input_dir=None):
    now = datetime.now(UTC)
    errors = []
    try:
        supplements = read(root / "config/event-supplements.json")
        if input_dir:
            idx = read(input_dir / "feed/index.json")
            snapshots = [{"issue": i, "document": read(input_dir / i["json_url"][len(FEED):])} for i in idx["issues"]]
            source_sha = "local-validation"
        else:
            snapshots, source_sha = acquire_snapshots(root)
        rules, overrides = read(root / "config/topics.json"), read(root / "config/article-overrides.json")
        registry, excluded = topic_registry(snapshots, rules, overrides)
        registry, article_index = compact_articles(root, registry, overrides.get('url_aliases', {}), now)
        recent_ids = {s['issue']['id'] for s in snapshots if stamp(s['issue']['published_at']).astimezone(KST).date()
                      >= now.astimezone(KST).date() - timedelta(days=DETAIL_DAYS - 1)}
        excluded = [e for e in excluded if e['briefing_id'] in recent_ids]
        topics = topics_feed(registry, excluded, rules, now)
        topics['registry_total'] = len(article_index['articles'])
        topics['latest_briefing_at'] = max((s['issue']['published_at'] for s in snapshots), default=None)
        events = events_feed(snapshots, supplements, now)
        events, event_registry = compact_events(root, events, supplements.get('event_aliases', {}), now)
        for value in (topics, events): value.update(source_sha=source_sha, source_checked_at=iso(now))
        validate_feed("topics", topics)
        validate_feed("opportunities", events)
        write(root / "data/topics.json", topics)
        write(root / "data/opportunities.json", events)
        write(root / "data/article-registry.json", {"schema_version": "1.0", "articles": registry, "excluded": excluded})
        write(root / 'state/article-first-seen.json', article_index)
        write(root / 'state/event-registry.json', event_registry)
        prune_source_cache(root, now)
        # v44 calendar consumes supplemental entries via opportunities.json; pushes remain isolated.
        fields = ("event_id", "title", "summary", "start_date", "end_date", "source_url", "status", "start_at", "end_at")
        write(root / "data/calendar-supplements.json", {"schema_version": "1.0", "updated_at": supplements["updated_at"],
            "calendar_events": [{k: e.get(k) for k in fields} for e in events["events"] if e["supplemental"]]})
    except Exception as ex:
        errors.append("briefings: " + type(ex).__name__)
        for name in ("topics", "opportunities"):
            old = read(root / f"data/{name}.json")
            if old:
                old.update(status="error", last_attempt_at=iso(now))
                write(root / f"data/{name}.json", old)
    if not input_dir:
        records = []
        for path in sorted((root / "observations").glob("*.jsonl")):
            records.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
        ok = True
        if slot(now) not in {r["slot"] for r in records}:
            try:
                payload = get_json(STEAM)
                observed = datetime.now(UTC)
                record = player_record(payload, observed, os.environ.get("GITHUB_EVENT_NAME", "manual"))
                if record["slot"] not in {r["slot"] for r in records}:
                    path = root / "observations" / (observed.strftime("%Y-%m") + ".jsonl")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as f: f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    records.append(record)
            except Exception as ex:
                ok = False
                errors.append("players: " + type(ex).__name__)
        started = read(root / 'state/retention.json', {}).get('collection_started_at')
        started = started or read(root / 'data/players.json', {}).get('collection_started_at')
        started = started or min((r['observed_at'] for r in records), default=None)
        observed_now = datetime.now(UTC)
        players = player_feed(records, observed_now, ok, started)
        validate_feed("players", players)
        write(root / "data/players.json", players)
        retain_observations(root, records, observed_now, started)
    storage_status(root, now)
    write(root / "data/status.json", {"schema_version": "1.0", "last_attempt_at": iso(datetime.now(UTC)), "errors": errors})
    print(json.dumps({"errors": errors, "read_only_upstream": True}))
    return bool(errors)

def validate_feed(kind, value):
    if value["schema_version"] != "1.0" or value["status"] not in ("ok", "error"):
        raise ValueError("Unknown feed version/status")
    stamp(value["generated_at"])
    if kind == "opportunities":
        ids = [e["event_id"] for e in value["events"]]
        if len(ids) > 3000: raise ValueError('Client event limit exceeded')
        if len(ids) != len(set(ids)): raise ValueError("Duplicate events")
        for event in value["events"]: normalize_event(event)
    elif kind == "topics":
        for t in value["topics"]:
            if t["count"] != len(t["articles"]) or not 0 <= t["count"] <= value["total"] or not 0 <= t["previous_count"] <= value["previous_total"]:
                raise ValueError("Invalid topic denominator")
            if t["share"] != (t["count"] / value["total"] if value["total"] else None): raise ValueError("Invalid share")
    elif kind == "players":
        if len(value["points"]) != len({p["slot"] for p in value["points"]}): raise ValueError("Duplicate observation")
        if any(type(p["count"]) is not int or p["count"] < 0 for p in value["points"]): raise ValueError("Invalid observation")
    if len(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('Client feed size limit exceeded')

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, help="Offline validation, never collects Steam observations")
    args = parser.parse_args()
    sys.exit(run(ROOT, args.input_dir))
