"""Batch payload review queue. Missing evidence is never replaced with plausible values."""


def draft(entry: dict, view: dict | None = None, recipient: dict | None = None) -> dict:
    r = entry["record"]
    blockers = list(entry["errors"])
    if not view or not view.get("live_verified"):
        blockers.append("canonical_page_and_download_unverified")
    image = r.get("image")
    if not image or image.get("visual_review") != "approved":
        blockers.append("image_visual_and_license_review_pending")
    if not recipient or recipient.get("handle") != "nodefather" or not recipient.get("x_account_id") or not recipient.get("verified_at"):
        blockers.append("recipient_identity_unverified")
    blockers.extend(["scientific_review_pending", "ticker_review_pending", "current_terms_human_review_pending",
                     "metadata_upload_and_binding_unverified","operator_public_wallet_unverified"])
    description = f'{r["accepted_name"]}. FungiP research collectible. Reference DNA and photo credits on the species page. No species, genome or IP ownership.'
    if len(description) > 200:
        description = f'{r["accepted_name"]}. FungiP research collectible. DNA and photo credits on species page. No species or genome ownership.'
    return {"species_id":r["species_id"],"record_sha256":entry["record_sha256"],"status":"draft","name":r.get("scientific_name") or r["accepted_name"],
            "symbol":r["ticker"],"description":description,"description_characters":len(description),
            "venue_description_limit":256,"fee_text_length":"must inspect visible form", "initial_buy":0,
            "image_local_path":image.get("local_path") if image else None,
            "image_sha256":image.get("sha256") if image else None,
            "attribution":image.get("attribution") if image else None,
            "image_license":image.get("license_code") if image else None,
            "credit_url":image.get("source_page") if image else None,
            "canonical_species_url":view.get("canonical_url") if view and view.get("live_verified") else None,
            "recipient_lookup":"nodefather", "verified_recipient":recipient,
            "network":None,"operator_public_wallet":None,"metadata_uri":None,"metadata_image_uri":None,"metadata_description":None,
            "terms_review":{"retrieved_date":"2026-10-02","terms_version":"1.9",
                            "terms_url":"https://usepaid.app/legal/terms", "docs_url":"https://usepaid.app/docs",
                            "launch_url":"https://usepaid.app/launch", "x_money_payouts":"paused",
                            "fee_entitlement":"not established by naming account"},
            "release_blockers":sorted(set(blockers)), "human_review":None}
