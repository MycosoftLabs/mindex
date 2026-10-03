"""Durable operator receipt ledger. Never launches or signs transactions."""
from uuid import UUID
from urllib.parse import urlparse
from datetime import datetime, timezone
from .catalog import canonical, digest
from .metadata import bind_metadata
from .receipts import RPC, decode58, read_receipt
from .detail import detail

TRANSITIONS = {"draft":{"prepared","failed"}, "prepared":{"submitted-unknown","failed"},
               "submitted-unknown":{"confirmed","failed"},"confirmed":set(),"failed":set()}


def check_transition(previous: str, status: str, evidence: dict, signature: str | None) -> None:
    if status not in TRANSITIONS.get(previous,set()):
        raise ValueError("Invalid ledger transition")
    if previous == "submitted-unknown":
        if (not signature or evidence.get("transaction_signature") != signature or
            "transaction_error" not in evidence or
            evidence.get("confirmation_status") != "finalized" or
            evidence.get("verifier") != "fungip-solana-metaplex-v1"):
            raise ValueError("Unknown submission must be reconciled against its exact finalized receipt")
        if (status == "confirmed") != (evidence.get("transaction_error") is None):
            raise ValueError("Receipt result does not support requested state")
        if status == "confirmed" and (not evidence.get("species_binding") or not evidence.get("prepared_payload_sha256")):
            raise ValueError("Species/metadata preparation binding is absent")


def begin_attempt(conn, species_id: str, network: str) -> str:
    if network not in RPC:
        raise ValueError("Unsupported network")
    with conn.transaction():
        # Partial unique index independently enforces single active attempt under concurrency.
        result = conn.execute("""INSERT INTO fungip.token_attempt(species_id,network,status)
                                 VALUES (%s,%s,'draft') RETURNING attempt_id""",(species_id,network)).fetchone()
        attempt_id = str(result["attempt_id"])
        conn.execute("INSERT INTO fungip.token_event(attempt_id,status,evidence) VALUES (%s,'draft','{}'::jsonb)", (attempt_id,))
    return attempt_id


def prepare(conn, attempt_id: str, reviewed_payload: dict):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock_shared(hashtext('fungip.catalog.import'))")
        _prepare(conn, attempt_id, reviewed_payload)


def _prepare(conn, attempt_id: str, reviewed_payload: dict):
    if reviewed_payload.get("release_blockers"):
        raise ValueError("Draft still has release blockers")
    review = reviewed_payload.get("human_review") or {}
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise ValueError("Human-reviewed preparation required")
    if not all(review.get(k) is True for k in ("scientific_identity_checked","image_rights_checked",
               "ticker_checked","current_terms_checked","beneficiary_checked")):
        raise ValueError("Preparation qualifications incomplete")
    recipient = reviewed_payload.get("verified_recipient") or {}
    if recipient.get("handle") != "nodefather" or not recipient.get("x_account_id") or not recipient.get("verified_at"):
        raise ValueError("Verified recipient missing")
    fee_length = review.get("description_fee_text_length")
    if not isinstance(fee_length,int) or fee_length < 0 or len(reviewed_payload.get("description", "")) + fee_length > 256:
        raise ValueError("Visible combined description length unqualified")
    if reviewed_payload.get("initial_buy") != 0:
        raise ValueError("Assisted preparation keeps initial buy at zero")
    row = conn.execute("""SELECT s.*,t.network AS attempt_network FROM fungip.species s JOIN fungip.token_attempt t ON t.species_id=s.species_id
                          WHERE t.attempt_id=%s""",(str(UUID(attempt_id)),)).fetchone()
    if not row or reviewed_payload.get("species_id") != row["species_id"] or row["validation_errors"] or reviewed_payload.get("record_sha256") != row["record_sha256"]:
        raise ValueError("Species payload identity or validation failure")
    verification = conn.execute("SELECT * FROM fungip.page_verification WHERE species_id=%s",(row["species_id"],)).fetchone()
    view = detail(row,verification)
    if not view["live_verified"] or reviewed_payload.get("canonical_species_url") != view["canonical_url"]:
        raise ValueError("Exact current canonical page not verified")
    image = row["record"].get("image") or {}
    if not image or reviewed_payload.get("image_sha256") != image.get("sha256") or reviewed_payload.get("attribution") != image.get("attribution"):
        raise ValueError("Image digest or exact attribution mismatch")
    if reviewed_payload.get("network") != row["attempt_network"] or reviewed_payload.get("name") != row["accepted_name"]:
        raise ValueError("Prepared network/scientific name mismatch")
    decode58(reviewed_payload.get("operator_public_wallet", ""),32)
    prepared = dict(reviewed_payload)
    prepared["prepared_unix_time"] = int(datetime.now(timezone.utc).timestamp())
    prepared["metadata_binding"] = bind_metadata(prepared)
    _transition(conn,attempt_id,"prepared",prepared)


def cancel_pre_submission(conn, attempt_id: str, reason: str):
    if not reason.strip():
        raise ValueError("Cancellation reason required")
    _transition(conn,attempt_id,"failed",{"operator_cancelled_before_submission":True,"reason":reason})


def record_operator_submission(conn, attempt_id: str, signature: str, mint: str | None = None):
    decode58(signature,64)
    if mint:
        decode58(mint,32)
    _transition(conn,attempt_id,"submitted-unknown",{"operator_reported":True},signature,mint)


def reconcile(conn, attempt_id: str, mint: str, metadata_address: str):
    row = conn.execute("SELECT * FROM fungip.token_attempt WHERE attempt_id=%s",(str(UUID(attempt_id)),)).fetchone()
    if not row or row["status"] != "submitted-unknown":
        raise ValueError("Only an unknown existing submission can be reconciled")
    if row["mint_address"] and row["mint_address"] != mint:
        raise ValueError("Mint differs from operator-recorded submission")
    prepared = row["prepared_payload"]
    if not prepared or digest(canonical(prepared).encode()) != row["prepared_payload_sha256"]:
        raise ValueError("Immutable prepared envelope unavailable")
    evidence = read_receipt(row["network"],mint,row["transaction_signature"],metadata_address,prepared)
    _transition(conn,attempt_id,"confirmed" if evidence["transaction_error"] is None else "failed",evidence,
                row["transaction_signature"],mint)
    return evidence


def record_usepaid_reference(conn, attempt_id: str, url: str, observation: dict):
    """Operator-reviewed venue identity, separate from chain finalization/fee entitlement."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "usepaid.app" or parsed.username:
        raise ValueError("Official UsePaid reference required")
    if not observation.get("reviewer") or not observation.get("reviewed_at"):
        raise ValueError("Venue observation reviewer required")
    with conn.transaction():
        row = conn.execute("SELECT * FROM fungip.token_attempt WHERE attempt_id=%s FOR UPDATE",(str(UUID(attempt_id)),)).fetchone()
        if not row or row["status"] != "confirmed" or observation.get("mint_address") != row["mint_address"] or observation.get("transaction_signature") != row["transaction_signature"]:
            raise ValueError("Venue observation must match independently confirmed mint and transaction")
        conn.execute("UPDATE fungip.token_attempt SET usepaid_url=%s,updated_at=now() WHERE attempt_id=%s",(url,attempt_id))
        conn.execute("INSERT INTO fungip.token_event(attempt_id,previous_status,status,evidence) VALUES (%s,'confirmed','confirmed',%s::jsonb)",
                     (attempt_id,canonical({"kind":"operator_reviewed_venue_reference","url":url,"observation":observation,"fee_entitlement":"unverified"})))


def _transition(conn, attempt_id: str, status: str, evidence: dict,
                signature: str | None = None, mint: str | None = None):
    with conn.transaction():
        row = conn.execute("SELECT * FROM fungip.token_attempt WHERE attempt_id=%s FOR UPDATE",(str(UUID(attempt_id)),)).fetchone()
        if not row:
            raise ValueError("Unknown attempt")
        check_transition(row["status"],status,evidence,row["transaction_signature"])
        if row["status"] == "submitted-unknown" and evidence.get("network") != row["network"]:
            raise ValueError("Receipt network mismatch")
        if status == "confirmed" and (evidence["prepared_payload_sha256"] != row["prepared_payload_sha256"] or evidence["species_binding"] != row["prepared_payload"]["metadata_binding"]):
            raise ValueError("Receipt does not belong to immutable prepared species")
        conn.execute("""UPDATE fungip.token_attempt SET status=%s,
          transaction_signature=COALESCE(transaction_signature,%s),mint_address=COALESCE(mint_address,%s),
          metadata_url=COALESCE(%s,metadata_url),transaction_url=COALESCE(%s,transaction_url),
          prepared_payload=CASE WHEN %s='prepared' THEN %s::jsonb ELSE prepared_payload END,
          prepared_payload_sha256=CASE WHEN %s='prepared' THEN %s ELSE prepared_payload_sha256 END,
          prepared_at=CASE WHEN %s='prepared' THEN now() ELSE prepared_at END,
          finalized_evidence=CASE WHEN %s IN ('confirmed','failed') AND %s='submitted-unknown' THEN %s::jsonb ELSE finalized_evidence END,
          updated_at=now() WHERE attempt_id=%s""",
          (status,signature,mint,evidence.get("metadata_url"),
           f'https://explorer.solana.com/tx/{signature}?cluster={row["network"].removeprefix("solana-")}' if signature else None,
           status,canonical(evidence),status,digest(canonical(evidence).encode()),status,
           status,row["status"],canonical(evidence),attempt_id))
        conn.execute("INSERT INTO fungip.token_event(attempt_id,previous_status,status,evidence) VALUES (%s,%s,%s,%s::jsonb)",
                     (attempt_id,row["status"],status,canonical(evidence)))
        if status == "prepared":
            recipient = evidence.get("verified_recipient")
            if recipient:
                if recipient.get("handle") != "nodefather" or not recipient.get("x_account_id") or not recipient.get("verified_at"):
                    raise ValueError("Verified recipient identity missing")
                wallet = recipient.get("public_wallet")
                if wallet:
                    decode58(wallet,32)
                    if not recipient.get("wallet_verification_evidence"):
                        raise ValueError("Wallet identity evidence missing")
                conn.execute("UPDATE fungip.token_attempt SET recipient_identity=%s::jsonb,recipient_public_wallet=%s WHERE attempt_id=%s",
                             (canonical(recipient),wallet,attempt_id))
