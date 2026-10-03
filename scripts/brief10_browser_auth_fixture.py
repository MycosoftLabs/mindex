"""Minimal loopback Supabase Auth-compatible user lookup for local Brief10 tests."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt


fixtures = json.loads(Path(os.environ["BRIEF10_FIXTURE_FILE"]).read_text(encoding="utf-8"))
public_key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(fixtures["jwks"]["keys"][0]))
users = {
    user["subject"]: {
        "id": user["subject"], "aud": fixtures["audience"], "role": "authenticated", "email": user["email"],
        "app_metadata": {"provider": "email", "providers": ["email"]}, "user_metadata": {},
        "created_at": fixtures["created_at"],
    }
    for user in fixtures["users"]
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path != "/auth/v1/user":
            self.send_error(404)
            return
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            self.reply(401, {"message": "Invalid token", "error": "invalid_token"})
            return
        try:
            claims = jwt.decode(authorization[7:], public_key, algorithms=["ES256"],
                audience=fixtures["audience"], issuer=fixtures["issuer"],
                options={"require": ["iss", "aud", "sub", "exp", "iat", "role", "is_anonymous"]})
            if claims["role"] != "authenticated" or claims["is_anonymous"] is not False or claims["sub"] not in users:
                raise ValueError("invalid synthetic identity")
        except Exception:
            self.reply(401, {"message": "Invalid token", "error": "invalid_token"})
            return
        self.reply(200, users[claims["sub"]])

    def reply(self, status: int, value: dict):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if os.environ.get("BRIEF10_BIND_HOST") != "127.0.0.1":
        raise SystemExit("BRIEF10_BIND_HOST must be explicitly set to 127.0.0.1")
    HTTPServer(("127.0.0.1", int(os.environ.get("BRIEF10_AUTH_PORT", "8788"))), Handler).serve_forever()
