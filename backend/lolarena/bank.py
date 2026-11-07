"""Authenticated synthetic submission service with HTTP and MCP interfaces."""
import argparse
import contextvars
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .validate import validate

IDENTITY = contextvars.ContextVar("bank_identity", default=None)
CREDENTIAL = contextvars.ContextVar("bank_credential", default=None)
MAX_BODY = 65536
HTML = Path(__file__).resolve().parent.parent / "bank" / "index.html"


class BankError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status
        super().__init__(message)


def utc_time(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("deadline must include timezone")
    return value.astimezone(timezone.utc)


class Bank:
    def __init__(self, config, database):
        round_data = config.get("round", {})
        batch = round_data.get("scheduled_batch_synthetic") is True and getattr(self, "_batch_class", False)
        if (config.get("mode") != "synthetic" or (not batch and (round_data.get("match_id") != "SYNTHETIC"
                or round_data.get("team_a") != "A" or round_data.get("team_b") != "B"
                or round_data.get("best_of") != 5))):
            raise ValueError("only synthetic rounds are enabled")
        self.config = config
        self.round = config["round"]
        if self.round.get("track", "offline") not in ("offline", "online"):
            raise ValueError("invalid round track")
        self.deadline = utc_time(self.round["deadline"])
        self.database = str(database)
        self.accounts = config["accounts"]
        digests = [entry["token_sha256"] for entry in self.accounts.values()]
        if len(digests) != len(set(digests)) or any(len(d) != 64 for d in digests):
            raise ValueError("invalid or duplicate account credential hashes")
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS submissions (round_id TEXT NOT NULL, account_id TEXT NOT NULL, "
                       "digest TEXT NOT NULL, prediction TEXT NOT NULL, accepted_at TEXT NOT NULL, "
                       "PRIMARY KEY (round_id, account_id))")
            db.execute("CREATE TABLE IF NOT EXISTS settlement_projections (round_id TEXT NOT NULL, account_id TEXT NOT NULL, "
                       "digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (round_id, account_id))")
            db.execute("CREATE TABLE IF NOT EXISTS round_control (round_id TEXT PRIMARY KEY, phase TEXT NOT NULL, phase_until TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS submission_versions (round_id TEXT NOT NULL, account_id TEXT NOT NULL, "
                       "version INTEGER NOT NULL, digest TEXT NOT NULL, prediction TEXT NOT NULL, accepted_at TEXT NOT NULL, "
                       "phase TEXT NOT NULL, PRIMARY KEY(round_id, account_id, version))")

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def authenticate(self, authorization):
        if not authorization.startswith("Bearer "):
            return None
        token = authorization[7:]
        if not token or len(token) > 512:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for name, entry in self.accounts.items():
            if hmac.compare_digest(digest, entry["token_sha256"]):
                return name if entry.get("active") else None
        return None

    def account(self, identity):
        snapshot = self.round.get("accounts", {}).get(identity)
        if snapshot is None:
            raise BankError("Account is not enrolled in this round", 403)
        fields = ("balance", "available", "cap", "cap_with_loan", "debt", "loan_eligible", "loans_taken")
        data = {"account_id": identity, "track": self.round.get("track", "offline"), "round_id": self.round["round_id"],
                **{key: snapshot[key] for key in fields if key in snapshot}}
        projection = self.projection(identity)
        if projection:
            data.update({key: projection[key] for key in (*fields, "net") if key in projection})
            data["settled"] = True
        return data

    def projection(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT digest, payload FROM settlement_projections WHERE round_id = ? AND account_id = ?",
                             (self.round["round_id"], identity)).fetchone()
        if not row:
            return None
        if not hmac.compare_digest(hashlib.sha256(row[1].encode()).hexdigest(), row[0]):
            raise BankError("Settlement projection failed integrity check", 503)
        return json.loads(row[1])

    def match(self, identity):
        self.account(identity)
        return {k: self.round[k] for k in ("round_id", "match_id", "team_a", "team_b", "best_of", "deadline")}

    def receipt(self, identity):
        self.account(identity)
        projection = self.projection(identity)
        if projection:
            return {"submitted": projection["status"] == "ok", "state": "settled", "result": projection}
        with closing(self.connect()) as db:
            row = db.execute("SELECT digest, accepted_at FROM submissions WHERE round_id = ? AND account_id = ?",
                             (self.round["round_id"], identity)).fetchone()
        if not row:
            state = "abstained" if datetime.now(timezone.utc) >= self.deadline else "open"
            return {"submitted": False, "state": state}
        state = "accepted_not_settled" if self.round.get("scheduled_synthetic") is True else "sealed_not_settled"
        return {"submitted": True, "sha256": row[0], "accepted_at": row[1], "state": state}

    def submit(self, identity, prediction):
        self.account(identity)
        clean, errors = validate(prediction, self.round["match_id"], self.round["team_a"], self.round["team_b"])
        if errors:
            raise BankError("Invalid prediction: " + "; ".join(errors))
        body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        credential = CREDENTIAL.get()
        if credential and credential in body:
            raise BankError("Prediction contains an account credential")
        digest = hashlib.sha256(body.encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest, accepted_at FROM submissions WHERE round_id = ? AND account_id = ?",
                             (self.round["round_id"], identity)).fetchone()
            scheduled = self.round.get("scheduled_synthetic") is True
            phase_row = db.execute("SELECT phase, phase_until FROM round_control WHERE round_id = ?", (self.round["round_id"],)).fetchone() if scheduled else None
            phase = phase_row[0] if phase_row else "closed"
            if row:
                if hmac.compare_digest(row[0], digest):
                    return {"submitted": True, "sha256": row[0], "accepted_at": row[1],
                            "state": "accepted_not_settled" if scheduled else "sealed_not_settled", "duplicate": True}
                if not (scheduled and self.round.get("track") == "online" and phase == "review"):
                    raise BankError("An immutable submission already exists", 409)
            accepted_at = datetime.now(timezone.utc)
            if scheduled and phase_row and accepted_at >= utc_time(phase_row[1]):
                phase = "closed"
            if accepted_at >= self.deadline:
                raise BankError("Submission deadline has passed", 409)
            if scheduled and phase not in ("first", "recovery", "review"):
                raise BankError("Submission window is closed", 409)
            if scheduled and phase == "review" and self.round.get("track") != "online":
                raise BankError("Review is unavailable for this track", 409)
            if row:
                db.execute("UPDATE submissions SET digest=?, prediction=?, accepted_at=? WHERE round_id=? AND account_id=?",
                           (digest, body, accepted_at.isoformat(), self.round["round_id"], identity))
            else:
                db.execute("INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
                           (self.round["round_id"], identity, digest, body, accepted_at.isoformat()))
            if scheduled:
                version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM submission_versions WHERE round_id=? AND account_id=?",
                                     (self.round["round_id"], identity)).fetchone()[0]
                db.execute("INSERT INTO submission_versions VALUES (?,?,?,?,?,?,?)",
                           (self.round["round_id"], identity, version, digest, body, accepted_at.isoformat(), phase))
        return {"submitted": True, "sha256": digest, "accepted_at": accepted_at.isoformat(),
                "state": "accepted_not_settled" if scheduled else "sealed_not_settled", "duplicate": False}


class SecurityMiddleware:
    def __init__(self, app, bank):
        self.app = app
        self.bank = bank

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        if host not in self.bank.config["allowed_hosts"] or (origin and origin != "http://" + host):
            await PlainTextResponse("Invalid host or origin", status_code=403)(scope, receive, send)
            return
        if scope["method"] == "POST":
            try:
                size = int(headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            if size < 0 or size > MAX_BODY:
                await PlainTextResponse("Invalid request size", status_code=413)(scope, receive, send)
                return
        path = scope["path"]
        identity = None
        if path == "/mcp" or path.startswith("/api/"):
            identity = self.bank.authenticate(headers.get("authorization", ""))
            if identity is None:
                await JSONResponse({"error": "Unauthorized"}, status_code=401,
                                   headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
        marker = IDENTITY.set(identity)
        credential_marker = CREDENTIAL.set(headers.get("authorization", "")[7:] if identity else None)
        try:
            await self.app(scope, receive, send)
        finally:
            CREDENTIAL.reset(credential_marker)
            IDENTITY.reset(marker)


def create_app(config, database):
    if config.get("round", {}).get("scheduled_batch_synthetic") is True:
        from .bank_batch import BatchBank
        bank = BatchBank(config, database)
    else:
        bank = Bank(config, database)
    return create_app_for_bank(bank)


def create_app_for_bank(bank):
    mcp = FastMCP("Arena Bank", host="0.0.0.0", stateless_http=True, json_response=True)

    @mcp.tool()
    def bank_get_account() -> dict:
        """Get the authenticated account's snapshot for the active round."""
        return bank.account(IDENTITY.get())

    @mcp.tool()
    def bank_get_match() -> dict:
        """Get the active match or match-day batch and submission deadline."""
        return bank.match(IDENTITY.get())

    @mcp.tool()
    def bank_get_submission() -> dict:
        """Get the authenticated account's submission receipt."""
        return bank.receipt(IDENTITY.get())

    @mcp.tool()
    def bank_submit_prediction(prediction: dict[str, Any]) -> dict:
        """Validate and submit a prediction for the authenticated account."""
        return bank.submit(IDENTITY.get(), prediction)

    @mcp.tool()
    def bank_submit_predictions(predictions: list[dict[str, Any]]) -> dict:
        """Submit one or more predictions atomically for the current match day."""
        if not hasattr(bank, "submit_many"):
            raise BankError("This round does not support batches")
        return bank.submit_many(IDENTITY.get(), predictions)

    async def index(request: Request):
        return HTMLResponse(HTML.read_text(encoding="utf-8"), headers={
            "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff"})

    async def health(request: Request):
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def api(request: Request):
        try:
            identity = IDENTITY.get()
            if request.url.path == "/api/account":
                data = bank.account(identity)
            elif request.url.path == "/api/match":
                data = bank.match(identity)
            elif request.method == "GET":
                data = bank.receipt(identity)
            else:
                try:
                    prediction = await request.json()
                except ValueError:
                    raise BankError("Invalid JSON") from None
                if isinstance(prediction, list) and hasattr(bank, "submit_many"):
                    data = bank.submit_many(identity, prediction)
                else:
                    data = bank.submit(identity, prediction)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except BankError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status, headers={"Cache-Control": "no-store"})

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(routes=[Route("/", index), Route("/healthz", health), Route("/api/account", api),
                            Route("/api/match", api), Route("/api/submission", api, methods=["GET", "POST"]),
                            Mount("/", app=mcp_app)], lifespan=lifespan)
    app.add_middleware(SecurityMiddleware, bank=bank)
    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    uvicorn.run(create_app(config, args.database), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
