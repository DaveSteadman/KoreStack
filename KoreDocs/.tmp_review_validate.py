# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# .tmp review validate helpers for KoreDocs.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import korefile
from server import app

client = TestClient(app)

created = client.post('/api/files', json={
    'folder_id': 1,
    'name': 'review_impl_test.koredoc',
    'content': '---\ntitle: review_impl_test\ncreated: 2026-04-26\n---\n\nhello',
}).json()
fid = created['id']
frev = created['revision']
renamed = client.patch(f'/api/files/{fid}', json={
    'name': 'review_impl_test_renamed.koredoc',
    'expected_revision': frev,
})
print('rename', renamed.status_code, renamed.json()['name'])
moved = client.patch(f'/api/files/{fid}', json={
    'folder_id': 1,
    'expected_revision': renamed.json()['revision'],
})
print('move', moved.status_code, moved.json()['id'])

folder = client.post('/api/folders', json={'name': 'review_folder_impl', 'parent_id': 1}).json()
folder_ok = client.patch(f"/api/folders/{folder['id']}", json={
    'name': 'review_folder_impl2',
    'expected_revision': folder['revision'],
})
print('folder-rename', folder_ok.status_code, folder_ok.json()['revision'])
folder_conflict = client.patch(f"/api/folders/{folder['id']}", json={
    'name': 'review_folder_impl3',
    'expected_revision': folder['revision'],
})
print('folder-conflict', folder_conflict.status_code, folder_conflict.json())

sheet = client.post('/api/files', json={
    'folder_id': 1,
    'name': 'review_sheet_impl.koresheet',
    'content': json.dumps({'version': 1, 'meta': {'title': 'review_sheet_impl'}, 'cols': 26, 'rows': 20, 'cells': {}}),
}).json()
srev = sheet['revision']
sheet_ok = client.post(f"/api/sheets/{sheet['id']}/cells", json={
    'cells': {'A1': 'x'},
    'expected_revision': srev,
})
print('sheet-write', sheet_ok.status_code, sheet_ok.json()['revision'])
sheet_conflict = client.post(f"/api/sheets/{sheet['id']}/cells", json={
    'cells': {'A2': 'y'},
    'expected_revision': srev,
})
print('sheet-conflict', sheet_conflict.status_code, sheet_conflict.json())

flat_invalid = client.put('/api/files/invalid_test.koresheet', json={'content': 'not json'})
print('flat-invalid', flat_invalid.status_code, flat_invalid.json())
client.put('/api/files/etag_test.koredoc', json={'content': 'alpha'})
etag = client.get('/api/files/etag_test.koredoc').headers.get('etag')
flat_conflict = client.put('/api/files/etag_test.koredoc', headers={'if-match': 'W/"stale"'}, json={'content': 'beta'})
print('flat-etag', bool(etag), flat_conflict.status_code, flat_conflict.json())

tmp = Path(tempfile.mkdtemp())
(tmp / 'bad.koresheet').write_text('not json', encoding='utf-8')
report = korefile.import_from_fs(tmp)
print('import-report', report['errors'], bool(report['error_details']))
