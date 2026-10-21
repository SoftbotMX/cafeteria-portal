import hashlib
import hmac
import json

import httpx


class ConduitFalso:
    def __init__(self, secreto: str = "cond-s"):
        self.secreto = secreto
        self.enviados: list[dict] = []
        self.responder: int = 200
        self.transport = httpx.MockTransport(self._manejar)

    def _manejar(self, req: httpx.Request) -> httpx.Response:
        ts = req.headers.get("X-Conduit-Timestamp", "")
        firma = req.headers.get("X-Conduit-Signature", "")
        esperada = "hmac-sha256=" + hmac.new(
            self.secreto.encode(), f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, esperada):
            return httpx.Response(401, json={"error": "firma"})
        if req.url.path != "/api/notify":
            return httpx.Response(404)
        self.enviados.append(json.loads(req.content))
        return httpx.Response(self.responder, json={"ok": self.responder == 200, "duplicate": False})
