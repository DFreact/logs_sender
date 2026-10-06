"""Local-only Telegram/HTTPS imitator for disposable Compose verification."""

import json
import ssl
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from aiosmtpd.controller import Controller

messages, counts, guard = [], {}, Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        with guard:
            payload = json.dumps({"messages": messages}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        data = json.loads(
            self.rfile.read(min(65536, int(self.headers.get("Content-Length", "0"))))
        )
        if self.path.endswith("/getMe"):
            status, payload = 200, {"ok": True, "result": {"id": 123, "is_bot": True}}
        else:
            text = data.get("text", data.get("message", ""))
            with guard:
                counts[text] = counts.get(text, 0) + 1
                number = counts[text]
                # Never retain auth headers or token-bearing request paths.
                messages.append(
                    {
                        "text": text,
                        "chat_id": data.get("chat_id"),
                        "key": self.headers.get("Idempotency-Key"),
                    }
                )
            if "permanent" in text:
                status, payload = (
                    403,
                    {"ok": False, "description": "secret-details-must-not-leak"},
                )
            elif "retry-once" in text and number == 1:
                status, payload = 429, {"ok": False, "parameters": {"retry_after": 1}}
            else:
                if "uncertain-once" in text and number == 1:
                    time.sleep(20)
                status, payload = 200, {"ok": True, "result": {"message_id": number}}
        raw = json.dumps(payload).encode()
        try:
            self.send_response(status)
            if status == 429:
                self.send_header("Retry-After", "1")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass


server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/run/fixtures/cert.pem", "/run/fixtures/key.pem")


class MailHandler:
    async def handle_DATA(self, server, session, envelope):
        message = BytesParser(policy=policy.default).parsebytes(envelope.content)
        text = message.get_body(preferencelist=("plain",)).get_content()
        with guard:
            messages.append(
                {"text": text, "subject": str(message.get("Subject", "")), "smtp": True}
            )
        return "250 Accepted"


mail = Controller(
    MailHandler(),
    hostname="0.0.0.0",
    port=465,
    ssl_context=context,
    data_size_limit=65536,
)
mail.start()
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
