import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse
from http import HTTPStatus

MAX_IMAGE_BYTES = 20 * 1024 * 1024
class AdapterError(RuntimeError):
    pass

def image_bytes(reference: str) -> bytes:
    """Read a bounded public HTTPS image; pin DNS and never follow redirects."""
    connection = None
    try:
        parsed = urllib.parse.urlsplit(reference)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment):
            raise ValueError("invalid image URL")
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("non-public image address")
        # Connect to the validated numeric address, retaining the original TLS hostname.
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=25, context=context)
        raw_socket = socket.create_connection((addresses[0][4][0], 443), timeout=25)
        try:
            connection.sock = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
        except Exception:
            raw_socket.close()
            raise
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request("GET", path, headers={"Accept": "image/png,image/jpeg,image/webp"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("image fetch did not return 200")
        length = response.getheader("Content-Length")
        if length is not None and not 0 < int(length) <= MAX_IMAGE_BYTES:
            raise ValueError("image length outside limit")
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp", "application/octet-stream"}:
            raise ValueError("unexpected image content type")
        deadline = time.monotonic() + 25
        chunks = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("image download deadline")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_IMAGE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise ValueError("image too large")
            chunks.append(chunk)
        image = b"".join(chunks)
        if not (image.startswith(b"\x89PNG\r\n\x1a\n") or image.startswith(b"\xff\xd8\xff")
                or (image.startswith(b"RIFF") and image[8:12] == b"WEBP")):
            raise ValueError("invalid image bytes")
        return image
    except (ValueError, OSError, http.client.HTTPException) as error:
        raise AdapterError(HTTPStatus.BAD_GATEWAY, "image_download_failed", "generated image could not be retrieved") from error
    finally:
        if connection is not None:
            connection.close()

