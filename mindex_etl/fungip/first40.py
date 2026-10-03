"""Input-authority-bound first-40 source adapter; no I/O, network or database calls.

An admitted operator supplies four exact byte buffers and an explicit source and
format declaration. The correction is additive; original observations remain
unchanged. There are no paths, fallback inputs, connections or command entrypoints.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from io import StringIO
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .catalog import canonical, digest
from .receipts import decode58

FIRST40_IDS = tuple(f"FG{i:03d}" for i in range(1,41))
VERIFICATION_BASIS = "user_supplied_and_attached_verification"
PARSER_VERSION = "fungip.first40.corrected.v1"
CORRECTION_SCHEMA = "direct_user_correction_v1"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
REVIEW_FLAGS = (
    "canonical_mindex_page_readback_unverified", "beneficiary_account_wallet_review_open",
    "fee_terms_transaction_review_open", "image_license_visual_review_open",
    "its_species_identification_review_open", "source_reported_not_new_chain_verification",
)
URL_FIELDS = (
    "solana_explorer_url", "solana_explorer_tx_url", "solscan_tx_url", "solscan_token_url",
    "usepaid_url", "usepaid_short_url", "pumpfun_url", "metadata_uri",
)
LAUNCH_FIELDS = ("mint_address", "launch_tx", "launched_at", "launched_at_pt", *URL_FIELDS,
                 "recipient", "token_program", "decimals", "supply_raw")
SCIENCE_FIELDS = ("species_id", "ticker", "accepted_name", "dna_sha256", "dna_accession_version",
                  "dna_database", "dna_source_url", "image_credit", "image_license", "image_sha256")
RECORD_FIELDS = set(SCIENCE_FIELDS + LAUNCH_FIELDS + (
    "launch_status", "source_hash_match", "canonical_approved", "candidate_launch",
    "superseded_mints", "synonyms", "catalog_review_flags", "gap_flags", "owner_entity",
    "verification_basis", "new_chain_verified",
))
COMMON_COLUMNS = {
    "species_id":"species_id", "ticker":"ticker", "launch_tx":"launch_tx",
    "launched_at_pt":"launched_at_pt", "usepaid_url":"usepaid_url", "pumpfun_url":"pumpfun_url",
    "metadata_uri":"metadata_uri", "solana_explorer_tx_url":"explorer_tx_url",
    "solscan_tx_url":"solscan_tx_url", "recipient":"recipient", "token_program":"token_program",
    "decimals":"decimals", "supply_raw":"supply_raw",
}
# Selecting this exact version is required; headers never select a version.
DIALECTS = {
    "launch_ledger_v1": {**COMMON_COLUMNS, "mint_address":"mint_address", "accepted_name":"name",
        "solana_explorer_url":"explorer_url", "usepaid_short_url":"usepaid_short_url",
        "solscan_token_url":"solscan_token_url"},
    "first40_launch_log_v1": {**COMMON_COLUMNS, "mint_address":"mint", "accepted_name":"accepted_name",
        "solana_explorer_url":"explorer_mint_url", "usepaid_short_url":"website_short_url",
        "solscan_token_url":"solscan_token_url"},
}


def pacific_to_utc(value: str) -> str:
    """Preserve caller's PT string; reject missing dates and ambiguous/gap wall time."""
    if not isinstance(value,str): raise ValueError("Explicit dated Pacific timestamp required")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?) (PDT|PST|PT)",value)
    if not match:
        raise ValueError("Explicit dated Pacific timestamp required")
    naive = datetime.strptime(match[1],"%Y-%m-%d %H:%M:%S" if len(match[1]) == 19 else "%Y-%m-%d %H:%M")
    try:
        zone = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Pacific timezone data unavailable; runtime admission prerequisite") from exc
    valid = {}
    for fold in (0,1):
        local = naive.replace(tzinfo=zone,fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) != naive:
            continue
        if match[2] != "PT" and local.tzname() != match[2]:
            continue
        valid[utc] = utc.isoformat().replace("+00:00","Z")
    if len(valid) != 1:
        raise ValueError("Ambiguous, nonexistent or incorrectly labeled Pacific time")
    return next(iter(valid.values()))


def nullable(value):
    if value is None or value == "" or isinstance(value,str) and not value.strip():
        return None
    if not isinstance(value,str):
        raise ValueError("CSV values must be strings")
    return value  # No URL/name/value normalization of nonblank source cells.


def csv_rows(raw: bytes, required: set[str]) -> list[dict]:
    if not isinstance(raw,bytes) or len(raw) > 4*1024*1024:
        raise ValueError("CSV byte bound exceeded")
    reader = csv.DictReader(StringIO(raw.decode("utf-8-sig",errors="strict"),newline=""))
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError("Missing/duplicate explicit-version CSV columns")
    rows = list(reader)
    if len(rows) > 350 or any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("Malformed or oversized CSV rows")
    return rows


def csv_bool(value: str | None) -> bool | None:
    if nullable(value) is None:
        return None
    if value not in ("True","False","true","false"):
        raise ValueError("Unsupported boolean cell")
    return value in ("True","true")


def excluded_mints(value: str | None, encoding: str) -> list[str]:
    if nullable(value) is None:
        return []
    if encoding == "json":
        values = json.loads(value)
        if not isinstance(values,list) or any(not isinstance(v,str) for v in values):
            raise ValueError("Declared JSON mint list required")
    elif encoding == "semicolon":
        values = value.split(";")
    else:
        raise ValueError("Explicit superseded encoding required")
    if len(values) != len(set(values)):
        raise ValueError("Duplicate excluded mint")
    for mint in values:
        decode58(mint,32)
    return values


def check_launch_fields(fields: dict, gap_flags: list[str]) -> None:
    if set(fields) != set(LAUNCH_FIELDS):
        raise ValueError("Unexpected launch payload fields")
    if fields["mint_address"] is not None: decode58(fields["mint_address"],32)
    if fields["launch_tx"] is not None: decode58(fields["launch_tx"],64)
    if (fields["launched_at"] is None) != (fields["launched_at_pt"] is None):
        raise ValueError("Timestamp pair incomplete")
    if fields["launched_at"] is not None and pacific_to_utc(fields["launched_at_pt"]) != fields["launched_at"]:
        raise ValueError("Pacific/UTC mismatch")
    for key in URL_FIELDS:
        value = fields[key]
        if value is None:
            if "blank_"+key not in gap_flags: raise ValueError("Blank URL missing gap flag")
            continue
        if not isinstance(value,str) or value != value.strip() or len(value) > 2048:
            raise ValueError("Invalid exact URL cell")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Unsupported public source URL")
        forms = {
            "solana_explorer_url":("explorer.solana.com","/address/",fields["mint_address"]),
            "solana_explorer_tx_url":("explorer.solana.com","/tx/",fields["launch_tx"]),
            "solscan_tx_url":("solscan.io","/tx/",fields["launch_tx"]),
            "solscan_token_url":("solscan.io","/token/",fields["mint_address"]),
            "usepaid_url":("usepaid.app","/token/",fields["mint_address"]),
            "pumpfun_url":("pump.fun","/coin/",fields["mint_address"]),
        }
        if key in forms:
            host,prefix,identifier = forms[key]
            if identifier is None or parsed.netloc != host or parsed.path != prefix+identifier or parsed.query or parsed.fragment:
                raise ValueError("Supplied URL/identifier disagreement")
        if key == "usepaid_short_url" and (parsed.netloc != "usepaid.app" or not re.fullmatch(r"/t/[A-Za-z0-9]+",parsed.path) or parsed.query or parsed.fragment):
            raise ValueError("Unsupported supplied UsePaid short URL")
    if fields["recipient"] not in (None,"@nodefather"):
        raise ValueError("Recipient disagreement")
    if fields["token_program"] not in (None,TOKEN_2022):
        raise ValueError("Token program disagreement")
    if fields["decimals"] is not None and (type(fields["decimals"]) is not int or fields["decimals"] != 6):
        raise ValueError("Decimals disagreement")
    if fields["supply_raw"] not in (None,"1000000000000000"):
        raise ValueError("Exact raw supply disagreement")


def handoff_match(pattern: str, body: str, label: str):
    if len(re.findall(r"^- "+re.escape(label)+r":",body,re.M)) > 1:
        raise ValueError("Repeated critical handoff label")
    matches = list(re.finditer(pattern,body,re.M))
    if len(matches) > 1: raise ValueError("Ambiguous handoff value")
    return matches[0] if matches else None


def handoff_records(raw: bytes) -> tuple[dict,dict]:
    """Parse the declared Markdown handoff grammar, never a fallback launch ledger."""
    if not isinstance(raw,bytes) or len(raw) > 1024*1024:
        raise ValueError("Handoff byte bound exceeded")
    text = raw.decode("utf-8-sig",errors="strict")
    sections = re.findall(r"^### (FG\d{3}) · ([^·\r\n]+) · \*([^*\r\n]+)\*\r?\n(.*?)(?=^### |^## |\Z)",text,re.M|re.S)
    records = {}
    for sid,ticker,name,body in sections:
        if sid in records or sid not in FIRST40_IDS: raise ValueError("Duplicate/out-of-scope handoff ID")
        dna = handoff_match(r"^- DNA accession: `([^`]+)`; dna_sha256: `([0-9a-f]{64})`",body,"DNA accession")
        if not dna: raise ValueError("Missing handoff DNA identity")
        item = {"species_id":sid,"ticker":ticker.strip(),"accepted_name":name,"dna_accession_version":dna[1],"dna_sha256":dna[2]}
        if sid == "FG026":
            if "**NO VALID MINT, needs_reissue=true**" not in body: raise ValueError("FG026 handoff disagreement")
            invalid = handoff_match(r"^- Live but invalid mint: `([^`]+)` \(launch tx `([^`]+)`, ([^)]+)\)",body,"Live but invalid mint")
            if not invalid: raise ValueError("FG026 excluded observation missing")
            item.update(record_status="live_but_invalid_needs_reissue",mint_address=invalid[1],launch_tx=invalid[2],launched_at_pt=invalid[3])
        else:
            if "**CANONICAL**, verification: VERIFIED" not in body: raise ValueError("Canonical handoff assertion missing")
            mint = handoff_match(r"^- Mint: `([^`]+)`",body,"Mint")
            tx = handoff_match(r"^- Launch tx: `([^`]+)` \(([^)]+)\)",body,"Launch tx")
            if not mint or not tx: raise ValueError("Handoff launch identity missing")
            item.update(record_status="canonical",mint_address=mint[1],launch_tx=tx[1],launched_at_pt=tx[2])
            labels = {"solana_explorer_tx_url":"Explorer tx","solscan_tx_url":"Solscan tx", "solana_explorer_url":"Explorer mint", "usepaid_url":"UsePaid", "pumpfun_url":"pump.fun", "metadata_uri":"Metadata URI"}
            for key,label in labels.items():
                found = handoff_match(r"^- "+re.escape(label)+r": (https://[^\s()]+)",body,label)
                item[key] = found[1] if found else None
            if body.count("(short link:") > 1: raise ValueError("Repeated critical handoff short link")
            short = re.search(r"\(short link: (https://[^\s)]+)\)",body)
            item["usepaid_short_url"] = short[1] if short else None
        records[sid] = item
    if set(records) != set(FIRST40_IDS): raise ValueError("Exactly forty handoff sections required")
    excluded = {}
    table = text.split("## Superseded / do-not-use mints",1)
    if len(table) != 2: raise ValueError("Explicit do-not-use table required")
    for line in table[1].splitlines():
        cells = [v.strip().strip("`") for v in line.split("|")[1:-1]]
        if cells and re.fullmatch(r"FG\d{3}",cells[0]) and cells[0] not in FIRST40_IDS:
            raise ValueError("Out-of-scope handoff excluded ID")
        if cells and cells[0] in FIRST40_IDS:
            if len(cells) != 6: raise ValueError("Unsupported do-not-use row")
            sid,ticker,status,mint,tx,_ = cells
            decode58(mint,32); decode58(tx,64)
            if mint in excluded: raise ValueError("Duplicate handoff excluded mint")
            excluded[mint] = {"species_id":sid,"ticker":ticker,"record_status":status,"launch_tx":tx}
    return records,excluded


def source_authority(authority: dict, bindings: dict) -> None:
    if not isinstance(authority,dict) or authority.get("approved") is not True or not isinstance(authority.get("approval_reference"),str) or not authority["approval_reference"].strip():
        raise ValueError("INPUT_AUTHORITY_HOLD: explicit source designation required")
    if set(authority) != {"approved","approval_reference","source_bindings","launch_schema","superseded_encoding","correction_schema"}:
        raise ValueError("Unexpected authority fields")
    if authority.get("source_bindings") != bindings or authority.get("launch_schema") != "first40_launch_log_v1" or authority.get("correction_schema") != CORRECTION_SCHEMA:
        raise ValueError("Approval must bind these exact inputs and explicit launch dialect")
    if authority.get("superseded_encoding") not in ("json","semicolon"):
        raise ValueError("Explicit superseded encoding required")
    if not isinstance(bindings,dict) or set(bindings) != {"catalog_sha256","launch_sha256","handoff_sha256","correction_sha256"} or any(not isinstance(v,str) or not re.fullmatch(r"[0-9a-f]{64}",v) for v in bindings.values()):
        raise ValueError("Input SHA256 bindings invalid")


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError("Duplicate correction JSON key")
        result[key] = value
    return result


def utc_timestamp(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",value):
        raise ValueError("Explicit UTC timestamp required")
    datetime.strptime(value,"%Y-%m-%dT%H:%M:%SZ")
    return value


def validation_rows(records: list[dict]) -> list[dict]:
    keys = ("species_id","ticker","dna_sha256","image_sha256","mint_address","source_hash_match",
            "canonical_approved","launch_status","superseded_mints","catalog_review_flags","gap_flags")
    return [{**{key:r[key] for key in keys},"written":False,"new_chain_verified":False} for r in records]


def correction_records(raw: bytes, bindings: dict) -> tuple[dict,str]:
    if not isinstance(raw,bytes) or len(raw) > 64*1024: raise ValueError("Correction byte bound exceeded")
    overlay = json.loads(raw.decode("utf-8-sig",errors="strict"),object_pairs_hook=unique_object)
    if not isinstance(overlay,dict) or type(overlay.get("schema_version")) is not int or overlay["schema_version"] != 1 or overlay.get("record_type") != "DIRECT_USER_CORRECTION_AND_INPUT_SELECTION" or overlay.get("scope") != ["FG001","FG040"]:
        raise ValueError("Unsupported direct-user correction schema/scope")
    designated = overlay.get("source_designation")
    if not isinstance(designated,dict) or any(designated.get(source) != bindings[target] for source,target in
        (("catalog_sha256","catalog_sha256"),("launch_log_sha256","launch_sha256"),("handoff_sha256","handoff_sha256"))):
        raise ValueError("Correction source designation differs")
    semantics = overlay.get("verification_semantics")
    if (overlay.get("owner_entity") != "MycoDAO" or overlay.get("recipient") != "@nodefather" or
        not isinstance(semantics,dict) or semantics.get("requested_launch_status") != "verified" or
        semantics.get("independent_chain_verification_performed_by_this_chat") is not False or
        overlay.get("expected") != {"canonical_species_count":40,"superseded_mint_count":5,"FG041_FG300_launch_changes":0}):
        raise ValueError("Correction qualification/entity/scope differs")
    corrections = overlay.get("corrections")
    if not isinstance(corrections,list) or len(corrections) != 2 or any(not isinstance(c,dict) for c in corrections):
        raise ValueError("Exactly two explicit corrections required")
    if any(not isinstance(c.get("species_id"),str) for c in corrections): raise ValueError("Invalid correction species identity")
    by_id = {c.get("species_id"):c for c in corrections}
    if set(by_id) != {"FG021","FG026"}: raise ValueError("Correction species scope differs")
    common = {"species_id","ticker","accepted_name","mint_address","launch_tx","launched_at_pt","superseded_mints","launch_status"}
    allowed = {"FG021":common|{"canonical_pending_confirmation","url_rule"},
               "FG026":common|{"usepaid_url","pumpfun_url","solana_explorer_url","metadata_uri","on_chain_description","dna_sha256","dna_accession_version","decimals","supply_display","user_verified_authorities_revoked","solana_explorer_tx_url","solscan_tx_url","needs_reissue","forbidden_inheritance"}}
    for sid,c in by_id.items():
        if set(c) != allowed[sid] or c["launch_status"] != "verified": raise ValueError("Correction fields/status differ")
        decode58(c["mint_address"],32)
        if not isinstance(c["superseded_mints"],list) or len(c["superseded_mints"]) != 1: raise ValueError("Explicit superseded selection required")
        decode58(c["superseded_mints"][0],32)
    c = by_id["FG021"]
    decode58(c["launch_tx"],64); pacific_to_utc(c["launched_at_pt"])
    if c["canonical_pending_confirmation"] is not False: raise ValueError("FG021 selection still pending")
    c = by_id["FG026"]
    if (c["needs_reissue"] is not False or any(c[key] is not None for key in ("launch_tx","launched_at_pt","solana_explorer_tx_url","solscan_tx_url")) or
        type(c["decimals"]) is not int or c["decimals"] != 6 or type(c["supply_display"]) is not int or c["supply_display"] != 1000000000 or
        c["user_verified_authorities_revoked"] != ["mint","freeze","update"] or not isinstance(c["dna_sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",c["dna_sha256"]) or c["on_chain_description"] != "SHA-256 "+c["dna_sha256"]):
        raise ValueError("FG026 supplied correction differs")
    return by_id,utc_timestamp(overlay.get("recorded_at_utc"))


def build_first40_snapshot(catalog_raw: bytes, launch_raw: bytes, handoff_raw: bytes,
                           correction_raw: bytes, authority: dict, source_reported_as_of_pt: str) -> dict:
    bindings = {"catalog_sha256":digest(catalog_raw),"launch_sha256":digest(launch_raw),"handoff_sha256":digest(handoff_raw),"correction_sha256":digest(correction_raw)}
    source_authority(authority,bindings)  # Must pass BEFORE parsing launch data.
    corrections,correction_time = correction_records(correction_raw,bindings)
    as_of = pacific_to_utc(source_reported_as_of_pt)
    catalog = csv_rows(catalog_raw,set(SCIENCE_FIELDS)|{"requested_name","common_name"})
    by_id = {}
    for row in catalog:
        if not re.fullmatch(r"FG(?:00[1-9]|0[1-9][0-9]|[12][0-9]{2}|300)",row["species_id"]) or row["species_id"] in by_id:
            raise ValueError("Duplicate/invalid catalog species key")
        by_id[row["species_id"]] = row
    if set(by_id) != {f"FG{i:03d}" for i in range(1,301)}: raise ValueError("Exactly 300 catalog identities required")
    dialect = authority["launch_schema"]; mapping = DIALECTS[dialect]
    required = {"record_status","needs_reissue","hash_match","superseded_mints",*mapping.values()}
    if dialect == "launch_ledger_v1": required -= {"usepaid_short_url","solscan_token_url","token_program","decimals","supply_raw"}
    else: required |= {"dna_accession","catalog_dna_sha256","catalog_image_sha256","description_hash","onchain_name","onchain_symbol","onchain_description","name_match","symbol_match"}
    rows = csv_rows(launch_raw,required)
    handoff,do_not_use = handoff_records(handoff_raw)
    selected,excluded,disagreements = {},{},[]
    observed_mints,observed_transactions = set(),set()
    for row in rows:
        sid = row["species_id"]
        if sid not in FIRST40_IDS: raise ValueError("Launch ID outside FG001–FG040")
        science = by_id[sid]
        if row["ticker"] != science["ticker"] or row[mapping["accepted_name"]] != science["accepted_name"]:
            disagreements.append({"species_id":sid,"reason":"catalog_identity_disagreement"})
        mint = nullable(row.get(mapping["mint_address"]))
        if mint is None: raise ValueError("Observed launch mint missing")
        decode58(mint,32)
        tx = nullable(row.get("launch_tx"))
        if tx is None: raise ValueError("Observed launch transaction missing")
        decode58(tx,64)
        if mint in observed_mints or tx in observed_transactions: raise ValueError("Duplicate observed mint/transaction")
        observed_mints.add(mint); observed_transactions.add(tx)
        if row["record_status"] == "canonical":
            if sid in selected: raise ValueError("Duplicate canonical species")
            selected[sid] = row
        elif row["record_status"] in {"superseded_bad_hash","superseded_duplicate","live_but_invalid_needs_reissue"}:
            if mint in excluded: raise ValueError("Duplicate noncanonical mint")
            excluded[mint] = row
        else: raise ValueError("Unsupported launch record status; old NOT_ISSUED templates are not authority")
        if dialect == "first40_launch_log_v1":
            # The designated log literally contains " /  / "; never rewrite cells.
            extracted = re.fullmatch(r"SHA-256 ([0-9a-f]{1,64})(?: / | /  / )Fees to @nodefather via UsePaid",row["onchain_description"])
            matched = row["description_hash"] == science["dna_sha256"]
            if (row["dna_accession"] != science["dna_accession_version"] or row["catalog_dna_sha256"] != science["dna_sha256"] or
                row["catalog_image_sha256"] != science["image_sha256"] or row["onchain_name"] != science["accepted_name"] or
                row["onchain_symbol"] != science["ticker"] or not extracted or extracted[1] != row["description_hash"] or
                csv_bool(row["hash_match"]) is not matched or csv_bool(row["name_match"]) is not True or csv_bool(row["symbol_match"]) is not True):
                disagreements.append({"species_id":sid,"reason":"reported_science_hash_assertion_disagreement"})
    for mint,row in excluded.items():
        expected = do_not_use.get(mint)
        if expected is None or any(row[key] != expected[key] for key in ("species_id","ticker","record_status","launch_tx")):
            disagreements.append({"species_id":row["species_id"],"reason":"handoff_excluded_disagreement"})
    if set(excluded) != set(do_not_use): disagreements.append({"reason":"excluded_coverage_disagreement"})
    if len(rows) != 44 or len(selected) != 39 or len(excluded) != 5 or set(selected) != set(FIRST40_IDS)-{"FG026"}:
        disagreements.append({"reason":"original_observation_coverage_disagreement"})
    records = []
    for sid in FIRST40_IDS:
        science = by_id[sid]; h = handoff[sid]; row = selected.get(sid)
        flags = list(REVIEW_FLAGS)
        raw_flags = nullable(science.get("launch_blockers"))
        if raw_flags:
            try: source_flags = json.loads(raw_flags)
            except json.JSONDecodeError: source_flags = [raw_flags]
            if not isinstance(source_flags,list) or any(not isinstance(f,str) for f in source_flags): raise ValueError("Catalog review flags unsupported")
            flags.extend(source_flags)
        gaps = []; fields = dict.fromkeys(LAUNCH_FIELDS)
        own_excluded = sorted(mint for mint,r in excluded.items() if r["species_id"] == sid)
        item = {key:science[key] for key in SCIENCE_FIELDS}
        item.update(fields, owner_entity="MycoDAO",verification_basis=VERIFICATION_BASIS,new_chain_verified=False,
                    canonical_approved=False,candidate_launch=None,superseded_mints=own_excluded,synonyms=[],
                    source_hash_match=None,launch_status="gap",catalog_review_flags=sorted(set(flags)),gap_flags=gaps)
        for key in ("ticker","accepted_name","dna_accession_version","dna_sha256"):
            if h[key] != science[key]: disagreements.append({"species_id":sid,"reason":"handoff_catalog_"+key})
        if sid == "FG026":
            invalid_rows = [r for r in excluded.values() if r["species_id"] == sid and r["record_status"] == "live_but_invalid_needs_reissue"]
            if row is not None or len(invalid_rows) != 1:
                disagreements.append({"species_id":sid,"reason":"FG026_needs_reissue_disagreement"})
            elif any(h[key] != invalid_rows[0][mapping[key]] for key in ("mint_address","launch_tx","launched_at_pt")):
                disagreements.append({"species_id":sid,"reason":"FG026_original_handoff_observation_disagreement"})
            c = corrections[sid]
            if any(c[key] != science[key] for key in ("ticker","accepted_name","dna_sha256","dna_accession_version")) or c["superseded_mints"] != own_excluded:
                disagreements.append({"species_id":sid,"reason":"FG026_correction_science_or_superseded_disagreement"})
            # Begin from NULL, not the rejected mint's row; apply only supplied values.
            fields = dict.fromkeys(LAUNCH_FIELDS)
            for key in ("mint_address","usepaid_url","pumpfun_url","solana_explorer_url","metadata_uri","decimals"):
                fields[key] = c[key]
            fields.update(recipient="@nodefather",supply_raw=str(c["supply_display"] * 10 ** c["decimals"]))
            gaps.extend("blank_"+key for key in LAUNCH_FIELDS if fields[key] is None)
            check_launch_fields(fields,gaps)
            item.update(fields,launch_status="verified",canonical_approved=True,source_hash_match=True)
        elif row is None:
            disagreements.append({"species_id":sid,"reason":"missing_canonical_source_row"}); gaps.append("missing_canonical_source_row")
        else:
            fields = {key:nullable(row.get(mapping[key])) for key in LAUNCH_FIELDS if key != "launched_at"}
            fields["launched_at"] = pacific_to_utc(fields["launched_at_pt"]) if fields["launched_at_pt"] else None
            if fields["decimals"] is not None:
                if not re.fullmatch(r"[0-9]+",fields["decimals"]): raise ValueError("Invalid decimals cell")
                fields["decimals"] = int(fields["decimals"])
            gaps.extend("blank_"+key for key in LAUNCH_FIELDS if fields[key] is None)
            check_launch_fields(fields,gaps)
            for key in ("mint_address","launch_tx","launched_at_pt",*URL_FIELDS):
                if key in h and h[key] != fields[key]: disagreements.append({"species_id":sid,"reason":"handoff_launch_"+key})
            if excluded_mints(row["superseded_mints"],authority["superseded_encoding"]) != own_excluded:
                disagreements.append({"species_id":sid,"reason":"superseded_list_disagreement"})
            good = csv_bool(row["hash_match"]) is True and csv_bool(row["needs_reissue"]) is False
            item["source_hash_match"] = csv_bool(row["hash_match"])
            if not good or not all(fields[k] is not None for k in ("mint_address","launch_tx","launched_at")):
                gaps.append("canonical_source_hash_or_identity_unqualified")
                disagreements.append({"species_id":sid,"reason":"canonical_source_hash_or_identity_unqualified"})
            else:
                if sid == "FG021":
                    c = corrections[sid]
                    if (any(c[key] != item[key] for key in ("ticker","accepted_name")) or
                        any(c[key] != fields[key] for key in ("mint_address","launch_tx")) or
                        pacific_to_utc(c["launched_at_pt"]) != fields["launched_at"] or c["superseded_mints"] != own_excluded):
                        disagreements.append({"species_id":sid,"reason":"FG021_correction_selected_identity_disagreement"})
                    fields["launched_at_pt"] = c["launched_at_pt"]
                item.update(fields,launch_status="verified",canonical_approved=True)
        if sid == "FG034":
            if science["accepted_name"] != "Ganoderma sichuanense" or science["requested_name"] != "Ganoderma lingzhi":
                disagreements.append({"species_id":sid,"reason":"FG034_concept_disagreement"})
            item["synonyms"] = ["Ganoderma lingzhi"]
            item["catalog_review_flags"].append("lingzhi_sichuanense_taxonomic_concept_review_open")
        if sid == "FG033" and science["accepted_name"] != "Ganoderma lucidum": disagreements.append({"species_id":sid,"reason":"FG033_separate_concept_disagreement"})
        item["gap_flags"] = sorted(set(gaps)|{"blank_"+key for key in LAUNCH_FIELDS if item[key] is None})
        records.append(item)
    snapshot = {"schema_version":1,"parser_version":PARSER_VERSION,"authority":authority,"source_bindings":bindings,
                "corrected_input_version":CORRECTION_SCHEMA,"correction_recorded_at_utc":correction_time,
                "source_reported_as_of_pt":source_reported_as_of_pt,"source_reported_as_of_utc":as_of,
                "records":records,"disagreements":disagreements,"admissible":not disagreements,
                "validation_report":validation_rows(records)}
    snapshot["snapshot_sha256"] = digest(canonical(snapshot).encode("utf-8"))
    if snapshot["admissible"]: validate_admitted_snapshot(snapshot)
    return snapshot


def validate_admitted_snapshot(snapshot: dict) -> dict:
    """Detach and revalidate prepared source input before any Cursor-owned apply."""
    envelope_fields = {"schema_version","parser_version","authority","source_bindings","corrected_input_version","correction_recorded_at_utc","source_reported_as_of_pt","source_reported_as_of_utc","records","disagreements","admissible","validation_report","snapshot_sha256"}
    if not isinstance(snapshot,dict) or set(snapshot) != envelope_fields or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1 or snapshot.get("admissible") is not True or snapshot.get("disagreements") != []:
        raise ValueError("Snapshot disagreement/admission hold")
    detached = json.loads(canonical(snapshot))
    observed = detached.pop("snapshot_sha256",None)
    if not isinstance(observed,str) or observed != digest(canonical(detached).encode("utf-8")):
        raise ValueError("Snapshot byte binding differs")
    detached["snapshot_sha256"] = observed
    source_authority(detached.get("authority"),detached.get("source_bindings"))
    if detached.get("parser_version") != PARSER_VERSION or detached.get("corrected_input_version") != CORRECTION_SCHEMA:
        raise ValueError("Corrected parser/input version differs")
    utc_timestamp(detached.get("correction_recorded_at_utc"))
    if pacific_to_utc(detached["source_reported_as_of_pt"]) != detached["source_reported_as_of_utc"]:
        raise ValueError("Source observation timezone differs")
    records = detached.get("records")
    if not isinstance(records,list) or len(records) != 40 or any(not isinstance(r,dict) or not isinstance(r.get("species_id"),str) for r in records) or {r["species_id"] for r in records} != set(FIRST40_IDS):
        raise ValueError("Exactly forty permanent IDs required")
    selected = set(); excluded = set()
    for r in records:
        if not isinstance(r,dict) or set(r) != RECORD_FIELDS: raise ValueError("Record allowlist differs")
        if any(not isinstance(r[k],str) or not r[k].strip() for k in SCIENCE_FIELDS): raise ValueError("Scientific cross-check values missing")
        if any(not re.fullmatch(r"[0-9a-f]{64}",r[k]) for k in ("dna_sha256","image_sha256")): raise ValueError("Scientific hash invalid")
        if r["owner_entity"] != "MycoDAO" or r["verification_basis"] != VERIFICATION_BASIS or r["new_chain_verified"] is not False or type(r["canonical_approved"]) is not bool:
            raise ValueError("Source-only entity/qualification differs")
        if r["source_hash_match"] is not None and type(r["source_hash_match"]) is not bool: raise ValueError("Source hash result invalid")
        for key in ("catalog_review_flags","gap_flags","synonyms","superseded_mints"):
            if not isinstance(r[key],list) or any(not isinstance(v,str) for v in r[key]) or len(set(r[key])) != len(r[key]): raise ValueError("Invalid unique string list")
        if not set(REVIEW_FLAGS).issubset(r["catalog_review_flags"]): raise ValueError("Catalog reviews silently cleared")
        if r["synonyms"] != (["Ganoderma lingzhi"] if r["species_id"] == "FG034" else []): raise ValueError("FG034 synonym scope differs")
        if r["species_id"] == "FG033" and r["accepted_name"] != "Ganoderma lucidum": raise ValueError("FG033 separate concept differs")
        if r["species_id"] == "FG034" and (r["accepted_name"] != "Ganoderma sichuanense" or "lingzhi_sichuanense_taxonomic_concept_review_open" not in r["catalog_review_flags"]): raise ValueError("FG034 concept/review differs")
        fields = {key:r[key] for key in LAUNCH_FIELDS}; check_launch_fields(fields,r["gap_flags"])
        if r["launch_status"] != "verified" or r["canonical_approved"] is not True or r["source_hash_match"] is not True or r["candidate_launch"] is not None or not fields["mint_address"] or fields["recipient"] != "@nodefather":
            raise ValueError("Corrected all-forty source approval differs")
        if r["species_id"] == "FG026":
            unsupplied = ("launch_tx","launched_at","launched_at_pt","solana_explorer_tx_url","solscan_tx_url","solscan_token_url","usepaid_short_url","token_program")
            if (any(fields[k] is not None or "blank_"+k not in r["gap_flags"] for k in unsupplied) or
                any(fields[k] is None for k in ("usepaid_url","pumpfun_url","solana_explorer_url","metadata_uri")) or
                fields["decimals"] != 6 or fields["supply_raw"] != "1000000000000000"):
                raise ValueError("FG026 inherited or missing correction fields")
        elif not all(fields[k] for k in ("launch_tx","launched_at","launched_at_pt")):
            raise ValueError("Original canonical observation incomplete")
        for key in LAUNCH_FIELDS:
            if fields[key] is None and "blank_"+key not in r["gap_flags"]: raise ValueError("Missing launch field flag")
        identity = fields
        for key in ("mint_address","launch_tx"):
            value = identity[key]
            if value:
                if (key,value) in selected: raise ValueError("Duplicate selected mint/transaction")
                selected.add((key,value))
        for mint in r["superseded_mints"]:
            decode58(mint,32)
            if mint in excluded: raise ValueError("Excluded mint assigned to multiple species")
            excluded.add(mint)
    if any(("mint_address",mint) in selected for mint in excluded): raise ValueError("Excluded mint selected as canonical/candidate")
    if len(excluded) != 5: raise ValueError("Exactly five preserved superseded mints required")
    if canonical(detached.get("validation_report")) != canonical(validation_rows(records)): raise ValueError("Per-ID report differs from selected records/types")
    return detached
