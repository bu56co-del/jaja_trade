"""Unpack the unchanged reviewed notebook sources; no network or package installs."""
import base64
import hashlib
import json
import lzma
from pathlib import Path

PAYLOAD_SHA256 = '1e4f519496779ab2371b0319c1977be0b81c94c99c12f249e5659181dc408bd0'
FILES = set('''LICENSE audit_data.py backtest_all.py cloud_audit.py config.json notebook_support.py
paperlab/__init__.py paperlab/backtest.py paperlab/common.py paperlab/engine.py
paperlab/feed.py paperlab/reports.py paperlab/runner.py paperlab/storage.py paperlab/synthetic.py
reviewed_audit.py tests/test_backtest.py tests/test_cloud_audit.py tests/test_paperlab.py tests/test_reviewed.py'''.split())


def unpack(root: Path) -> dict:
    chunks = sorted((root / 'bundle').glob('part*.b64'))
    if [p.name for p in chunks] != ['part%02d.b64' % i for i in range(7)]:
        raise ValueError('Incomplete source bundle')
    if any(p.is_symlink() or p.stat().st_size > 10001 for p in chunks):
        raise ValueError('Unexpected bundle file')
    encoded = ''.join(p.read_text(encoding='ascii').strip() for p in chunks)
    if len(encoded) != 68264:
        raise ValueError('Source length mismatch')
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
    raw = decoder.decompress(base64.b64decode(encoded, validate=True), max_length=1_000_001)
    if len(raw) > 1_000_000 or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or oversized source archive')
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA256:
        raise ValueError('Source checksum mismatch; refuse execution')
    payload = json.loads(raw)
    if set(payload['files']) != FILES or set(payload['sha256']) != FILES:
        raise ValueError('Unexpected source paths')
    for name, content in payload['files'].items():
        if hashlib.sha256(content.encode('utf-8')).hexdigest() != payload['sha256'][name]:
            raise ValueError('File checksum mismatch: ' + name)
    target = root / 'application'
    target.mkdir(exist_ok=False)
    for name, content in payload['files'].items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'source-manifest.json').write_text(json.dumps({
        'bundle_payload_sha256': PAYLOAD_SHA256, 'files': payload['sha256'],
        'note': 'Byte-identical application source from reviewed Colab v2; no strategy changes.'
    }, indent=2), encoding='utf-8')
    print('SOURCE_VERIFIED: %d files; original engine SHA256=%s' %
          (len(FILES), payload['sha256']['paperlab/engine.py']))
    return payload['sha256']


if __name__ == '__main__':
    unpack(Path(__file__).resolve().parent)
