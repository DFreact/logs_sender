"""Fault checks restricted to the caller's disposable Compose project."""

import json
import smtplib
import time
import urllib.error
import urllib.request

PRELUDE = '''
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import RawMessage, Event, WorkItem
from app.services.ingestion import issue_key
settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)
'''


def verify(run, base, smtp_port):
    def python(code):
        return run('exec', '-T', 'api', 'python', '-', input_text=PRELUDE + code)

    # This secret is captured in memory, never printed or placed in process arguments.
    token = python('''
with factory.begin() as db:
    _, token = issue_key(db, "Проверка восстановления")
print(token)
''').strip()

    def post(payload, key='restart-check'):
        request = urllib.request.Request(base + '/api/v1/ingest', data=payload,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'text/plain',
                     'Idempotency-Key': key}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def smtp():
        with smtplib.SMTP('127.0.0.1', smtp_port, timeout=20) as client:
            return client.sendmail('checks@example.org', ['events@localhost'],
                b'Subject: recovery-check\r\n\r\noriginal-body\r\n')

    try:
        run('stop', 'ingestion', 'redis')
        status, receipt = post(b'recovery-rest-body')
        assert status == 202
        assert post(b'recovery-rest-body')[1] == receipt
        assert smtp() == {}
        assert int(python('''
with factory() as db:
    query = select(func.count()).select_from(RawMessage).where(RawMessage.state == 'PENDING')
    print(db.scalar(query))
''')) == 2
        run('start', 'redis')
        run('start', 'ingestion')
        for _ in range(50):
            pending = int(python('''
with factory() as db:
    query = select(func.count()).select_from(RawMessage).where(RawMessage.state == 'PENDING')
    print(db.scalar(query))
'''))
            if pending == 0:
                break
            time.sleep(1)
        assert pending == 0
        print('Приём без Redis и восстановление обработки после перезапуска: пройдено.', flush=True)

        python('''
for n in range(256):
    (settings.storage_root / f"{n:02x}").chmod(0o500)
''')
        assert post(b'not-confirmed', 'disk-failure')[0] == 503
        try:
            smtp()
            raise AssertionError('SMTP_CONFIRMED_FAILED_WRITE')
        except smtplib.SMTPDataError as error:
            assert error.smtp_code == 451
        print('Отказ диска: приём не подтверждён по REST и SMTP.', flush=True)
    finally:
        python('''
for n in range(256):
    (settings.storage_root / f"{n:02x}").chmod(0o700)
''')
        run('start', 'redis', 'ingestion')

    try:
        run('stop', 'postgres')
        assert post(b'not-confirmed', 'database-failure')[0] == 503
        try:
            smtp()
            raise AssertionError('SMTP_CONFIRMED_DATABASE_FAILURE')
        except smtplib.SMTPDataError as error:
            assert error.smtp_code == 451
        print('Отказ базы данных: успешное подтверждение не выдаётся.', flush=True)
    finally:
        run('start', 'postgres')
    for _ in range(50):
        try:
            with urllib.request.urlopen(base + '/health/ready', timeout=5) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError('INGESTION_RECOVERY_FAILED')
    print('Все компоненты восстановили готовность.', flush=True)
