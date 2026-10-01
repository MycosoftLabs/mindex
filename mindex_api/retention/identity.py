"""Verified Supabase user identity for the private retention v1 boundary.

Authentication yields a selected scope, never membership. Every repository
operation must authorize that scope against current server-owned membership.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from .contracts import Principal, RetentionError

MAX_TOKEN_BYTES = 16_384
MAX_JWKS_BYTES = 65_536
MAX_JWKS_KEYS = 32
MAX_HEADER_BYTES = 1_024
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+\Z")
_KID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ALGORITHMS = frozenset({"RS256", "ES256"})
KeyProvider = Callable[[], Awaitable[Mapping[str, Any]]]


def _unauthorized() -> RetentionError:
    # Do not expose token claims, key material, or detailed signature failures.
    return RetentionError("unauthorized", status=401)


def _unavailable() -> RetentionError:
    return RetentionError("identity_unavailable", status=503)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def _nonfinite(_: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _json(data: str | bytes) -> Any:
    return json.loads(data, object_pairs_hook=_object, parse_constant=_nonfinite)


def _uuid(value: str | UUID) -> str:
    if isinstance(value, UUID):
        if value.int == 0:
            raise ValueError("Expected non-zero UUID")
        return str(value)
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Expected UUID")
    parsed = UUID(value)
    if str(parsed) != value.lower() or parsed.int == 0:
        raise ValueError("Expected non-zero canonical UUID")
    return str(parsed)


def _https_url(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or len(value) > 2_048 or not value.isascii():
        raise ValueError("Invalid trusted URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError("A fixed HTTPS URL without credentials/query/fragment is required")
    return parsed.hostname.lower(), parsed.port or 443


class IdentityVerifier:
    """Explicitly enabled, asynchronous, bounded asymmetric JWT verifier.

    ``key_provider`` is a trusted server-injected offline/provider adapter that
    returns a complete JWKS without accepting token-selected input. Production
    normally supplies ``jwks_url``. ``http_transport`` exists for deterministic
    network-boundary fixtures; it must never be selected by a request.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_provider: KeyProvider | None = None,
        enabled: bool = False,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        cache_ttl_seconds: float = 300,
        refresh_cooldown_seconds: float = 30,
        fetch_timeout_seconds: float = 5,
        clock_skew_seconds: int = 30,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        issuer_origin = _https_url(issuer)
        if issuer.endswith("/"):
            raise ValueError("Issuer must not end with a slash")
        if not isinstance(audience, str) or not audience or len(audience) > 256:
            raise ValueError("A fixed audience is required")
        if (jwks_url is None) == (key_provider is None):
            raise ValueError("Configure exactly one trusted JWKS URL or key provider")
        if jwks_url is not None and _https_url(jwks_url) != issuer_origin:
            raise ValueError("JWKS URL must share the configured issuer HTTPS origin")
        if key_provider is not None and not callable(key_provider):
            raise ValueError("Key provider must be an async callable")
        selected_algorithms = tuple(algorithms)
        if not selected_algorithms or not set(selected_algorithms) <= _ALGORITHMS:
            raise ValueError("Only RS256 and ES256 are supported")
        for value, minimum, maximum in (
            (cache_ttl_seconds, 1, 600),
            (refresh_cooldown_seconds, 1, 300),
            (fetch_timeout_seconds, 0.01, 10),
            (clock_skew_seconds, 0, 60),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Invalid identity time limit")
            if not minimum <= value <= maximum:
                raise ValueError("Identity time limit outside permitted bounds")
        self.issuer = issuer
        self.audience = audience
        self.enabled = enabled is True
        self._jwks_url = jwks_url
        self._key_provider = key_provider
        self._algorithms = selected_algorithms
        self._cache_ttl = cache_ttl_seconds
        self._cooldown = refresh_cooldown_seconds
        self._fetch_timeout = fetch_timeout_seconds
        self._clock_skew = clock_skew_seconds
        self._http_transport = http_transport
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires_at = 0.0
        self._refresh_after = 0.0
        self._lock = asyncio.Lock()

    async def verify(
        self, token: str, tenant_id: str | UUID, project_id: str | UUID
    ) -> Principal:
        """Verify identity and normalize a requested scope; never grant access.

        Relaying the original bearer JWT through an authenticated BFF/MAS hop is
        supported. No unsigned owner/user header is accepted by this API.
        """
        if not self.enabled:
            raise _unavailable()
        try:
            tenant = _uuid(tenant_id)
            project = _uuid(project_id)
        except (ValueError, TypeError, AttributeError):
            raise RetentionError("invalid_scope", status=400) from None
        try:
            header = self._header(token)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise _unauthorized() from None
        key = await self._key(header["kid"])
        if key.algorithm_name != header["alg"]:
            raise _unauthorized()
        try:
            claims = jwt.decode(
                token,
                key.key,
                # This is the fixed server allowlist, never header-derived.
                algorithms=self._algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self._clock_skew,
                options={
                    "require": ["iss", "aud", "sub", "exp", "iat", "role", "is_anonymous"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "strict_aud": True,
                },
            )
            # Supabase uses integer NumericDate values. Reject coercion such as
            # bool/string timestamps and impossible lifetimes after verification.
            for name in ("exp", "iat", "nbf"):
                if name in claims and type(claims[name]) is not int:
                    raise ValueError("Invalid NumericDate")
            if claims["iss"] != self.issuer or claims["aud"] != self.audience:
                raise ValueError("Invalid issuer or audience")
            if claims["exp"] <= claims["iat"] or claims.get("nbf", claims["iat"]) >= claims["exp"]:
                raise ValueError("Invalid token lifetime")
            if claims["role"] != "authenticated" or claims["is_anonymous"] is not False:
                raise ValueError("Not a permanent authenticated user")
            subject = _uuid(claims["sub"])
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise _unauthorized() from None
        return Principal(
            issuer=self.issuer, subject=subject, tenant_id=tenant, project_id=project
        )

    def _header(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or not token.isascii():
            raise ValueError("Invalid JWT input")
        parts = token.split(".")
        if len(parts) != 3 or not all(_SEGMENT.fullmatch(part) for part in parts):
            raise ValueError("Invalid compact JWT")
        header_bytes = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
        if len(header_bytes) > MAX_HEADER_BYTES:
            raise ValueError("Oversized header")
        header = _json(header_bytes)
        # Strict JSON parsing rejects duplicate/ambiguous claims even though no
        # payload value is trusted here. PyJWT subsequently verifies all claims.
        payload = _json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise ValueError("Expected JWT objects")
        if header.get("alg") not in self._algorithms or header.get("typ", "JWT") != "JWT":
            raise ValueError("Unsupported JWT algorithm/type")
        if not isinstance(header.get("kid"), str) or not _KID.fullmatch(header["kid"]):
            raise ValueError("Invalid key identifier")
        # Never resolve token-chosen URLs/keys/certificates or JOSE extensions.
        if any(name in header for name in ("jku", "jwk", "x5u", "x5c", "crit", "b64")):
            raise ValueError("Unsupported key source or critical header")
        return header

    async def _key(self, kid: str) -> jwt.PyJWK:
        now = time.monotonic()
        if now < self._expires_at and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            now = time.monotonic()
            fresh = now < self._expires_at
            if fresh and kid in self._keys:
                return self._keys[kid]
            if now < self._refresh_after:
                # Rate-limit unknown-kid storms and failed-fetch retries globally,
                # rather than retaining an attacker-controlled negative-kid cache.
                raise _unauthorized() if fresh else _unavailable()
            self._refresh_after = now + self._cooldown
            try:
                async with asyncio.timeout(self._fetch_timeout):
                    raw = await self._fetch()
                keys = self._parse_keys(raw)
            except Exception:
                # A failed refresh must never extend trust in stale/revoked keys.
                # asyncio.CancelledError remains uncaught and propagates normally.
                self._keys = {}
                self._expires_at = 0.0
                raise _unavailable() from None
            self._keys = keys
            self._expires_at = time.monotonic() + self._cache_ttl
            if kid not in keys:
                raise _unauthorized()
            return keys[kid]

    async def _fetch(self) -> Mapping[str, Any]:
        if self._key_provider is not None:
            return await self._key_provider()
        async with httpx.AsyncClient(
            timeout=self._fetch_timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._http_transport,
        ) as client:
            async with client.stream(
                "GET", self._jwks_url, headers={"Accept": "application/json", "Accept-Encoding": "identity"}
            ) as response:
                if response.status_code != 200:
                    raise ValueError("JWKS unavailable")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("Compressed JWKS is unsupported")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > MAX_JWKS_BYTES):
                    raise ValueError("JWKS exceeds byte limit")
                raw = bytearray()
                async for chunk in response.aiter_raw(chunk_size=4_096):
                    if len(raw) + len(chunk) > MAX_JWKS_BYTES:
                        raise ValueError("JWKS exceeds byte limit")
                    raw.extend(chunk)
                return _json(bytes(raw))

    def _parse_keys(self, raw: Mapping[str, Any]) -> dict[str, jwt.PyJWK]:
        if not isinstance(raw, Mapping):
            raise ValueError("Expected JWKS object")
        if len(json.dumps(raw, allow_nan=False).encode("utf-8")) > MAX_JWKS_BYTES:
            raise ValueError("JWKS exceeds byte limit")
        entries = raw.get("keys")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_JWKS_KEYS:
            raise ValueError("Invalid JWKS key count")
        keys: dict[str, jwt.PyJWK] = {}
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid JWK")
            kid = entry.get("kid")
            if not isinstance(kid, str) or not _KID.fullmatch(kid) or kid in seen:
                raise ValueError("Missing, invalid or duplicate JWK kid")
            seen.add(kid)
            if any(name in entry for name in ("d", "p", "q", "dp", "dq", "qi", "oth", "k")):
                raise ValueError("JWKS must contain public asymmetric keys only")
            if entry.get("alg") not in self._algorithms:
                continue
            if entry.get("use", "sig") != "sig" or entry.get("key_ops", ["verify"]) != ["verify"]:
                raise ValueError("JWK does not authorize signature verification")
            if any(name in entry for name in ("jku", "jwk", "x5u", "x5c")):
                raise ValueError("Indirect JWK key source is unsupported")
            if entry["alg"] == "RS256" and entry.get("kty") != "RSA":
                raise ValueError("Mismatched RSA key type")
            if entry["alg"] == "ES256" and (entry.get("kty") != "EC" or entry.get("crv") != "P-256"):
                raise ValueError("Mismatched EC key type")
            # Bound key integers before handing them to the crypto backend.
            fields = {"n": 1_366, "e": 8} if entry["alg"] == "RS256" else {"x": 43, "y": 43}
            for name, maximum in fields.items():
                part = entry.get(name)
                if not isinstance(part, str) or len(part) > maximum or not _SEGMENT.fullmatch(part):
                    raise ValueError("Invalid or oversized public key component")
            jwk = jwt.PyJWK.from_dict(entry)
            if isinstance(jwk.key, rsa.RSAPublicKey):
                if not 2_048 <= jwk.key.key_size <= 8_192:
                    raise ValueError("RSA key size outside permitted bounds")
            elif not isinstance(jwk.key, ec.EllipticCurvePublicKey):
                raise ValueError("Unsupported public key")
            keys[kid] = jwk
        if not keys:
            raise ValueError("No approved verification keys")
        return keys

    async def invalidate_cache(self) -> None:
        """Server/operator cache purge for a reviewed signing-key revocation.

        This does not invalidate an upstream Supabase edge cache or already
        issued user sessions. Never expose it as an unauthenticated route.
        """
        async with self._lock:
            self._keys = {}
            self._expires_at = 0.0
            self._refresh_after = 0.0
