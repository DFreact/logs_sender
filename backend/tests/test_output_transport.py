"""Real sockets, local TLS imitators only. Never contact a real recipient."""

import fcntl
import ipaddress
import json
import socket
import ssl
import struct
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from aiosmtpd.controller import Controller
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.adapters.output import Adapter, PreparedNotification


@pytest.fixture
def tls_server(setup, tmp_path, monkeypatch):
    settings = setup[-1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        addresses = []
        for _, name in socket.if_nameindex():
            try:
                raw = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
                candidate = socket.inet_ntoa(raw[20:24])
                if not ipaddress.ip_address(candidate).is_loopback:
                    addresses.append(candidate)
            except OSError:
                continue
    assert addresses, "A local non-loopback IPv4 interface is required"
    address = addresses[0]
    # Real non-loopback local interface so production SSRF checks stay enabled.
    assert not ipaddress.ip_address(address).is_loopback
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr(ssl, "create_default_context", lambda: client_context)
    settings.outbound_hosts, settings.outbound_networks = [address], [address + "/32"]
    return settings, address, server_context


def test_real_smtp_probe_does_not_send_and_send_accepts(tls_server):
    settings, address, context = tls_server
    messages = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            messages.append(envelope.content)
            return "250 OK"

    with socket.socket() as reserved:
        reserved.bind((address, 0))
        port = reserved.getsockname()[1]
    controller = Controller(
        Handler(), hostname=address, port=port, tls_context=context, require_starttls=True
    )
    controller.start()
    try:
        settings.outbound_ports = [port]
        adapter = Adapter(
            settings,
            {
                "kind": "SMTP",
                "host": address,
                "port": port,
                "tls": "STARTTLS",
                "username": "",
                "sender": "hub@example.org",
                "recipients": ["ops@example.org"],
            },
        )
        assert adapter.test_connection() == "CONNECTED" and messages == []
        assert (
            adapter.send(PreparedNotification("Проверка", "Локальное письмо")).outcome == "ACCEPTED"
        )
        assert len(messages) == 1 and b"hub@example.org" in messages[0]
    finally:
        controller.stop()


def test_real_webhook_and_telegram_contract_without_external_requests(tls_server, monkeypatch):
    settings, address, context = tls_server
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, value, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    server = ThreadingHTTPServer((address, 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        settings.outbound_ports = [port]
        adapter = Adapter(
            settings,
            {"kind": "WEBHOOK", "url": f"https://{address}:{port}/hook"},
            lambda: "local-test-token",
        )
        assert adapter.test_connection() == "TLS_ONLY" and requests == []
        prepared = PreparedNotification(
            "Тема", "Текст", payload=json.dumps({"subject": "Тема", "message": "Текст"})
        )
        assert adapter.send(prepared).outcome == "ACCEPTED"
        assert requests == [
            ("/hook", {"subject": "Тема", "message": "Текст"}, "Bearer local-test-token")
        ]
        bot = Adapter(
            settings, {"kind": "TELEGRAM", "chat_id": "-123"}, lambda: "123:local-test-token"
        )
        # Substitute only fixed Telegram destination; TLS, sockets and payload logic remain real.
        monkeypatch.setattr(bot, "destination", lambda: (address, port))
        assert bot.test_connection() == "BOT_VERIFIED"
        assert requests[-1][0].endswith("/getMe") and requests[-1][1] == {}
        assert bot.send(PreparedNotification("", "Текст")).outcome == "ACCEPTED"
        assert requests[-1][0].endswith("/sendMessage")
        assert requests[-1][1] == {"chat_id": "-123", "text": "Текст"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
