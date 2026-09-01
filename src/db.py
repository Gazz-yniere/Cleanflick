import sqlite3
import json
import os
import time

# Racine du projet (le module vit dans src/ mais la BDD reste à la racine).
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'cleanflick.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # Improve concurrency for multiple connections
    try:
        cur.execute('PRAGMA journal_mode=WAL;')
        cur.execute('PRAGMA synchronous=NORMAL;')
    except Exception:
        pass
    cur.execute('''
    CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY,
        op TEXT,
        date TEXT,
        from_path TEXT,
        from_name TEXT,
        to_path TEXT,
        to_name TEXT,
        extra TEXT
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS search_cache (
        key TEXT PRIMARY KEY,
        media_type TEXT,
        results_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS file_search_cache (
        fkey TEXT PRIMARY KEY,
        file_path TEXT,
        size INTEGER,
        mtime INTEGER,
        media_type TEXT,
        results_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS details_cache (
        kind TEXT PRIMARY KEY,
        id TEXT,
        details_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS omdb_cache (
        imdb_id TEXT PRIMARY KEY,
        rating TEXT,
        data_json TEXT,
        loaded_at INTEGER
    )''')
    try:
        cur.execute('ALTER TABLE omdb_cache ADD COLUMN data_json TEXT')
    except Exception:
        pass
    cur.execute('''
    CREATE TABLE IF NOT EXISTS omdb_episode_cache (
        key TEXT PRIMARY KEY,
        rating TEXT,
        data_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS series_episodes_cache (
        series_id TEXT PRIMARY KEY,
        data_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS series_meta_cache (
        tvdb_id TEXT PRIMARY KEY,
        imdb_id TEXT,
        data_json TEXT,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS usage_counters (
        service TEXT PRIMARY KEY,
        total INTEGER,
        day TEXT,
        day_count INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS media_duration_cache (
        path TEXT PRIMARY KEY,
        size INTEGER,
        mtime INTEGER,
        minutes INTEGER,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS folder_size_cache (
        path TEXT PRIMARY KEY,
        mtime INTEGER,
        size INTEGER,
        loaded_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    conn.close()


def add_history(entry: dict):
    conn = get_conn()
    cur = conn.cursor()
    known = {'id', 'op', 'date', 'from_path', 'from_name', 'to_path', 'to_name'}
    extra = {k: v for k, v in entry.items() if k not in known}
    cur.execute('''INSERT OR REPLACE INTO history (id, op, date, from_path, from_name, to_path, to_name, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
        entry.get('id'), entry.get('op'), entry.get('date'), entry.get('from_path'), entry.get('from_name'),
        entry.get('to_path'), entry.get('to_name'), json.dumps(extra, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()


def get_history():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM history ORDER BY date DESC')
    rows = cur.fetchall()
    result = []
    for r in rows:
        extra = {}
        try:
            extra = json.loads(r['extra'] or '{}')
        except Exception:
            extra = {}
        item = {
            'id': r['id'], 'op': r['op'], 'date': r['date'],
            'from_path': r['from_path'], 'from_name': r['from_name'],
            'to_path': r['to_path'], 'to_name': r['to_name'],
            **extra
        }
        result.append(item)
    conn.close()
    return result


def clear_history():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM history')
    conn.commit()
    conn.close()


def cache_get(key: str, max_age_seconds: int = None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT media_type, results_json, loaded_at FROM search_cache WHERE key = ?', (key,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return None
    if max_age_seconds is not None and int(time.time()) - int(r['loaded_at'] or 0) > max_age_seconds:
        return None
    try:
        return {'media_type': r['media_type'], 'results': json.loads(r['results_json'] or '[]')}
    except Exception:
        return None


def cache_set(key: str, media_type: str, results: list):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO search_cache (key, media_type, results_json, loaded_at) VALUES (?, ?, ?, ?)', (
        key, media_type, json.dumps(results, ensure_ascii=False), int(time.time())
    ))
    conn.commit()
    conn.close()


def file_cache_get(fkey: str, max_age_seconds: int = None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT media_type, results_json, loaded_at FROM file_search_cache WHERE fkey = ?', (fkey,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return None
    if max_age_seconds is not None and int(time.time()) - int(r['loaded_at'] or 0) > max_age_seconds:
        return None
    try:
        return {'media_type': r['media_type'], 'results': json.loads(r['results_json'] or '[]')}
    except Exception:
        return None


def file_cache_set(fkey: str, file_path: str, size: int, mtime: int, media_type: str, results: list):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO file_search_cache (fkey, file_path, size, mtime, media_type, results_json, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (
        fkey, file_path, int(size), int(mtime), media_type, json.dumps(results, ensure_ascii=False), int(time.time())
    ))
    conn.commit()
    conn.close()


def file_cache_migrate(old_fkey: str, new_fkey: str, new_file_path: str, size: int, mtime: int, old_name: str = None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT file_path, results_json, media_type FROM file_search_cache WHERE fkey = ?', (old_fkey,))
        r = cur.fetchone()
        if not r:
            return False
        # N'émigre que si le nom du fichier n'a pas changé (sinon le résultat ne correspond plus)
        if old_name and os.path.basename(r['file_path']) != old_name:
            return False
        results_json = r['results_json']
        media_type = r['media_type']
        cur.execute('INSERT OR REPLACE INTO file_search_cache (fkey, file_path, size, mtime, media_type, results_json, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (
            new_fkey, new_file_path, int(size), int(mtime), media_type, results_json, int(time.time())
        ))
        cur.execute('DELETE FROM file_search_cache WHERE fkey = ? AND fkey != ?', (old_fkey, new_fkey))
        conn.commit()
        return True
    finally:
        conn.close()


def details_get(kind: str, id: str, max_age_seconds: int = None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT details_json, loaded_at FROM details_cache WHERE kind = ?', (f"{kind}:{id}",))
        r = cur.fetchone()
        if not r:
            return None
        if max_age_seconds is not None and int(time.time()) - int(r['loaded_at'] or 0) > max_age_seconds:
            return None
        try:
            return json.loads(r['details_json'] or '{}')
        except Exception:
            return None
    finally:
        conn.close()


def details_set(kind: str, id: str, details: dict):
    conn = get_conn()
    cur = conn.cursor()
    try:
        key = f"{kind}:{id}"
        cur.execute('INSERT OR REPLACE INTO details_cache (kind, id, details_json, loaded_at) VALUES (?, ?, ?, ?)', (
            key, id, json.dumps(details, ensure_ascii=False), int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def omdb_get(imdb_id: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT rating, data_json FROM omdb_cache WHERE imdb_id = ?', (imdb_id.lower(),))
        r = cur.fetchone()
        if not r:
            return None
        try:
            return json.loads(r['data_json'] or '{}') or {'imdbRating': r['rating']}
        except Exception:
            return {'imdbRating': r['rating']}
    finally:
        conn.close()


def omdb_set(imdb_id: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO omdb_cache (imdb_id, rating, data_json, loaded_at) VALUES (?, ?, ?, ?)', (
            imdb_id.lower(), data.get('imdbRating', '') or '', json.dumps(data, ensure_ascii=False), int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def omdb_all():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT imdb_id, data_json FROM omdb_cache')
        out = {}
        for r in cur.fetchall():
            try:
                out[r['imdb_id']] = json.loads(r['data_json'] or '{}')
            except Exception:
                continue
        return out
    finally:
        conn.close()


def folder_size_get(path: str, mtime: int):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT size FROM folder_size_cache WHERE path = ? AND mtime = ?', (path, mtime))
        r = cur.fetchone()
        if not r:
            return None
        return {'size': r['size']}
    finally:
        conn.close()


def folder_size_set(path: str, size, mtime):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO folder_size_cache (path, mtime, size, loaded_at) VALUES (?, ?, ?, ?)', (
            path, mtime, size, int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def omdb_episode_get(key: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT data_json FROM omdb_episode_cache WHERE key = ?', (key.lower(),))
        r = cur.fetchone()
        if not r:
            return None
        try:
            return json.loads(r['data_json'] or '{}')
        except Exception:
            return {}
    finally:
        conn.close()


def media_dur_get(path: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT size, mtime, minutes FROM media_duration_cache WHERE path = ?', (path,))
        r = cur.fetchone()
        if not r:
            return None
        return {'size': r['size'], 'mtime': r['mtime'], 'minutes': r['minutes']}
    finally:
        conn.close()


def media_dur_set(path: str, minutes, size, mtime):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO media_duration_cache (path, size, mtime, minutes, loaded_at) VALUES (?, ?, ?, ?, ?)', (
            path, size, mtime, minutes, int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def omdb_episode_set(key: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO omdb_episode_cache (key, rating, data_json, loaded_at) VALUES (?, ?, ?, ?)', (
            key.lower(), data.get('imdbRating', '') or '', json.dumps(data, ensure_ascii=False), int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def omdb_episode_all():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT key, data_json FROM omdb_episode_cache')
        out = {}
        for r in cur.fetchall():
            try:
                out[r['key']] = json.loads(r['data_json'] or '{}')
            except Exception:
                continue
        return out
    finally:
        conn.close()


def series_episodes_get(series_id: str, max_age: int = None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT data_json, loaded_at FROM series_episodes_cache WHERE series_id = ?', (series_id.lower(),))
        r = cur.fetchone()
        if not r:
            return None
        if max_age is not None and r['loaded_at'] and (time.time() - r['loaded_at']) > max_age:
            return None
        try:
            return json.loads(r['data_json'] or '{}')
        except Exception:
            return None
    finally:
        conn.close()


def series_episodes_set(series_id: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO series_episodes_cache (series_id, data_json, loaded_at) VALUES (?, ?, ?)', (
            series_id.lower(), json.dumps(data, ensure_ascii=False), int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def series_meta_get(tvdb_id: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT imdb_id, data_json, loaded_at FROM series_meta_cache WHERE tvdb_id = ?', (str(tvdb_id).lower(),))
        r = cur.fetchone()
        if not r:
            return None
        try:
            return {'imdb_id': r['imdb_id'] or '', 'data': json.loads(r['data_json'] or '{}'), 'loaded_at': r['loaded_at'] or 0}
        except Exception:
            return {'imdb_id': r['imdb_id'] or '', 'data': {}, 'loaded_at': r['loaded_at'] or 0}
    finally:
        conn.close()


def series_meta_set(tvdb_id: str, imdb_id: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO series_meta_cache (tvdb_id, imdb_id, data_json, loaded_at) VALUES (?, ?, ?, ?)', (
            str(tvdb_id).lower(), (imdb_id or '').strip().lower(), json.dumps(data or {}, ensure_ascii=False), int(time.time())
        ))
        conn.commit()
    finally:
        conn.close()


def series_meta_all():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT tvdb_id, imdb_id, data_json FROM series_meta_cache')
        out = {}
        for r in cur.fetchall():
            try:
                out[r['tvdb_id']] = {'imdb_id': r['imdb_id'] or '', 'data': json.loads(r['data_json'] or '{}')}
            except Exception:
                continue
        return out
    finally:
        conn.close()


def refresh_due(table: str, refresh_days: int, now: float = None) -> list:
    """Clés d'une table de cache (search_cache, file_search_cache, details_cache,
    series_episodes_cache) dont loaded_at est plus vieux que refresh_days.
    Les entrées sans loaded_at sont exclues. now permet de tester."""
    if not refresh_days or refresh_days <= 0:
        return []
    if now is None:
        now = time.time()
    cutoff = int(now) - int(refresh_days * 86400)
    col = {'search_cache': 'key', 'file_search_cache': 'fkey',
           'details_cache': 'kind', 'series_episodes_cache': 'series_id'}[table]
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT {col}, loaded_at FROM {table} WHERE loaded_at IS NOT NULL AND loaded_at <= ?', (cutoff,))
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


_usage_listener = None


def usage_set_listener(fn):
    """Callback appelé après chaque usage_bump (pour le temps réel UI)."""
    global _usage_listener
    _usage_listener = fn


def usage_bump(service: str):
    conn = get_conn()
    cur = conn.cursor()
    today = time.strftime('%Y-%m-%d')
    try:
        cur.execute('SELECT total, day, day_count FROM usage_counters WHERE service = ?', (service,))
        r = cur.fetchone()
        if not r:
            cur.execute('INSERT INTO usage_counters (service, total, day, day_count) VALUES (?, ?, ?, ?)',
                        (service, 1, today, 1))
        elif r['day'] == today:
            cur.execute('UPDATE usage_counters SET total = total + 1, day_count = day_count + 1 WHERE service = ?', (service,))
        else:
            cur.execute('UPDATE usage_counters SET total = total + 1, day = ?, day_count = 1 WHERE service = ?', (today, service))
        conn.commit()
    finally:
        conn.close()
    if _usage_listener:
        try:
            _usage_listener(service)
        except Exception:
            pass


def usage_get():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT service, total, day, day_count FROM usage_counters')
        out = {}
        for r in cur.fetchall():
            out[r['service']] = {'total': r['total'], 'day': r['day'], 'day_count': r['day_count']}
        return out
    finally:
        conn.close()


def meta_get(key: str, default=None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT value FROM app_meta WHERE key = ?', (key,))
        r = cur.fetchone()
        return r['value'] if r else default
    except Exception:
        return default
    finally:
        conn.close()


def meta_set(key: str, value):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# Initialize DB on import
try:
    init_db()
except Exception:
    pass
