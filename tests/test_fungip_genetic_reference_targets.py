"""Portable target controls and actual captured projection; no database/network."""
import copy
import json
import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

import pytest

import test_fungip_genetic_references as captured

subject = captured.subject


def manifest():
    # Documentation-reserved identities: never an operational destination.
    return {"schema": subject.TARGET_SCHEMA,
            "connection": {"host": "database.example.invalid", "port": 5432, "dbname": "qualified_fixture"},
            "server": {"addresses": ["192.0.2.10", "2001:db8::10"], "port": 5432, "dbname": "qualified_fixture"}}


def binding(value=None):
    raw = json.dumps(manifest() if value is None else value).encode()
    return subject.TargetBinding(raw, subject.digest(raw))


def parameters():
    return {"host": "database.example.invalid", "port": "5432", "dbname": "qualified_fixture",
            "sslmode": "verify-full"}


def test_private_default_and_explicit_portable_target_are_distinct():
    subject.validate_target(subject.TARGET)
    with pytest.raises(subject.Rejected, match="wrong_database_target"):
        subject.validate_target(parameters())
    subject.validate_target(parameters(), binding())
    with pytest.raises(subject.Rejected, match="wrong_database_target"):
        subject.validate_target(subject.TARGET, binding())


@pytest.mark.parametrize("key,value", [
    ("host", "database.example.invalid,other.example.invalid"), ("port", "5432,5433"),
    ("dbname", "wrong_database"), ("hostaddr", "192.0.2.10"), ("service", "fixture"),
    ("options", "-c search_path=public"), ("target_session_attrs", "read-write"),
    ("sslmode", "prefer"), ("sslmode", "require"), ("sslmode", "verify-ca"),
])
def test_connection_override_or_weaker_tls_is_rejected(key, value):
    with pytest.raises(subject.Rejected):
        subject.validate_target(parameters() | {key: value}, binding())


@pytest.mark.parametrize("section,key,value", [
    ("connection", "host", "a,b"), ("connection", "host", "/unix/socket"),
    ("connection", "host", "user@database.example.invalid"), ("connection", "host", "127.1"),
    ("connection", "port", True), ("connection", "port", 0), ("connection", "port", 65536),
    ("connection", "dbname", ""), ("connection", "dbname", "x" * 64),
    ("server", "dbname", "different"), ("server", "addresses", []),
    ("server", "addresses", ["192.0.2.0/24"]), ("server", "addresses", ["0.0.0.0"]),
    ("server", "addresses", ["192.0.2.10", "192.0.2.10"]),
])
def test_manifest_rejects_ambiguous_or_incomplete_identities(section, key, value):
    data = manifest()
    data[section][key] = value
    with pytest.raises(subject.Rejected):
        binding(data)


def test_exact_manifest_hash_and_duplicate_keys_are_enforced(tmp_path):
    value = binding()
    path = tmp_path / "target.json"
    path.write_bytes(value.raw)
    assert subject.load_target_binding(path, value.sha256) == value
    path.write_bytes(value.raw + b"\n")
    with pytest.raises(subject.Rejected, match="target_manifest_hash"):
        subject.load_target_binding(path, value.sha256)
    duplicate = value.raw.replace(b'"schema":', b'"schema":"other", "schema":', 1)
    with pytest.raises(subject.Rejected):
        subject.TargetBinding(duplicate, subject.digest(duplicate))
    with pytest.raises(subject.Rejected, match="target_manifest_size"):
        subject.TargetBinding(b" " * 16385, "0" * 64)


@pytest.mark.parametrize("address", ["192.0.2.10", "192.0.2.10/32", "2001:db8::10/128"])
def test_connected_address_must_be_one_exact_operator_bound_address(address):
    subject.validate_connected_target({"db": "qualified_fixture", "port": 5432, "address": address}, binding())


@pytest.mark.parametrize("patch", [
    {"address": "192.0.2.11/32"}, {"address": None}, {"address": "192.0.2.10.evil"},
    {"db": "wrong_database"}, {"port": 5547}, {"port": "5432"},
])
def test_connected_identity_mismatch_is_not_inferred_from_connection_settings(patch):
    with pytest.raises(subject.Rejected, match="connected_target_differs"):
        subject.validate_connected_target({"db": "qualified_fixture", "port": 5432,
                                           "address": "192.0.2.10/32"} | patch, binding())


def cli_setup(tmp_path, monkeypatch):
    target = binding()
    path = tmp_path / "target.json"
    path.write_bytes(target.raw)
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(subject, "load_projection", lambda *_: {"rows": [], "counts": {"references": 300}})
    calls = []
    driver = ModuleType("psycopg")
    driver.connect = lambda **kwargs: calls.append(kwargs)
    conninfo = ModuleType("psycopg.conninfo")
    conninfo.conninfo_to_dict = lambda _: parameters() | {"password": "synthetic-never-display"}
    rows = ModuleType("psycopg.rows")
    rows.dict_row = object()
    for name, module in [("psycopg", driver), ("psycopg.conninfo", conninfo), ("psycopg.rows", rows)]:
        monkeypatch.setitem(sys.modules, name, module)
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGLOADBALANCEHOSTS", "PGTARGETSESSIONATTRS"):
        monkeypatch.delenv(key, raising=False)
    args = [str(tmp_path), "--preflight", str(tmp_path / "preflight.json"), "--output", str(output),
            "--target-manifest", str(path), "--target-sha256", target.sha256]
    return args, output, calls, path


def test_offline_cli_with_bound_target_never_connects(tmp_path, monkeypatch):
    args, output, calls, _ = cli_setup(tmp_path, monkeypatch)
    assert subject.main(args) == 0
    report = json.loads(output.read_text())
    assert calls == [] and report["connected_target_verified"] is False
    assert report["target_binding"] == subject.target_receipt(binding())


def test_target_hash_mismatch_fails_before_connection_and_does_not_leak_inputs(tmp_path, monkeypatch, capsys):
    args, output, calls, path = cli_setup(tmp_path, monkeypatch)
    path.write_bytes(path.read_bytes() + b"\n")
    assert subject.main(args + ["--apply"]) == 2
    report = json.loads(output.read_text())
    assert report["reason"] == "target_manifest_hash" and calls == []
    assert report["target_binding"]["mode"] == "operator_binding_unvalidated"
    assert "synthetic-never-display" not in capsys.readouterr().out + output.read_text()


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGLOADBALANCEHOSTS", "PGTARGETSESSIONATTRS"])
def test_ambient_connection_override_is_rejected_before_connection(tmp_path, monkeypatch, name):
    args, output, calls, _ = cli_setup(tmp_path, monkeypatch)
    monkeypatch.setenv(name, "synthetic-not-a-target")
    assert subject.main(args + ["--apply"]) == 2
    assert json.loads(output.read_text())["reason"] == "ambient_connection_override" and calls == []


@pytest.fixture(scope="module")
def actual_projection():
    package = captured.local_input("FUNGIP_CAPTURED_PACKAGE", directory=True)
    proof = captured.local_input("FUNGIP_GENETICS_PREFLIGHT")
    catalog = subject.read_bound_json(package / "data/catalog.json", subject.CATALOG_SHA)
    preflight = subject.read_bound_json(proof, subject.PREFLIGHT_SHA)
    return catalog, preflight, subject.load_projection(package, proof)


def portable_connection(actual_projection):
    catalog, preflight, _ = actual_projection
    conn = captured.Connection(catalog, preflight)
    conn.info = SimpleNamespace(host="database.example.invalid", port=5432, dbname="qualified_fixture",
                                get_parameters=lambda: {"sslmode": "verify-full"})
    conn.pgconn = SimpleNamespace(ssl_in_use=True)
    conn.target = {"db": "qualified_fixture", "port": 5432, "address": "192.0.2.10/32"}
    return conn


def test_portable_actual_projection_preserves_300_references_nullable_links_and_replay(actual_projection):
    conn = portable_connection(actual_projection)
    result = subject.import_projection(conn, actual_projection[2], target_binding=binding())
    assert (result["inserted"], result["unchanged"], result["updated"]) == (300, 0, 0)
    assert sum(r["taxon_id"] is None for r in conn.existing.values()) == 54
    assert result["connected_target_verified"] is True and result["target_binding"] == subject.target_receipt(binding())
    before = copy.deepcopy(conn.existing)
    replay = subject.import_projection(conn, actual_projection[2], target_binding=binding())
    assert (replay["inserted"], replay["unchanged"]) == (0, 300) and conn.existing == before


def test_portable_actual_projection_wrong_connected_identity_prevents_source_reads(actual_projection):
    conn = portable_connection(actual_projection)
    conn.target["address"] = "192.0.2.11/32"
    with pytest.raises(subject.Rejected, match="connected_target_differs"):
        subject.import_projection(conn, actual_projection[2], target_binding=binding())
    assert conn.insert_attempts == 0 and conn.rolled_back
    assert not any("FROM fungip.species" in sql for sql, _ in conn.statements)


@pytest.mark.parametrize("tls,mode", [(False, "verify-full"), (True, "require")])
def test_direct_import_cannot_bypass_portable_tls(actual_projection, tls, mode):
    conn = portable_connection(actual_projection)
    conn.pgconn.ssl_in_use = tls
    conn.info.get_parameters = lambda: {"sslmode": mode}
    with pytest.raises(subject.Rejected):
        subject.import_projection(conn, actual_projection[2], target_binding=binding())
    assert conn.statements == [] and conn.insert_attempts == 0


@pytest.mark.parametrize("fault", ["existing_conflict", "late_insert", "commit_ack"])
def test_portable_target_does_not_relax_conflict_rollback_or_unknown_commit(actual_projection, fault):
    conn = portable_connection(actual_projection)
    projection = actual_projection[2]
    if fault == "existing_conflict":
        row = copy.deepcopy(projection["rows"][0]); row["sequence"] = "ACGT"
        conn.existing[row["accession"]] = row
    elif fault == "late_insert":
        conn.fail_at = 9
    else:
        conn.commit_failure = True
    before = copy.deepcopy(conn.existing)
    expected = subject.CommitUnknown if fault == "commit_ack" else subject.Rejected if fault == "existing_conflict" else RuntimeError
    with pytest.raises(expected):
        subject.import_projection(conn, projection, target_binding=binding())
    if fault == "commit_ack":
        assert not conn.committed and not conn.rolled_back
    else:
        assert conn.existing == before and conn.rolled_back


@pytest.mark.parametrize("omit", ["--target-manifest", "--target-sha256"])
def test_cli_requires_both_target_arguments_before_connection(tmp_path, monkeypatch, omit):
    args, output, calls, _ = cli_setup(tmp_path, monkeypatch)
    index = args.index(omit)
    del args[index:index + 2]
    assert subject.main(args + ["--apply"]) == 2
    assert json.loads(output.read_text())["reason"] == "target_manifest_pair_required" and calls == []


def test_cli_explicit_apply_checks_target_and_keeps_connection_secrets_out_of_receipt(
    tmp_path, monkeypatch, capsys, actual_projection,
):
    args, output, calls, _ = cli_setup(tmp_path, monkeypatch)
    conn = portable_connection(actual_projection)
    monkeypatch.setattr(subject, "load_projection", lambda *_: actual_projection[2])

    @contextmanager
    def connect(**kwargs):
        calls.append(kwargs)
        yield conn

    sys.modules["psycopg"].connect = connect
    assert subject.main(args + ["--apply"]) == 0
    assert len(calls) == 1 and calls[0]["connect_timeout"] == 3
    report = json.loads(output.read_text())
    assert report["status"] == "committed" and report["inserted"] == 300
    assert report["target_binding"] == subject.target_receipt(binding())
    written = output.read_text() + capsys.readouterr().out
    assert "synthetic-never-display" not in written and "database.example.invalid" not in written


def test_driver_error_details_never_escape_receipt(tmp_path, monkeypatch, capsys):
    args, output, calls, _ = cli_setup(tmp_path, monkeypatch)

    def connect(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("synthetic-never-display")

    sys.modules["psycopg"].connect = connect
    assert subject.main(args + ["--apply"]) == 2
    assert len(calls) == 1 and json.loads(output.read_text())["reason"] == "operation_failed"
    assert "synthetic-never-display" not in output.read_text() + capsys.readouterr().out
