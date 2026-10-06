"""Restricted receiving endpoint. Never relays mail or performs outbound delivery."""

import asyncio
import ipaddress
import logging
import signal
import ssl
import sys
import time
from pathlib import Path
from uuid import uuid4

from aiosmtpd.smtp import SMTP

from app.api.errors import ApiFailure, log_failure
from app.observability.health import heartbeat
from app.persistence.database import build_engine, build_session_factory
from app.services.ingestion import receive, store_for
from app.settings import Settings
from app.workers.runtime import SafeLogs


class Receiver:
    def __init__(self, factory, store, settings):
        self.factory, self.store, self.settings = factory, store, settings
        self.window, self.count = 0, 0

    async def handle_RCPT(self, server, session, envelope, address, options):
        if address.lower() not in {item.lower() for item in self.settings.smtp_recipients}:
            return "550 5.1.1 Recipient not accepted"
        if len(envelope.rcpt_tos) >= self.settings.smtp_max_recipients:
            return "452 4.5.3 Too many recipients"
        envelope.rcpt_tos.append(address)
        return "250 2.1.5 OK"

    async def handle_DATA(self, server, session, envelope):
        window = int(time.monotonic()) // 60
        if window != self.window:
            self.window, self.count = window, 0
        if self.count >= self.settings.ingest_per_minute:
            return "451 4.7.0 Rate limit; retry later"
        self.count += 1
        try:
            identifier = await asyncio.to_thread(
                receive,
                self.factory,
                self.store,
                envelope.original_content,
                "message/rfc822",
                kind="SMTP",
                envelope_sender=envelope.mail_from,
                recipients=envelope.rcpt_tos,
            )
        except Exception as error:
            if not isinstance(error, ApiFailure):
                log_failure(error, uuid4().hex)
            return "451 4.3.0 Storage unavailable; retry later"
        return f"250 2.0.0 Accepted {identifier}"

    async def handle_exception(self, error):
        log_failure(error, uuid4().hex)
        return "451 4.3.0 Temporary failure; retry later"


class RestrictedSMTP(SMTP):
    def __init__(self, handler, connections, settings, **kwargs):
        self.connections, self.settings, self.accepted = connections, settings, False
        super().__init__(handler, **kwargs)

    def connection_made(self, transport):
        peer = transport.get_extra_info("peername")
        allowed = peer and any(
            ipaddress.ip_address(peer[0]) in ipaddress.ip_network(net)
            for net in self.settings.smtp_allowed_networks
        )
        if not allowed or (
            self not in self.connections
            and len(self.connections) >= self.settings.smtp_max_connections
        ):
            transport.write(b"421 4.7.0 Connection not accepted\r\n")
            transport.close()
            return
        self.accepted = True
        self.connections.add(self)
        super().connection_made(transport)

    def connection_lost(self, exc):
        if self.accepted:
            self.connections.discard(self)
            super().connection_lost(exc)


def tls_context(settings):
    if settings.smtp_tls_cert and settings.smtp_tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(settings.smtp_tls_cert, settings.smtp_tls_key)
        return context
    if not settings.smtp_allow_plaintext:
        raise RuntimeError("SMTP_TLS_REQUIRED")
    return None


async def serve(settings):
    context = tls_context(settings)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    receiver = Receiver(factory, store_for(settings), settings)
    loop, stopped, connections, identifier = (
        asyncio.get_running_loop(),
        asyncio.Event(),
        set(),
        uuid4(),
    )
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    server = await loop.create_server(
        lambda: RestrictedSMTP(
            receiver,
            connections,
            settings,
            hostname="eventhub",
            ident="receiver",
            decode_data=False,
            data_size_limit=min(settings.ingest_max_bytes, settings.blob_max_bytes),
            timeout=60,
            command_call_limit=100,
            auth_exclude_mechanism=["LOGIN", "PLAIN"],
            tls_context=context,
            require_starttls=not settings.smtp_allow_plaintext,
        ),
        settings.smtp_host,
        settings.smtp_port,
    )
    try:
        while not stopped.is_set():
            try:
                await asyncio.to_thread(heartbeat, factory, "SMTP", identifier)
                Path("/tmp/eventhub-background-alive").write_text(str(time.time()))
            except Exception:
                logging.getLogger("eventhub.smtp").warning("HEARTBEAT_FAILED")
            try:
                await asyncio.wait_for(stopped.wait(), settings.background_poll_seconds)
            except TimeoutError:
                pass
    finally:
        server.close()
        await server.wait_closed()
        for connection in tuple(connections):
            connection.transport.close()
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, handlers=[SafeLogs()], force=True)
    try:
        asyncio.run(serve(Settings()))
    except Exception:
        sys.stderr.write('{"code":"SMTP_START_FAILED"}\n')
        raise SystemExit(1) from None
