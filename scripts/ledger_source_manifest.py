"""Reproduce a bounded file/hash manifest for the brief10 patch, without secrets."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "42b876fcfca2e86b0365e8fe8afab628d6a94705"
RETENTION_COMMITS = ["c626870bb27a52f3d45ac22be95550c2d8a8eef5",
                     "92bcb38356f96ecf89abc960c7d018a1a64e246a"]
OWNED = [
    "mindex_api/main.py", "mindex_api/provenance_access.py", "pyproject.toml",
    "mindex_api/routers/provenance.py", "mindex_api/routers/ledger.py",
    "mindex_api/routers/integrity.py", "mindex_api/routers/ip_assets.py",
    "mindex_api/ledger/anchor_service.py", "mindex_api/ledger/dag.py",
    "mindex_api/ledger/hypergraph_client.py", "mindex_api/ledger/op_return.py",
    "mindex_api/ledger/provenance.py", "mindex_api/ledger/provenance_store.py",
    "mindex_api/ledger/provenance_proof.py", "migrations/20261001_ledger_provenance.sql",
    "tests/test_ledger_legacy_safety.py", "tests/test_ledger_provenance.py",
    "tests/test_ledger_provenance_proof.py", "tests/test_provenance_api.py",
    "tests/test_provenance_retention_integration.py", "tests/test_provenance_postgres.py",
    "scripts/benchmark_ledger_provenance.py", "scripts/ledger_source_manifest.py",
]


def entry(path, baseline=False):
    data = path.read_bytes()
    value = {"path": path.relative_to(ROOT).as_posix(), "bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest(),
             "lf_normalized_sha256": hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()}
    if baseline:
        original = subprocess.run(["git", "show", f"{BASELINE}:{value['path']}"],
                                  cwd=ROOT, capture_output=True)
        value["baseline_sha256"] = hashlib.sha256(original.stdout).hexdigest() if original.returncode == 0 else None
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared-source", type=Path)
    args = parser.parse_args()
    documents = sorted(path for path in (ROOT / "docs/ledger-provenance").iterdir()
                       if path.is_file() and path.name != "source-manifest.json")
    files = [entry(ROOT / path, True) for path in OWNED] + [entry(path) for path in documents]
    shared = []
    if args.shared_source:
        # Only reviewed source code; no credentials, environment or ignored files.
        package = args.shared_source / "mindex_api"
        for relative in ["routers/retention.py", "retention/contracts.py", "retention/identity.py",
                         "retention/service.py", "retention/repository.py", "retention/object_store.py"]:
            data = (package / relative).read_bytes()
            shared.append({"path": "mindex_api/" + relative, "sha256": hashlib.sha256(data).hexdigest()})
    retained = {}
    for commit in RETENTION_COMMITS:
        paths = subprocess.check_output(
            ["git", "show", "--format=", "--name-only", commit], cwd=ROOT, text=True
        ).splitlines()
        for path in paths:
            if path and (ROOT / path).is_file():
                retained[path] = entry(ROOT / path)
    result = {"manifest_version": "brief10-source-v1", "baseline": BASELINE,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "files": files, "shared09_dependency_hashes": shared,
              "integrated_brief09_commits": RETENTION_COMMITS,
              "integrated_brief09_files": list(retained.values()),
              "excluded": ["mindex_test3_utf8.txt", "mindex_test4_utf8.txt", "mindex_test5_utf8.txt", "mindex_test_utf8.txt"],
              "no_dirty_parent_patch_imported": True}
    (ROOT / "docs/ledger-provenance/source-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
