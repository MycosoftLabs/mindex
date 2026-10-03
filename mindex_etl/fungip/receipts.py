"""Read-only Solana receipt verification. No wallet access, signing or submission."""
from __future__ import annotations
import base64
import json
import struct
from urllib.request import Request, build_opener
from .catalog import canonical, digest
from .metadata import NoRedirect, read_uri_bytes, verify_binding

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
TOKEN_PROGRAMS = {"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"}
METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
RENT_SYSVAR = "SysvarRent111111111111111111111111111111111"
CREATION_DECODER = "metaplex-create-metadata-account-v3-borsh-353d01be"
RPC = {"solana-mainnet-beta":"https://api.mainnet-beta.solana.com",
       "solana-devnet":"https://api.devnet.solana.com", "solana-testnet":"https://api.testnet.solana.com"}


def decode58(value: str, size: int) -> bytes:
    if not isinstance(value,str) or not value or len(value) > size * 2:
        raise ValueError("Invalid bounded base58 identifier")
    n = 0
    for char in value:
        if char not in ALPHABET:
            raise ValueError("Invalid base58 identifier")
        n = n * 58 + ALPHABET.index(char)
    result = b"\0" * (len(value) - len(value.lstrip("1"))) + (n.to_bytes((n.bit_length()+7)//8, "big") if n else b"")
    if len(result) != size:
        raise ValueError("Incorrect identifier length")
    return result


def creation_metadata(data: str) -> dict:
    """Fully decode only CreateMetadataAccountV3 at the pinned official layout.

    Protocol sources and the derived 495-byte bound are in RECEIPT-DECODER.md.
    Transaction strings are exact bytes, unlike the puffed current account strings.
    """
    if not isinstance(data,str) or not data or len(data) > 680:
        raise ValueError("Invalid bounded creation instruction")
    n = 0
    for char in data:
        if char not in ALPHABET:
            raise ValueError("Invalid creation base58")
        n = n * 58 + ALPHABET.index(char)
    raw = b"\0" * (len(data)-len(data.lstrip("1"))) + (n.to_bytes((n.bit_length()+7)//8,"big") if n else b"")
    if len(raw) > 495:
        raise ValueError("Creation instruction exceeds supported bound")
    offset = 0

    def take(length):
        nonlocal offset
        if offset + length > len(raw):
            raise ValueError("Truncated creation instruction")
        value = raw[offset:offset+length]
        offset += length
        return value

    def number(fmt):
        return struct.unpack(fmt,take(struct.calcsize(fmt)))[0]

    def tag():
        value = number("<B")
        if value not in (0,1):
            raise ValueError("Invalid creation boolean/option tag")
        return value

    def string(limit):
        length = number("<I")
        if length > limit:
            raise ValueError("Creation string exceeds protocol bound")
        try:
            return take(length).decode("utf-8",errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid creation UTF-8") from exc

    if number("<B") != 33:
        raise ValueError("Unsupported metadata creation variant")
    name, symbol, uri = string(32), string(10), string(200)
    fee = number("<H")
    if fee > 10000:
        raise ValueError("Invalid creation seller fee")
    if tag():
        count = number("<I")
        if not 1 <= count <= 5:
            raise ValueError("Invalid bounded creator count")
        for _ in range(count):
            take(32); tag(); number("<B")
    if tag():
        tag(); take(32)
    if tag():
        if number("<B") not in (0,1,2):
            raise ValueError("Unsupported creation use method")
        number("<Q"); number("<Q")
    is_mutable = bool(tag())
    if tag():
        if number("<B") not in (0,1):
            raise ValueError("Unsupported creation collection details")
        take(8)
    if offset != len(raw):
        raise ValueError("Trailing creation instruction bytes")
    return {"name":name,"symbol":symbol,"uri":uri,"is_mutable":is_mutable,
            "instruction_sha256":digest(raw),"decoder":CREATION_DECODER}


def instruction_accounts(instruction: dict, keys: list[str]) -> list[str]:
    values = instruction.get("accounts")
    if not isinstance(values,list):
        raise ValueError("Metadata instruction accounts unavailable")
    accounts = []
    for value in values:
        if isinstance(value,int) and not isinstance(value,bool) and 0 <= value < len(keys):
            account = keys[value]
        elif isinstance(value,str):
            account = value
        else:
            raise ValueError("Invalid metadata account index")
        decode58(account,32)
        accounts.append(account)
    return accounts


def rpc(network: str, method: str, params: list):
    if method not in {"getSignatureStatuses", "getTransaction", "getAccountInfo"}:
        raise ValueError("Only receipt reads allowed")
    request = Request(RPC[network], data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),
                      headers={"Content-Type":"application/json"})
    with build_opener(NoRedirect()).open(request, timeout=25) as response:
        if response.geturl() != RPC[network]:
            raise ValueError("RPC redirect rejected")
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("RPC evidence exceeds body bound")
        data = json.loads(raw)
    if data.get("error"):
        raise ValueError("RPC receipt unavailable")
    return data["result"]


def metadata_identity(account: dict, mint: str) -> dict:
    if account.get("owner") != METADATA_PROGRAM or account.get("executable"):
        raise ValueError("Metadata owner mismatch")
    payload = account.get("data")
    if not isinstance(payload, list) or len(payload) != 2 or payload[1] != "base64":
        raise ValueError("Metadata encoding unavailable")
    raw = base64.b64decode(payload[0], validate=True)
    if len(raw) < 65 or raw[0] != 4 or raw[33:65] != decode58(mint,32):
        raise ValueError("Metadata is not this mint's MetadataV1 account")
    offset, strings = 65, []
    for _ in range(3):
        if offset + 4 > len(raw):
            raise ValueError("Truncated metadata")
        length = struct.unpack_from("<I",raw,offset)[0]
        offset += 4
        if length > 4096 or offset + length > len(raw):
            raise ValueError("Invalid metadata string")
        strings.append(raw[offset:offset+length].decode("utf-8").rstrip("\0"))
        offset += length
    return {"name":strings[0],"symbol":strings[1],"uri":strings[2]}


def verify_evidence(network: str, mint: str, signature: str, metadata_address: str,
                    expected_metadata_uri: str, status: dict, transaction: dict,
                    mint_account: dict, metadata_account: dict) -> dict:
    """Narrow supported proof: finalized mint initialization + Metaplex metadata creation.

    Other issuance protocols remain submitted-unknown until a reviewed decoder is added.
    Creation identity is decoded from the recorded transaction. Current account
    identity is a separate at-or-after-slot observation, never historical state.
    """
    decode58(mint,32); decode58(signature,64); decode58(metadata_address,32)
    if network not in RPC or not expected_metadata_uri:
        raise ValueError("Network and reviewed metadata URI required")
    if not isinstance(status,dict) or status.get("confirmationStatus") != "finalized" or "err" not in status or status["err"] is not None:
        raise ValueError("Transaction is not finalized successfully")
    if not isinstance(transaction,dict) or not isinstance(transaction.get("meta"),dict) or "err" not in transaction["meta"] or transaction["meta"]["err"] is not None:
        raise ValueError("Successful transaction unavailable")
    tx = transaction.get("transaction")
    message = tx.get("message") if isinstance(tx,dict) else None
    if not isinstance(message,dict) or not isinstance(tx.get("signatures"),list) or not all(isinstance(s,str) for s in tx["signatures"]) or not isinstance(message.get("accountKeys"),list) or not isinstance(message.get("instructions"),list):
        raise ValueError("Incomplete transaction containers")
    if any(not isinstance(k,str) and not (isinstance(k,dict) and isinstance(k.get("pubkey"),str)) for k in message["accountKeys"]):
        raise ValueError("Invalid transaction account keys")
    if not isinstance(status.get("slot"),int) or isinstance(status["slot"],bool) or status["slot"] < 0 or signature not in transaction["transaction"]["signatures"] or transaction.get("slot") != status["slot"]:
        raise ValueError("Receipt signature/slot mismatch")
    keys = [k["pubkey"] if isinstance(k,dict) else k for k in message["accountKeys"]]
    instructions = list(message.get("instructions", []))
    for inner in transaction["meta"].get("innerInstructions",[]):
        if not isinstance(inner,dict) or not isinstance(inner.get("instructions"),list):
            raise ValueError("Invalid inner-instruction container")
        instructions.extend(inner.get("instructions",[]))
    if any(not isinstance(i,dict) for i in instructions):
        raise ValueError("Invalid instruction container")
    initialized = any(i.get("programId") in TOKEN_PROGRAMS and
                      i.get("parsed",{}).get("type") in {"initializeMint","initializeMint2"} and
                      i.get("parsed",{}).get("info",{}).get("mint") == mint for i in instructions)
    creations = []
    for instruction in instructions:
        if instruction.get("programId") != METADATA_PROGRAM:
            continue
        # Fail closed for every unsupported Metaplex instruction in this narrow proof.
        identity = creation_metadata(instruction.get("data"))
        accounts = instruction_accounts(instruction,keys)
        if len(accounts) not in (6,7) or accounts[5] != SYSTEM_PROGRAM or (len(accounts) == 7 and accounts[6] != RENT_SYSVAR):
            raise ValueError("Unsupported V3 creation account layout")
        if accounts[:2] == [metadata_address,mint]:
            creations.append({**identity,"metadata_address":accounts[0],"mint_address":accounts[1],
                              "mint_authority":accounts[2],"payer":accounts[3],"update_authority":accounts[4]})
    if not initialized or len(creations) != 1:
        raise ValueError("Actual mint initialization/metadata creation not proven")
    parsed = mint_account.get("data",{}).get("parsed",{})
    if mint_account.get("owner") not in TOKEN_PROGRAMS or parsed.get("type") != "mint" or not parsed.get("info",{}).get("isInitialized"):
        raise ValueError("Mint account identity unavailable")
    identity = metadata_identity(metadata_account,mint)
    uri = identity["uri"]
    creation = creations[0]
    if creation["uri"] != expected_metadata_uri or uri != expected_metadata_uri:
        raise ValueError("Creation/current metadata URI mismatch")
    evidence = {"network":network,"mint_address":mint,"transaction_signature":signature,
                "metadata_address":metadata_address,"metadata_url":uri,"slot":status["slot"],
                "metadata_name":identity["name"],"metadata_symbol":identity["symbol"],
                "creation_metadata":creation,"current_metadata_account":identity,
                "confirmation_status":"finalized", "transaction_error":None,
                "mint_info":parsed["info"],"verifier":"fungip-solana-metaplex-v1",
                "rpc_evidence":{"status":status,"transaction":transaction,"mint_account":mint_account,"metadata_account":metadata_account},
                "raw_evidence_sha256":digest(canonical([status,transaction,mint_account,metadata_account]).encode())}
    return evidence


def qualify_prepared_receipt(evidence: dict, prepared: dict, transaction: dict, raw_metadata: bytes, image_raw: bytes) -> dict:
    creation = evidence.get("creation_metadata") or {}
    if creation.get("decoder") != CREATION_DECODER or any(creation.get(field) != prepared[expected]
            for field,expected in (("name","name"),("symbol","symbol"),("uri","metadata_uri"))):
        raise ValueError("Transaction-time creation differs from immutable preparation")
    if evidence["metadata_url"] != prepared["metadata_uri"]:
        raise ValueError("Receipt URI differs from immutable preparation")
    if evidence["metadata_name"] != prepared["name"] or evidence["metadata_symbol"] != prepared["symbol"]:
        raise ValueError("On-chain name/symbol differ from prepared species")
    message = transaction["transaction"]["message"]
    signers = [key["pubkey"] for key in message["accountKeys"] if isinstance(key,dict) and key.get("signer") is True]
    if prepared["operator_public_wallet"] not in signers:
        raise ValueError("Prepared human operator is not a transaction signer")
    if not isinstance(transaction.get("blockTime"),int) or isinstance(transaction["blockTime"],bool) or transaction["blockTime"] < prepared["prepared_unix_time"]:
        raise ValueError("Historical transaction predates preparation")
    verify_binding(prepared,raw_metadata,image_raw)
    return {**evidence,"species_binding":prepared["metadata_binding"],"prepared_payload_sha256":digest(canonical(prepared).encode())}


def read_receipt(network: str, mint: str, signature: str, metadata_address: str, prepared: dict) -> dict:
    decode58(mint,32); decode58(signature,64); decode58(metadata_address,32)
    status = rpc(network,"getSignatureStatuses",[[signature],{"searchTransactionHistory":True}])["value"][0]
    if not status or status.get("confirmationStatus") != "finalized":
        raise ValueError("Submission remains unknown")
    transaction = rpc(network,"getTransaction",[signature,{"encoding":"jsonParsed","commitment":"finalized","maxSupportedTransactionVersion":0}])
    if "err" not in status:
        raise ValueError("Receipt error/result field missing")
    if status["err"] is not None and transaction and isinstance(transaction.get("meta"),dict) and "err" in transaction["meta"] and transaction["meta"]["err"] == status["err"]:
        if signature not in transaction["transaction"]["signatures"] or status["slot"] != transaction["slot"]:
            raise ValueError("Failed receipt identity mismatch")
        return {"network":network,"transaction_signature":signature,"confirmation_status":"finalized",
                "transaction_error":status["err"],"slot":status["slot"],"verifier":"fungip-solana-metaplex-v1",
                "rpc_evidence":{"status":status,"transaction":transaction}}
    mint_result = rpc(network,"getAccountInfo",[mint,{"encoding":"jsonParsed","commitment":"finalized","minContextSlot":status["slot"]}])
    metadata_result = rpc(network,"getAccountInfo",[metadata_address,{"encoding":"base64","commitment":"finalized","minContextSlot":status["slot"]}])
    if not mint_result["value"] or not metadata_result["value"]:
        raise ValueError("Finalized accounts unavailable")
    contexts = {}
    for kind,result in (("mint",mint_result),("metadata",metadata_result)):
        context_slot = (result.get("context") or {}).get("slot")
        if not isinstance(context_slot,int) or isinstance(context_slot,bool) or context_slot < status["slot"]:
            raise ValueError("Current account context unavailable or below receipt slot")
        contexts[kind] = context_slot
    evidence = verify_evidence(network,mint,signature,metadata_address,prepared["metadata_uri"],status,transaction,
                              mint_result["value"],metadata_result["value"])
    evidence["current_account_observation"] = {"context_slots":contexts,"historical_state":False}
    raw_metadata = read_uri_bytes(prepared["metadata_uri"])
    image_raw = read_uri_bytes(prepared["metadata_image_uri"],32*1024*1024)
    return qualify_prepared_receipt(evidence,prepared,transaction,raw_metadata,image_raw)
