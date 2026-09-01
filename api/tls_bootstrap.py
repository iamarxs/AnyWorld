# tls_bootstrap.py
import datetime
import ipaddress
import logging
import socket
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
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _cert_covers_ip_and_is_fresh(ip: str) -> bool:
    if not (CERT_PATH.exists() and KEY_PATH.exists()):
        return False
    cert = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
    if cert.not_valid_after_utc < datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=REGEN_THRESHOLD_DAYS
    ):
        return False
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips_in_cert = san.get_values_for_type(x509.IPAddress)
        return ipaddress.ip_address(ip) in ips_in_cert
    except x509.ExtensionNotFound:
        return False


def _generate_cert(ip: str) -> None:
    CERT_DIR.mkdir(exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, ip),
        ]
    )

    san_entries = [
        x509.IPAddress(ipaddress.ip_address(ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]

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
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ensure_cert() -> tuple[str, str, str]:
    """Returns (external_ip, cert_path, key_path), regenerating the cert if needed."""
    ip = get_external_ip()
    if not _cert_covers_ip_and_is_fresh(ip):
        logger.info(
            "TLS certificate for %s is missing, expiring soon, or does not cover the IP; renewing",
            ip,
        )
        _generate_cert(ip)
    else:
        logger.debug("TLS certificate is valid for %s", ip)
    return ip, str(CERT_PATH), str(KEY_PATH)
