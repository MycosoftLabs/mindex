"""Synthetic receipt-decoder controls; none are real tokens or live chain certification."""
import base64
import copy
import struct
import json
import unittest
from unittest.mock import patch
from mindex_etl.fungip.receipts import ALPHABET, METADATA_PROGRAM, TOKEN_PROGRAMS, SYSTEM_PROGRAM, RENT_SYSVAR, CREATION_DECODER, decode58, creation_metadata, verify_evidence, qualify_prepared_receipt, read_receipt
from mindex_etl.fungip.metadata import validate_metadata, verify_binding, read_uri_bytes
from mindex_etl.fungip.catalog import digest
from mindex_etl.fungip.ledger import check_transition


def encode58(raw):
    n = int.from_bytes(raw,"big"); encoded = ""
    while n:
        n, remainder = divmod(n,58); encoded = ALPHABET[remainder] + encoded
    return "1"*(len(raw)-len(raw.lstrip(b"\0")))+encoded


def creation_bytes(name="Synthetic research fixture",symbol="SYNTH",uri="https://example.test/reviewed-metadata.json",tail=b"\0\0\0\1\0"):
    raw = bytes([33])
    for value in (name,symbol,uri):
        data = value.encode(); raw += struct.pack("<I",len(data))+data
    return raw + struct.pack("<H",0) + tail


def fixture():
    mint, signature, metadata = encode58(bytes([1])*32), encode58(bytes([3])*64), encode58(bytes([2])*32)
    uri = "https://example.test/reviewed-metadata.json"
    raw = bytes([4])+bytes([7])*32+decode58(mint,32)
    for value in ("Synthetic research fixture","SYNTH",uri):
        data = value.encode(); raw += struct.pack("<I",len(data))+data
    program = sorted(TOKEN_PROGRAMS)[0]
    status = {"confirmationStatus":"finalized","err":None,"slot":100}
    transaction = {"slot":100,"meta":{"err":None,"innerInstructions":[]},"transaction":{"signatures":[signature],"message":{
      "accountKeys":[mint,metadata],"instructions":[
        {"programId":program,"parsed":{"type":"initializeMint2","info":{"mint":mint}}},
        {"programId":METADATA_PROGRAM,"accounts":[metadata,mint,mint,mint,mint,SYSTEM_PROGRAM],"data":encode58(creation_bytes())}]}}}
    account = {"owner":program,"data":{"parsed":{"type":"mint","info":{"isInitialized":True,"supply":"1","decimals":0,"mintAuthority":None,"freezeAuthority":None}}}}
    meta_account = {"owner":METADATA_PROGRAM,"data":[base64.b64encode(raw).decode(),"base64"]}
    return ["solana-devnet",mint,signature,metadata,uri,status,transaction,account,meta_account]


def prepared_fixture(args):
    image = b"synthetic-image"
    metadata = {"name":"Synthetic research fixture","symbol":"SYNTH","description":"Reviewed scientific fixture","image":"https://ipfs.io/ipfs/fixture","website":"https://mycosoft.com/natureos/ancestry/species/11111111-1111-4111-8111-111111111111"}
    raw = json.dumps(metadata).encode()
    prepared = {"species_id":"FG001","record_sha256":"a"*64,"name":metadata["name"],"symbol":metadata["symbol"],"metadata_uri":args[4],
                "canonical_species_url":metadata["website"],"metadata_description":metadata["description"],"metadata_image_uri":metadata["image"],
                "image_sha256":digest(image),"operator_public_wallet":args[1],"prepared_unix_time":1000}
    prepared["metadata_binding"] = validate_metadata(prepared,raw,image)
    args[6]["blockTime"] = 1001
    args[6]["transaction"]["message"]["accountKeys"][0] = {"pubkey":args[1],"signer":True}
    return prepared,raw,image,metadata


class ReceiptTests(unittest.TestCase):
    def test_positive_decoder_fixture(self):
        evidence = verify_evidence(*fixture())
        self.assertEqual(evidence["confirmation_status"],"finalized")
        self.assertEqual(evidence["network"],"solana-devnet")
        self.assertIn("rpc_evidence",evidence)
        self.assertEqual(evidence["creation_metadata"]["decoder"],CREATION_DECODER)
        self.assertEqual(evidence["creation_metadata"]["name"],evidence["current_metadata_account"]["name"])

    def test_url_or_account_key_is_not_issuance_proof(self):
        args = fixture(); args[6]["transaction"]["message"]["instructions"] = []
        with self.assertRaises(ValueError): verify_evidence(*args)

    def test_wrong_mint_metadata_owner_or_uri_rejected(self):
        for case in ("mint","owner","uri"):
            args = fixture()
            if case == "mint":
                raw = bytearray(base64.b64decode(args[8]["data"][0])); raw[33] = 4
                args[8]["data"][0] = base64.b64encode(raw).decode()
            elif case == "owner": args[8]["owner"] = args[7]["owner"]
            else: args[4] = "https://example.test/other.json"
            with self.assertRaises(ValueError): verify_evidence(*args)

    def test_pending_failed_wrong_signature_and_slot_rejected(self):
        for case in ("pending","failed","signature","slot"):
            args = fixture()
            if case == "pending": args[5]["confirmationStatus"] = "confirmed"
            elif case == "failed": args[6]["meta"]["err"] = {"InstructionError":[0,"bad"]}
            elif case == "signature": args[6]["transaction"]["signatures"] = [encode58(bytes([4])*64)]
            else: args[6]["slot"] = 99
            with self.assertRaises(ValueError): verify_evidence(*args)

    def test_unknown_cannot_be_cancelled_or_reprepared(self):
        with self.assertRaises(ValueError): check_transition("submitted-unknown","failed",{"operator_cancelled":True},fixture()[2])
        with self.assertRaises(ValueError): check_transition("submitted-unknown","prepared",{},fixture()[2])
        with self.assertRaises(ValueError): check_transition("confirmed","draft",{},fixture()[2])

    def test_unknown_reconciles_exact_success_or_failure(self):
        args = fixture(); evidence = verify_evidence(*args)
        with self.assertRaises(ValueError): check_transition("submitted-unknown","confirmed",evidence,args[2])
        evidence = {**evidence,"species_binding":{"species_id":"FG001"},"prepared_payload_sha256":"a"*64}
        check_transition("submitted-unknown","confirmed",evidence,args[2])
        failed = {**evidence,"transaction_error":{"InstructionError":[0,"failed"]}}
        check_transition("submitted-unknown","failed",failed,args[2])
        with self.assertRaises(ValueError): check_transition("submitted-unknown","confirmed",failed,args[2])

    def test_missing_error_keys_are_not_success(self):
        for where in ("status","transaction"):
            args = fixture()
            if where == "status": args[5].pop("err")
            else: args[6]["meta"].pop("err")
            with self.assertRaises(ValueError): verify_evidence(*args)

    def test_unrelated_metadata_historical_receipts_and_wrong_operator_rejected(self):
        args = fixture(); prepared,raw,image,metadata = prepared_fixture(args); evidence = verify_evidence(*args)
        qualified = qualify_prepared_receipt(evidence,prepared,args[6],raw,image)
        self.assertEqual(qualified["species_binding"]["species_id"],"FG001")
        for changes in ({"name":"Different species"},{"website":"https://example.test/unrelated"},{"symbol":"OTHER"}):
            altered = json.dumps({**metadata,**changes}).encode()
            with self.assertRaises(ValueError): qualify_prepared_receipt(evidence,prepared,args[6],altered,image)
        with self.assertRaises(ValueError): qualify_prepared_receipt(evidence,{**prepared,"operator_public_wallet":args[3]},args[6],raw,image)
        with self.assertRaises(ValueError): qualify_prepared_receipt(evidence,prepared,{**args[6],"blockTime":999},raw,image)
        with self.assertRaises(ValueError): verify_binding(prepared,raw+b" ",image)

    def test_later_matching_account_cannot_repair_unrelated_creation(self):
        for changes in ({"name":"Unrelated species"},{"symbol":"OTHER"},{"uri":"https://example.test/other.json"},
                        {"name":"Synthetic research fixture\0"},{"symbol":"SYNTH "}):
            with self.subTest(changes=changes):
                args = fixture(); prepared,raw,image,_ = prepared_fixture(args)
                args[6]["transaction"]["message"]["instructions"][1]["data"] = encode58(creation_bytes(**changes))
                # Finalized success, prepared signer/time, and CURRENT account/JSON/image
                # all match preparation. Only the historical creation identity differs.
                with self.assertRaises(ValueError):
                    evidence = verify_evidence(*args)
                    qualify_prepared_receipt(evidence,prepared,args[6],raw,image)

    def test_same_transaction_update_cannot_repair_unrelated_creation(self):
        args = fixture(); prepared,raw,image,_ = prepared_fixture(args)
        instructions = args[6]["transaction"]["message"]["instructions"]
        instructions[1]["data"] = encode58(creation_bytes(name="Unrelated species"))
        instructions.append({"programId":METADATA_PROGRAM,"accounts":[args[3],args[1]],"data":encode58(bytes([15]))})
        with self.assertRaises(ValueError):
            qualify_prepared_receipt(verify_evidence(*args),prepared,args[6],raw,image)

    def test_complete_optional_v3_fields_and_rent_are_supported(self):
        for method in (0,1,2):
            for details in (0,1):
                tail = b"\1"+struct.pack("<I",1)+bytes([9])*32+b"\0\x64"
                tail += b"\1\1"+bytes([8])*32+b"\1"+bytes([method])+struct.pack("<QQ",2,3)
                tail += b"\1\1"+bytes([details])+bytes(8)
                args = fixture(); instruction = args[6]["transaction"]["message"]["instructions"][1]
                instruction["data"] = encode58(creation_bytes(tail=tail)); instruction["accounts"].append(RENT_SYSVAR)
                evidence = verify_evidence(*args)
                self.assertTrue(evidence["creation_metadata"]["is_mutable"])
        tail = b"\1"+struct.pack("<I",5)+(bytes(32)+b"\0\x14")*5
        tail += b"\1\0"+bytes(32)+b"\1\0"+bytes(16)+b"\1\1\0"+bytes(8)
        maximum = creation_bytes(name="n"*32,symbol="s"*10,uri="u"*200,tail=tail)
        self.assertEqual(len(maximum),495)
        self.assertEqual(creation_metadata(encode58(maximum))["name"],"n"*32)

    def test_incomplete_unsupported_or_malformed_payload_rejected(self):
        valid = creation_bytes()
        malformed = [bytes([33]),bytes([0]),bytes([16]),valid+b"\0",valid[:-1],
                     creation_bytes(tail=b"\2\0\0\1\0"),creation_bytes(tail=b"\0\0\0\2\0"),
                     creation_bytes(tail=b"\1"+struct.pack("<I",0)),creation_bytes(tail=b"\1"+struct.pack("<I",6)),
                     creation_bytes(tail=b"\0\1\2"+bytes(32)),
                     creation_bytes(tail=b"\0\0\1\3"+bytes(16)+b"\1\0"),
                     creation_bytes(tail=b"\0\0\0\1\1\2"+bytes(8)),
                     b"\x21"+struct.pack("<I",1)+b"\xff"+valid[30:],
                     creation_bytes(name="n"*33),creation_bytes(symbol="s"*11),creation_bytes(uri="u"*201)]
        for payload in malformed:
            with self.subTest(payload=payload[:12]):
                args = fixture(); args[6]["transaction"]["message"]["instructions"][1]["data"] = encode58(payload)
                with self.assertRaises(ValueError): verify_evidence(*args)
        for encoded in ("0","1"*681,None):
            with self.assertRaises(ValueError): creation_metadata(encoded)
        # Every strict prefix of an otherwise supported complete body is denied.
        for length in range(len(valid)):
            with self.assertRaises(ValueError): creation_metadata(encode58(valid[:length]))

    def test_invalid_account_indices_layout_or_duplicate_creation_rejected(self):
        for accounts in ([0,1,0,0,0,-1],[0,1,0,0,0,99],[0,1,0,0,0,True],
                         [0,1], [0,1,0,0,0,0]):
            args = fixture(); args[6]["transaction"]["message"]["instructions"][1]["accounts"] = accounts
            with self.assertRaises(ValueError): verify_evidence(*args)
        args = fixture(); instructions = args[6]["transaction"]["message"]["instructions"]
        instructions.append(copy.deepcopy(instructions[1]))
        with self.assertRaises(ValueError): verify_evidence(*args)

    def test_creation_strings_are_byte_exact_without_unicode_normalization(self):
        name = "Caf\u00e9"
        self.assertEqual(creation_metadata(encode58(creation_bytes(name=name)))["name"],name)
        self.assertNotEqual(creation_metadata(encode58(creation_bytes(name="Cafe\u0301")))["name"],name)

    def test_account_read_context_is_current_not_historical(self):
        args = fixture(); prepared,raw,image,_ = prepared_fixture(args)
        responses = [{"value":[args[5]]},args[6],{"value":args[7],"context":{"slot":500}},
                     {"value":args[8],"context":{"slot":501}}]
        with patch("mindex_etl.fungip.receipts.rpc",side_effect=responses), patch("mindex_etl.fungip.receipts.read_uri_bytes",side_effect=[raw,image]):
            evidence = read_receipt(*args[:4],prepared)
        self.assertEqual(evidence["slot"],100)
        self.assertEqual(evidence["current_account_observation"],{"context_slots":{"mint":500,"metadata":501},"historical_state":False})

    def test_invalid_or_missing_account_context_cannot_qualify(self):
        for context in ({},{"slot":99},{"slot":True}):
            args = fixture(); prepared,_,_,_ = prepared_fixture(args)
            responses = [{"value":[args[5]]},args[6],{"value":args[7],"context":context},
                         {"value":args[8],"context":{"slot":500}}]
            with patch("mindex_etl.fungip.receipts.rpc",side_effect=responses), patch("mindex_etl.fungip.receipts.read_uri_bytes") as fetch:
                with self.assertRaises(ValueError): read_receipt(*args[:4],prepared)
                fetch.assert_not_called()

    def test_unsupported_metadata_hosts_do_not_fetch(self):
        for uri in ("http://127.0.0.1/internal","https://example.test/metadata","file:///private","https://user:pass@ipfs.io/ipfs/fixture"):
            with self.assertRaises(ValueError): read_uri_bytes(uri)


if __name__ == "__main__": unittest.main()
