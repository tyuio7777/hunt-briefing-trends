"""Weekly repository-size observation. No history rewriting or deletion."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KIB = 256 * 1024

def main():
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if repository not in ('tyuio7777/hunt-briefing-guestbook', 'tyuio7777/hunt-briefing-trends'):
        raise ValueError('Unexpected repository')
    path = ROOT / 'data/repository-size.json'
    now = dt.datetime.now(dt.timezone.utc)
    try:
        old = json.loads(path.read_text()) if path.exists() else {}
        checked = dt.datetime.fromisoformat(old['checked_at']) if old.get('checked_at') else None
        if checked and dt.timedelta(0) <= now - checked < dt.timedelta(days=7):
            if old['size_kib'] >= LIMIT_KIB:
                print('::warning::Repository size estimate exceeds 256 MiB. Review Git history growth; no automatic rewrite is performed.')
            return
        headers = {'User-Agent': 'HuntBriefing-Storage/1.0', 'Accept': 'application/vnd.github+json'}
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if token: headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request('https://api.github.com/repos/' + repository, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(65537)
        if len(raw) > 65536: raise ValueError('Metadata too large')
        value = json.loads(raw)
        size = value['size']
        if value['full_name'] != repository or type(size) is not int or size < 0: raise ValueError('Invalid metadata')
        status = {'schema_version': '1.0', 'checked_at': now.isoformat(timespec='seconds'),
                  'size_kib': size, 'warning_threshold_kib': LIMIT_KIB, 'source': 'github_repository_size_estimate'}
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp'); temp.write_text(json.dumps(status, indent=2) + '\n'); temp.replace(path)
        if size >= LIMIT_KIB:
            print('::warning::Repository size estimate exceeds 256 MiB. Review Git history growth; no automatic rewrite is performed.')
        print('Repository size estimate: ' + str(size) + ' KiB')
    except Exception:
        # A maintenance lookup must never stop new posts or player collection.
        print('::warning::Repository size could not be checked. Previous size record was preserved.')

if __name__ == '__main__': main()
