"""Self-signed TLS certificate bootstrap for IP-based server discovery."""

import datetime
import ipaddress
import logging
import os
import socket
import re
from uuid import uuid4
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = Path("./certs")
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"
# comfortably under CA/Browser Forum's 825-day cap, irrelevant for self-signed but conventional
VALIDITY_DAYS = 825
REGEN_THRESHOLD_DAYS = 30  # regenerate if cert expires soon

logger = logging.getLogger(__name__)


def get_external_ip() -> str:
    """Best-effort external IP detection. Falls back to local IP if offline."""
    import urllib.request

    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                ip = r.read().decode().strip()
                ipaddress.ip_address(ip)  # validate
                return ip
        except Exception:
            continue
    # Fallback: local interface IP (works for LAN play, not internet-facing)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as local:
            local.connect(("1.1.1.1", 80))
            return local.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def _address(value: str):
    try:
        return x509.IPAddress(ipaddress.ip_address(value))
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise ValueError("TLS addresses must be IP addresses or ASCII hostnames.")
        if any(
            not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
            for part in value.split(".")
        ):
            raise ValueError("Invalid TLS hostname.")
        return x509.DNSName(value.lower())


def _validate_pair(cert_path: Path, key_path: Path, addresses: list[str]) -> None:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        now = datetime.datetime.now(datetime.timezone.utc)
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now + datetime.timedelta(
            days=REGEN_THRESHOLD_DAYS
        ):
            raise ValueError("TLS certificate is not currently valid or expires within 30 days.")

        def public_bytes(public):
            return public.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )

        if public_bytes(cert.public_key()) != public_bytes(key.public_key()):
            raise ValueError("TLS certificate and private key do not match.")
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for address in addresses:
            requested = _address(address)
            if requested not in names:
                if isinstance(requested, x509.DNSName) and any(
                    name.startswith("*.")
                    and requested.value.split(".", 1)[-1] == name[2:]
                    and len(requested.value.split(".")) == len(name.split("."))
                    for name in names.get_values_for_type(x509.DNSName)
                ):
                    continue
                raise ValueError("TLS certificate does not cover a requested address.")
    except (OSError, TypeError, x509.ExtensionNotFound) as exc:
        raise ValueError(
            "TLS certificate/key files cannot be loaded or lack address coverage."
        ) from exc


def _write_private_key(path: Path, content: bytes) -> None:
    """Create a new key file with owner-only permissions before writing any bytes."""
    with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(content)


def _generate_cert(ip: str, addresses: list[str] | None = None) -> tuple[Path, Path]:
    """Generate a self-signed certificate and key covering the given IP."""
    CERT_DIR.mkdir(exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, ip),
        ]
    )

    san_entries = list(
        dict.fromkeys(
            _address(value) for value in [*(addresses or [ip]), "127.0.0.1", "::1", "localhost"]
        )
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=VALIDITY_DAYS)
        )
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    transaction = uuid4().hex
    cert_path = CERT_DIR / f"cert-{transaction}.tmp"
    key_path = CERT_DIR / f"key-{transaction}.tmp"
    temporary = [cert_path, key_path]
    published = []
    previous = {}
    try:
        # Save only the files we own; explicitly supplied pairs never enter here.
        for target in (CERT_PATH, KEY_PATH):
            previous[target] = target.read_bytes() if target.exists() else None
        _write_private_key(
            key_path,
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        _validate_pair(cert_path, key_path, addresses or [ip])
        for source, target in ((cert_path, CERT_PATH), (key_path, KEY_PATH)):
            source.replace(target)
            published.append(target)
    except BaseException:
        # A failed second replacement must not leave a new cert with the old key.
        for target in reversed(published):
            content = previous[target]
            if content is None:
                target.unlink(missing_ok=True)
            else:
                rollback = CERT_DIR / f"{target.stem}-rollback-{transaction}.tmp"
                temporary.append(rollback)
                if target == KEY_PATH:
                    _write_private_key(rollback, content)
                else:
                    rollback.write_bytes(content)
                rollback.replace(target)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return CERT_PATH, KEY_PATH


def ensure_cert(
    addresses: list[str] | None = None, cert_path: str | None = None, key_path: str | None = None
) -> tuple[str, str, str]:
    """Returns (external_ip, cert_path, key_path), regenerating the cert if needed."""
    addresses = list(dict.fromkeys(addresses or [get_external_ip()]))
    for value in addresses:
        _address(value)
    ip = addresses[0]
    if bool(cert_path) != bool(key_path):
        raise ValueError("Supply both TLS certificate and key paths.")
    if cert_path and key_path:
        _validate_pair(Path(cert_path), Path(key_path), addresses)
        return ip, cert_path, key_path
    cert, key = CERT_PATH, KEY_PATH
    try:
        _validate_pair(cert, key, addresses)
    except ValueError:
        logger.info(
            "TLS certificate for %s is missing, expiring soon, or does not cover the IP; renewing",
            ip,
        )
        cert, key = _generate_cert(ip, addresses)
    else:
        logger.debug("TLS certificate is valid for %s", ip)
    return ip, str(cert), str(key)
