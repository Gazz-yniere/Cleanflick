from src import history


def test_history_path_status_cache_supports_explicit_refresh(tmp_path):
    path = str(tmp_path / 'entry.mkv')
    with open(path, 'wb') as media:
        media.write(b'x')
    history.invalidate_path_statuses()

    assert history.path_existence_statuses([path]) == {path: True}
    tmp_path.joinpath('entry.mkv').unlink()

    assert history.path_existence_statuses([path]) == {path: True}
    assert history.path_existence_statuses([path], refresh=True) == {path: False}
    history.invalidate_path_statuses()


def test_history_path_status_invalidation(tmp_path):
    path_obj = tmp_path / 'entry.mkv'
    path_obj.write_bytes(b'x')
    path = str(path_obj)
    history.invalidate_path_statuses()

    assert history.path_existence_statuses([path]) == {path: True}
    path_obj.unlink()
    history.invalidate_path_statuses([path])

    assert history.path_existence_statuses([path]) == {path: False}
    history.invalidate_path_statuses()


def test_history_endpoint_reuses_statuses_and_force_refreshes(
        app_client, tmp_path, monkeypatch):
    from src import db

    paths = [str(tmp_path / f'{name}.mkv') for name in ('from', 'to')]
    with open(paths[1], 'wb') as media:
        media.write(b'x')
    db.add_history({
        'id': 'status-cache', 'op': 'move', 'date': '2026-01-01',
        'from_path': paths[0], 'from_name': 'from.mkv',
        'to_path': paths[1], 'to_name': 'to.mkv',
    })
    history.invalidate_path_statuses()
    checks = []
    original_check = history._path_exists

    def counted_check(path):
        checks.append(path)
        return original_check(path)

    monkeypatch.setattr(history, '_path_exists', counted_check)

    first = app_client.get('/api/history?limit=100&offset=0')
    assert first.status_code == 200
    assert first.get_json()[0]['revert_status'] == 'available'
    assert len(checks) == 2

    second = app_client.get('/api/history?limit=100&offset=0')
    assert second.status_code == 200
    assert len(checks) == 2

    refreshed = app_client.get('/api/history?limit=100&offset=0&refresh=1')
    assert refreshed.status_code == 200
    assert len(checks) == 4
    history.invalidate_path_statuses()
