"""Bound DNS lookup independently of the application thread and environment."""

import json
import resource
import socket
import sys

resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
try:
    addresses = sorted(
        {
            r[4][0]
            for r in socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)
        }
    )
    if not addresses or len(addresses) > 16:
        raise ValueError()
    sys.stdout.write(json.dumps(addresses))
except Exception:
    sys.exit(1)
