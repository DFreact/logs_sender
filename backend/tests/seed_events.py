"""Fixtures enter through the actual durable receipt and normalization services."""


def seed_events(factory, store, settings):
    from email import policy
    from email.message import EmailMessage

    from sqlalchemy import select

    from app.persistence.models import WorkItem
    from app.security.tokens import digest
    from app.services.ingestion import issue_key, receive
    from app.workers.execution import execute

    message = EmailMessage()
    message["From"] = "backup@example.org"
    message["To"] = "events@localhost"
    message["Subject"] = "Проверка резервного копирования"
    message.set_content("Резервная копия создана.")
    message.add_attachment(
        b"fixture-attachment", maintype="application", subtype="octet-stream", filename="report.txt"
    )
    receive(
        factory,
        store,
        message.as_bytes(policy=policy.SMTP),
        "message/rfc822",
        kind="SMTP",
        envelope_sender="delivery@example.org",
        recipients=["events@localhost"],
    )
    with factory.begin() as db:
        _, token = issue_key(db, "Проверка интерфейса")
    receive(
        factory,
        store,
        '{"subject":"Проверка диска","body":"Осталось мало места","severity":"WARNING"}'.encode(),
        "application/json",
        kind="REST",
        token_hash=digest(token),
    )
    with factory() as db:
        identifiers = db.scalars(select(WorkItem.id).where(WorkItem.kind == "NORMALIZE")).all()
    for identifier in identifiers:
        execute(factory, store, settings, str(identifier))
