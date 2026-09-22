"""Fetch public inspection inputs without granting access to workstation services."""
import ipaddress
import socket
import time

import httpx

from geodata_common.inspection import validate

MAX_BYTES = 100 * 1024 * 1024


def download(url, path):
    """Validate every redirect and connect to the checked IP, preserving TLS SNI."""
    deadline = time.monotonic() + 120
    for _ in range(6):
        validate({"url": url})
        target = httpx.URL(url).copy_with(fragment=None)
        addresses = {row[4][0] for row in socket.getaddrinfo(
            target.host, target.port or (443 if target.scheme == "https" else 80),
            type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError("inspection URL must resolve only to public internet addresses")
        address = sorted(addresses, key=lambda a: (":" in a, a))[0]
        # A fresh connection per hop prevents sharing TLS sessions across hostnames.
        with httpx.Client(timeout=60, trust_env=False) as client:
            with client.stream("GET", target.copy_with(host=address),
                               headers={"Host": target.netloc.decode(),
                                        "User-Agent": "Geodata-MCP/1.0 document inspection"},
                               extensions={"sni_hostname": target.host}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("document redirect is missing its destination")
                    url = str(target.join(location))
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"document download failed (HTTP {response.status_code})")
                total = 0
                with open(path, "wb") as output:
                    for chunk in response.iter_bytes(65536):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("inspection input exceeds 100 MiB")
                        if time.monotonic() > deadline:
                            raise RuntimeError("document download exceeded 120 seconds")
                        output.write(chunk)
                return str(target), total
    raise ValueError("too many document redirects")
