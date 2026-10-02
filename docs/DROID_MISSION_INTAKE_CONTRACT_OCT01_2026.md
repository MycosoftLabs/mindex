# Droid mission intake — source candidate, 1 October 2026

## What is implemented

`/devices/request-mission` is a dedicated field-mission intake. The existing Droids “Discuss a field mission” CTA links directly to it. Users can type a latitude/longitude, choose a map point after explicitly loading the map, or explicitly request browser geolocation. A center and radius (0–50,000 meters) describe the requested planning area. Purpose, task, requested deliverables, location name, access notes and explicit private-retention consent are submitted together.

This is source-local implementation, not a deployed booking service. No deployment, migration, live charge, email, field action or provider ingestion ran during development. A request is never represented as a scheduled mission. Receipt payment status is read from canonical records, never inferred from a browser return URL.

## Canonical boundary

Website handlers: `app/api/devices/mission-requests/route.ts`, `[id]/route.ts`, `[id]/checkout/route.ts`, and `webhook/route.ts`; core contracts and handlers live in `lib/mission-requests`. The canonical repository is the separate MINDEX checkout: `mindex_api/mission_requests.py`, `mindex_api/routers/mission_requests.py`, and `migrations/20261001_mission_requests.sql`. MINDEX mounts the domain router below the configured internal prefix. No public source-capture tables, generic memory stores or browser persistence are used.

The website obtains the signed-in user through Supabase `auth.getUser`. It derives the subject from that verified result and checks the configured issuer against the Supabase project. A short-lived HMAC binds method, exact path, body digest, role, issuer and subject; MINDEX also requires existing internal service authentication. Customer reads and checkout creation use an `issuer + owner_subject` SQL predicate. This slice supports private individual ownership; organization membership/delegation is a future integration with brief 09, not an inferred tenant feature.

Create obtains a per-owner PostgreSQL transaction lock, deduplicates `(issuer, owner, idempotency_key)`, rejects changed payloads under the same key, and limits new requests to ten per owner per hour. A successful response requires transaction commit followed by an owner-bound readback. Failure after a possible commit leaves the UI uncertain and retains the same in-memory retry key and payload. Closing the tab loses that retry context; an owner request-list/recovery UI is not yet included.

Only private domain records are implemented here. Shared retention/identity/memory contract ownership remains with company brief 09. There is no automatic MYCA recall, NLM training, public ledger publication or canonical delivery-artifact registration in this slice.

## Quote, payment and state authority

1. A committed request begins `submitted`; deployment is `not_scheduled`, delivery `not_available`.
2. A separately authenticated internal operator submits a server-approved USD quote and scope. It requires both an operator-role delegation and a separate operator secret. The customer cannot submit a price. Quote approval does not reserve capacity.
3. Checkout reserves a canonical payment attempt tied to exactly that quote and request. A composite database foreign key enforces the relationship. Amount, currency and deterministic checkout expiry come from MINDEX. The Stripe idempotency key derives from the durable attempt ID. Canonical binding must commit before the checkout URL is returned.
4. Stripe receives opaque request, quote and attempt IDs only, not mission text or coordinates. Return origins are fixed server configuration; client host headers and arbitrary redirects are rejected.
5. The webhook verifies the raw-body Stripe signature with the SDK and checks test/live mode. Verified paid events must match canonical amount, currency, request, quote, session and payment intent. Event IDs and normalized payload digests deduplicate callbacks. Refund totals are monotonic; late failed/expired events cannot erase recorded payment. A refund callback is a record of a provider refund, not authority to initiate one.
6. Payment never dispatches hardware, assigns staff, marks data delivered or transitions a mission to queued/scheduled. Those workflows remain unimplemented operational gates.

A prepared checkout currently freezes quote revision. Failed/expired checkout recovery and revised scopes require a reviewed operator workflow; the source does not silently replace an attempt. Checkout sessions use Stripe’s hosted domain only; custom payment domains are not supported by this initial allowlist.

## Security and privacy limits

Same-origin checks cover customer mutations. The backend enforces identity, strict field bounds, request size (16 KiB), owner rate admission and transaction integrity. Webhook raw bodies are bounded at 64 KiB and exempt from browser-origin checks because signatures authenticate the provider. Customer responses are `private, no-store`. Production internal transport must use HTTPS. No private payloads or credentials are deliberately logged.

Map loading is optional and separately disclosed: CARTO raster tile requests expose the viewed geographic area to the basemap provider. Typed coordinates work without geolocation permission or map loading. No seeded or simulated droids appear. Geolocation is one explicit lookup, not ongoing tracking. Mission details stay in the form until submitted; no localStorage/sessionStorage copies are created. Access codes/passwords should not be entered.

Before service launch: review private-data retention/deletion duration and operational access; rate-limit the gateway and authenticated read/checkout/webhook paths; qualify organization membership if required; validate TLS/network service restrictions and secrets separation; grant only the actual MINDEX service role access to the new private schema. The migration revokes PUBLIC schema/table rights and does not guess a deployment role or bypass application owner predicates. PostgreSQL row-level policies are not claimed by this migration.

## Configuration — names only, no credentials

Website requires `NEXT_PUBLIC_SUPABASE_URL`, its existing Supabase configuration, `MISSION_REQUEST_IDENTITY_ISSUER` (`<project URL>/auth/v1`), `MISSION_REQUEST_MINDEX_URL` (explicit internal API base including its internal prefix), `MINDEX_INTERNAL_TOKEN`, `MISSION_REQUEST_DELEGATION_SECRET` (at least 32 characters), and fixed `MISSION_REQUEST_PUBLIC_ORIGIN`.

MINDEX requires its existing database/internal-auth configuration, `MISSION_REQUESTS_ENABLED=1`, matching issuer and delegation secret, and an independently provisioned `MISSION_REQUEST_OPERATOR_TOKEN` for quote approval. Keep that operator token out of the customer website environment.

Payment is off unless `MISSION_REQUEST_PAYMENTS_MODE=test` with a test Stripe key, or mode `live` with both `MISSION_REQUEST_LIVE_PAYMENTS_APPROVED=1` and a live key. A dedicated `MISSION_REQUEST_STRIPE_WEBHOOK_SECRET` is required. No setting was populated or enabled in this task. A configuration flag is a technical gate, not authorization to launch or charge.

## Validation and remaining acceptance

Offline tests exercise the real BFF functions, UI DOM, actual Stripe SDK signature verification, cross-language UTF-8 HMAC fixture, actual FastAPI router/internal-auth dependency, strict models, transaction-failure paths and owner/idempotency/rate predicates using isolated dependencies. They do not prove PostgreSQL migration execution, real concurrency/locking, Stripe account/webhook configuration, authenticated staging round trips, map rendering or field operations. Parent owns browser review; no browser acceptance is claimed here.

Release sequence: independent source/security review; authorized disposable PostgreSQL schema and multi-owner/concurrent transaction tests; staging identity and commit/readback tests; explicit Stripe test-mode checkout/webhook/refund and outage qualification; operator capacity/access/quote workflow; private delivery artifact contract and retention policy; then manual Cursor deployment review. No workflow dispatch or production migration is part of this implementation.

References: [Stripe Checkout Session creation](https://docs.stripe.com/api/checkout/sessions/create), [Stripe webhook signatures](https://docs.stripe.com/webhooks/signatures), [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests). Runtime expiry is fixed from the durable attempt, within the provider’s supported creation window; retries do not change the parameters under one idempotency key.
