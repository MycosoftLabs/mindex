"""Create a review hash manifest for the explicit brief09 source allowlist."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ['mindex_api/retention/*.py', 'mindex_api/routers/retention.py',
    'mindex_api/main.py', 'pyproject.toml', 'migrations/20261001_shared_retention_v1.sql',
    'migrations/20261002_private_orphan_reconciliation.sql',
    'migrations/20261003_backfill_preupgrade_orphan_reconciliation.sql',
    'mindex_etl/jobs/retention_worker.py', 'sdk/typescript/retention-v1*',
    'sdk/retention-v1.schema.json', 'tests/test_retention_*.py', 'scripts/retention_*.py',
    'scripts/retention_*.ps1', 'docs/retention/*.md']
paths = sorted({path for pattern in PATTERNS for path in ROOT.glob(pattern) if path.is_file()})
document = {'schema': 'brief09-file-manifest.v1',
    'base_commit': '42b876fcfca2e86b0365e8fe8afab628d6a94705',
    'repository': 'https://github.com/MycosoftLabs/mindex.git',
    'branch': subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),
    'hash_semantics': 'sha256 is working-file bytes; normalized_lf_sha256 is portable text with CRLF normalized to LF',
    'files': [{'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size,
               'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'normalized_lf_sha256': hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()}
              for path in paths]}
output=ROOT/'docs/retention/FILE_MANIFEST.json'
output.write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8')
print(f'Wrote {len(paths)} file hashes to {output}')
