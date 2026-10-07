"""Certificate validation, staged renewal and rollback without network I/O."""

from pathlib import Path
import os
import subprocess

import pytest

from api import tls_bootstrap as tls


def test_private_key_is_restricted_at_creation_and_never_overwrites(monkeypatch):
    path = Path("private.tmp")
    original_open = os.open
    observed = []

    def inspect_open(name, flags, mode=0o777):
        descriptor = original_open(name, flags, mode)
        observed.append((flags, mode))
        if os.name == "posix":
            assert os.fstat(descriptor).st_mode & 0o777 == 0o600
            assert os.fstat(descriptor).st_size == 0
        return descriptor

    monkeypatch.setattr(os, "open", inspect_open)
    tls._write_private_key(path, b"test key")
    assert observed and observed[0][0] & os.O_EXCL and observed[0][1] == 0o600
    assert path.read_bytes() == b"test key"
    with pytest.raises(FileExistsError):
        tls._write_private_key(path, b"replacement")
    assert path.read_bytes() == b"test key"


def test_explicit_addresses_and_matching_pair():
    address, cert, key = tls.ensure_cert(["localhost", "127.0.0.1", "::1"])
    assert address == "localhost"
    from cryptography import x509

    certificate = x509.load_pem_x509_certificate(Path(cert).read_bytes())
    assert not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert tls.ensure_cert(["localhost"], cert, key) == (address, cert, key)
    saved_cert = Path("first-cert.pem")
    saved_cert.write_bytes(Path(cert).read_bytes())
    _, other_cert, other_key = tls.ensure_cert(["game.example"])
    assert other_cert == cert and other_key == key
    assert (Path(cert), Path(key)) == (tls.CERT_PATH, tls.KEY_PATH)
    with pytest.raises(ValueError, match="do not match"):
        tls.ensure_cert(["localhost"], str(saved_cert), other_key)
    with pytest.raises(ValueError, match="does not cover"):
        tls.ensure_cert(["other.example"], cert, key)


@pytest.mark.parametrize("failed_target", ["cert.pem", "key.pem"])
@pytest.mark.parametrize("existing", [False, True])
def test_failed_pair_publication_keeps_previous_pair(monkeypatch, failed_target, existing):
    first = tls.ensure_cert(["localhost"]) if existing else None
    before = {path: path.read_bytes() for path in (tls.CERT_PATH, tls.KEY_PATH)} if existing else {}
    replace = Path.replace
    failed = False

    def fail_replacement(path, target):
        nonlocal failed
        if Path(target).name == failed_target and not failed:
            failed = True
            raise OSError("injected disk failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_replacement)
    with pytest.raises(OSError, match="injected"):
        tls.ensure_cert(["game.example"])
    if existing:
        assert all(path.read_bytes() == content for path, content in before.items())
        assert tls.ensure_cert(["localhost"]) == first
    else:
        assert not tls.CERT_PATH.exists() and not tls.KEY_PATH.exists()
    assert not list(tls.CERT_DIR.glob("*.tmp"))
    assert not any(path.is_dir() for path in tls.CERT_DIR.iterdir())


def test_corrupt_generated_pair_recovers_but_supplied_pair_is_untouched():
    _, cert, key = tls.ensure_cert(["localhost"])
    Path(key).write_bytes(b"invalid key")
    with pytest.raises(ValueError):
        tls.ensure_cert(["localhost"], cert, key)
    assert Path(key).read_bytes() == b"invalid key"
    assert tls.ensure_cert(["localhost"])[2] == key
    assert Path(key).read_bytes() != b"invalid key"
    tls._validate_pair(Path(cert), Path(key), ["localhost"])


def test_offline_address_fallback(monkeypatch):
    import urllib.request

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(tls.socket, "socket", unavailable)
    monkeypatch.setattr(tls.socket, "gethostbyname", unavailable)
    assert tls.get_external_ip() == "127.0.0.1"


def test_generated_certificate_paths_are_ignored_by_repository_rules(tmp_path):
    ignore = Path(__file__).resolve().parents[1] / ".gitignore"
    (tmp_path / ".gitignore").write_bytes(ignore.read_bytes())
    _, cert, key = tls.ensure_cert(["localhost", "127.0.0.1"])
    subprocess.run(["git", "init", "--quiet"], check=True, cwd=tmp_path)
    paths = [cert, key, "certs/cert-example.tmp", "certs/key-example.tmp"]
    result = subprocess.run(
        ["git", "check-ignore", "--", *paths],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert len(result.stdout.splitlines()) == len(paths)
    subprocess.run(["git", "add", "."], check=True, cwd=tmp_path, capture_output=True)
    tracked = subprocess.run(
        ["git", "ls-files"], check=True, capture_output=True, text=True, cwd=tmp_path
    ).stdout
    assert "certs/" not in tracked
