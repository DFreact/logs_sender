"""Disposable capacity experiment. Never targets an existing installation."""

import argparse
import concurrent.futures
import http.cookiejar
import json
import math
import os
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

SEED = '''
import json
from sqlalchemy import text
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID
from app.api.schemas import UserCreate
from app.security.bootstrap import create_first_admin
from app.services.ingestion import issue_key

engine = build_engine(Settings())
factory = build_session_factory(engine)
with factory.begin() as db:
    create_first_admin(
        db, UserCreate(username="loadadmin", display_name="Проверка нагрузки", password=password)
    )
with factory.begin() as db:
    _, token = issue_key(db, "Проверка нагрузки")
with engine.begin() as db:
    db.execute(
        text("""INSERT INTO sources (id,team_id,name,description,enabled,version)
        SELECT md5('source-' || n)::uuid, :team, 'Источник ' || n, '', true, 1
        FROM generate_series(0,63) n"""),
        {"team": DEFAULT_TEAM_ID},
    )
    db.execute(
        text("""INSERT INTO events
        (id,team_id,source_id,input_adapter,subject,sender,body,status,severity,
         category,event_type,tags,received_at,first_seen_at,last_seen_at,occurrence_count,version)
        SELECT md5('event-' || n)::uuid, :team, md5('source-' || n%64)::uuid, 'REST',
          CASE WHEN n%10000=0 THEN 'Редкая сигнатура ' ELSE 'Событие ' END || n,
          'sender' || n%100 || '@example.invalid', repeat(md5(n::text), 8),
          CASE WHEN n%10=0 THEN 'RESOLVED' ELSE 'NEW' END,
          CASE WHEN n%20=0 THEN 'CRITICAL' ELSE 'INFO' END,
          '', '', '[]', now()-n*interval '1 second', now()-n*interval '1 second',
          now()-n*interval '1 second',1,1 FROM generate_series(1,:rows) n"""),
        {"team": DEFAULT_TEAM_ID, "rows": rows},
    )
    db.execute(text("ANALYZE events"))
print(json.dumps({"token": token}))
engine.dispose()
'''

MEASURE = """
import json, statistics, time
from sqlalchemy import select, func, tuple_, text
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, Event, Source
from app.api.events import event_page_query

engine = build_engine(Settings())
factory = build_session_factory(engine)
with factory() as db:
    position = db.execute(
        select(Event.received_at, Event.id)
        .order_by(Event.received_at.desc(), Event.id.desc())
        .offset(int(rows * 0.9))
        .limit(1)
    ).one()
    base = [Event.team_id == DEFAULT_TEAM_ID]
    queries = {
        "first_page": event_page_query(base, 26),
        "deep_cursor": event_page_query(
            [*base, tuple_(Event.received_at, Event.id) < tuple(position)], 26
        ),
        "source_filter": event_page_query(
            [*base, Event.source_id == db.scalar(select(Source.id).limit(1))], 26
        ),
        "critical_filter": event_page_query([*base, Event.severity == "CRITICAL"], 26),
        "substring_search": event_page_query(
            [
                *base,
                Event.subject.ilike("%Редкая сигнатура%")
                | Event.sender.ilike("%Редкая сигнатура%"),
            ],
            26,
        ),
        "old_deep_offset": select(Event)
        .where(*base)
        .order_by(Event.received_at.desc(), Event.id.desc())
        .offset(int(rows * 0.9))
        .limit(25),
        "old_exact_count": select(func.count()).select_from(Event).where(*base),
    }
    result = {}
    for name, query in queries.items():
        timings = []
        for _ in range(12):
            start = time.perf_counter()
            db.execute(query).all()
            timings.append((time.perf_counter() - start) * 1000)
        sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
        plan = db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)).scalar()[0]
        result[name] = {
            "p50_ms": round(statistics.median(timings), 2),
            "p95_ms": round(sorted(timings)[-1], 2),
            "plan": plan,
        }
    result["database_bytes"] = db.scalar(text("SELECT pg_database_size(current_database())"))
print(json.dumps(result, default=str))
engine.dispose()
"""


def summary(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": round(values[len(values) // 2], 2),
        "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1], 2),
        "max_ms": round(values[-1], 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--messages", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    assert 1000 <= args.rows <= 2000000 and 1 <= args.messages <= 10000
    assert 1 <= args.concurrency <= 32
    project = "eventhub-load-" + uuid4().hex[:8]
    env = {
        **os.environ,
        "HUB_HTTP_PORT": "8082",
        "HUB_SMTP_PORT": "2527",
        "HUB_DOCKER_SUBNET": "172.31.53.0/24",
        "HUB_INGEST_PER_MINUTE": "600000",
        "HUB_INGESTION_THREADS": "8",
        "HUB_DB_POOL_SIZE": "10",
        "HUB_DB_MAX_OVERFLOW": "5",
        "HUB_BACKGROUND_POLL_SECONDS": "0.2",
        "HUB_WORK_BATCH_SIZE": "200",
    }
    base = ["docker", "compose", "-p", project, "-f", "infra/compose/compose.yaml"]

    def run(*args, input_text=None):
        result = subprocess.run(
            [*base, *args],
            cwd=ROOT,
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("LOAD_COMMAND_FAILED:" + args[0])
        return result.stdout

    def python(code):
        return run("exec", "-T", "api", "python", "-", input_text=code)

    url = "http://127.0.0.1:8082"
    report = {
        "version": "0.11.0",
        "archive_rows": args.rows,
        "body_bytes": 256,
        "messages": args.messages,
        "concurrency": args.concurrency,
        "ingestion_threads": 8,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    resources = subprocess.run(
        ["docker", "info", "--format", "{{json .NCPU}} {{json .MemTotal}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    report["docker_cpus"], report["docker_memory_bytes"] = map(int, resources)
    try:
        print("Запуск отдельного нагрузочного стенда…", flush=True)
        run("up", "-d", "--no-build", "--wait")
        password = secrets.token_urlsafe(32)
        print(f"Создание архива: {args.rows} событий…", flush=True)
        seeded = json.loads(
            python("rows = " + str(args.rows) + "\npassword = " + repr(password) + "\n" + SEED)
        )
        report["queries"] = json.loads(python("rows = " + str(args.rows) + "\n" + MEASURE))
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

        def request(path, data=None, headers=None):
            req = urllib.request.Request(
                url + path,
                data=json.dumps(data).encode() if data else None,
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            with opener.open(req, timeout=30) as response:
                return json.load(response)

        csrf = request("/api/v1/auth/csrf")["csrf_token"]
        request(
            "/api/v1/auth/login",
            {"username": "loadadmin", "password": password},
            {"Origin": url, "X-CSRF-Token": csrf},
        )
        timings, cursor, seen = [], "", set()
        for _ in range(40):
            start = time.perf_counter()
            page = request("/api/v1/events?limit=25" + ("&cursor=" + cursor if cursor else ""))
            timings.append((time.perf_counter() - start) * 1000)
            for item in page["items"]:
                assert item["id"] not in seen
                seen.add(item["id"])
            cursor = page["next_cursor"]
        report["http_pages"] = summary(timings)
        print("Архив и последовательные страницы проверены. Запуск потока приёма…", flush=True)

        def ingest(index):
            body = json.dumps(
                {"subject": "Нагрузка " + str(index), "body": "Проверка сохранности"}
            ).encode()
            req = urllib.request.Request(
                url + "/api/v1/ingest",
                data=body,
                headers={
                    "Authorization": "Bearer " + seeded["token"],
                    "Content-Type": "application/json",
                },
            )
            start = time.perf_counter()
            with urllib.request.urlopen(req, timeout=30) as response:
                assert response.status == 202
                receipt = json.load(response)["receipt_id"]
            return receipt, (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(args.concurrency) as pool:
            futures = [pool.submit(ingest, i) for i in range(args.messages)]
            concurrent_pages = []
            while not all(f.done() for f in futures):
                tick = time.perf_counter()
                request("/api/v1/events?limit=25")
                concurrent_pages.append((time.perf_counter() - tick) * 1000)
                time.sleep(0.1)
            results = [f.result() for f in futures]
        accepted_at = time.perf_counter()
        ids = [r[0] for r in results]
        assert len(set(ids)) == args.messages
        deadline = time.monotonic() + 180
        while True:
            counts = json.loads(
                python("""
import json
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine
from app.persistence.models import RawMessage, EventOccurrence, WorkItem

engine = build_engine(Settings())
with engine.connect() as db:
    print(
        json.dumps(
            {
                "raw": db.scalar(select(func.count()).select_from(RawMessage)),
                "occurrences": db.scalar(select(func.count()).select_from(EventOccurrence)),
                "failed": db.scalar(
                    select(func.count()).select_from(WorkItem).where(WorkItem.state == "FAILED")
                ),
            }
        )
    )
engine.dispose()
""")
            )
            assert counts["failed"] == 0
            if counts["occurrences"] == args.messages:
                break
            assert time.monotonic() < deadline, "NORMALIZATION_DID_NOT_DRAIN"
            time.sleep(1)
        assert counts["raw"] == args.messages
        report.update(
            {
                "ingest_http": summary([r[1] for r in results]),
                "http_pages_during_ingest": summary(concurrent_pages),
                "accepted_per_second": round(args.messages / (accepted_at - start), 2),
                "completed_per_second": round(args.messages / (time.perf_counter() - start), 2),
                "verified_counts": counts,
            }
        )
        report["containers"] = [
            json.loads(line)
            for line in subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    *run("ps", "-q").split(),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        ]
        target = ROOT / "docs/load-measurements.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(
            "Измерения сохранены в docs/load-measurements.json. Все сообщения обработаны.",
            flush=True,
        )
    finally:
        run("down", "--volumes", "--remove-orphans")
        print("Нагрузочный стенд и его данные удалены.", flush=True)


if __name__ == "__main__":
    main()
