"""Isolated renderer. No imports of application/settings/database modules."""

import json
import resource
import sys

resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

import nh3  # noqa: E402
from jinja2 import StrictUndefined, nodes  # noqa: E402
from jinja2.sandbox import ImmutableSandboxedEnvironment  # noqa: E402

ALLOWED = (
    nodes.Template,
    nodes.Output,
    nodes.TemplateData,
    nodes.Name,
    nodes.Getattr,
    nodes.Filter,
    nodes.Const,
)
FIELDS = {"subject", "body", "sender", "source", "severity", "category", "event_type"}


def render_one(source, event, html=False):
    if not isinstance(source, str) or len(source.encode()) > 32768:
        raise ValueError()
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=html)
    env.globals.clear()
    env.tests.clear()
    env.filters = {k: env.filters[k] for k in ("upper", "lower", "trim", "default")}
    tree = env.parse(source)
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if type(node) not in ALLOWED or depth > 12 or count > 500:
            raise ValueError()
        if isinstance(node, nodes.Name) and node.name != "event":
            raise ValueError()
        if isinstance(node, nodes.Getattr) and (
            not isinstance(node.node, nodes.Name) or node.attr not in FIELDS
        ):
            raise ValueError()
        if isinstance(node, nodes.Filter) and (
            node.name not in env.filters or node.kwargs or node.dyn_args or node.dyn_kwargs
        ):
            raise ValueError()
        if isinstance(node, nodes.Const) and not isinstance(node.value, (str, bool)):
            raise ValueError()
        pending.extend((child, depth + 1) for child in node.iter_child_nodes())
    chunks, size = [], 0
    for chunk in env.from_string(source).generate(event=event):
        size += len(chunk.encode())
        if size > 65536:
            raise ValueError()
        chunks.append(chunk)
    output = "".join(chunks)
    if html:
        output = nh3.clean(
            output,
            tags={
                "p",
                "br",
                "b",
                "strong",
                "i",
                "em",
                "ul",
                "ol",
                "li",
                "pre",
                "code",
                "table",
                "tr",
                "td",
                "th",
                "thead",
                "tbody",
            },
            attributes={},
            link_rel=None,
        )
    return output


def main():
    data = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
    t, event = data["template"], data["event"]
    subject = render_one(t["subject"], event)
    if any(ord(c) < 32 or ord(c) == 127 for c in subject) or len(subject.encode()) > 998:
        raise ValueError()
    body = render_one(t["body"], event)
    html = render_one(t["html"], event, True)
    if t["kind"] == "TELEGRAM" and (not body or len(body.encode("utf-16-le")) // 2 > 4096):
        raise ValueError()
    if t["kind"] == "MAX" and (not body or len(body.encode("utf-16-le")) // 2 > 4000):
        raise ValueError()
    if t["kind"] not in ("SMTP", "TELEGRAM", "MAX", "WEBHOOK"):
        raise ValueError()
    payload = (
        json.dumps({"subject": subject, "message": body}, ensure_ascii=False)
        if t["kind"] == "WEBHOOK"
        else ""
    )
    if sum(len(s.encode()) for s in (subject, body, html, payload)) > 65536:
        raise ValueError()
    sys.stdout.write(
        json.dumps({"subject": subject, "body": body, "html": html, "payload": payload})
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
