"""Read-only public smoke check. Never uses credentials or creates user data."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def verify(base_url):
    checks = []
    routes = [
        ("/", 200),
        ("/login", 200),
        ("/api/health", 200),
        ("/api/markets?limit=1", 200),
        ("/api/auth/me", 401),
        ("/api/baskets", 401),
    ]
    for path, expected in routes:
        try:
            request = Request(base_url + path, headers={"User-Agent": "Basket-deployment-check"})
            with urlopen(request, timeout=20) as response:
                status, body = response.status, response.read(65536)
        except HTTPError as error:
            status, body = error.code, error.read(65536)
        except (URLError, TimeoutError):
            checks.append({"path": path, "ok": False, "error": "Connection or TLS check failed"})
            continue
        ok = status == expected
        if ok and path == "/api/health":
            try:
                ok = json.loads(body).get("status") == "ok"
            except (ValueError, AttributeError):
                ok = False
        checks.append({"path": path, "status": status, "expected": expected, "ok": ok})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public HTTPS origin, without a path")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        parser.error("Use an HTTPS origin without credentials, query, fragment, or path")
    checks = verify(args.url.rstrip("/"))
    print(json.dumps({"origin": args.url.rstrip("/"), "checks": checks}, indent=2))
    raise SystemExit(0 if all(check["ok"] for check in checks) else 1)


if __name__ == "__main__":
    main()
