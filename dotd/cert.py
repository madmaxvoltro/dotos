"""The RDP server's TLS certificate, fetched by Dot itself before any password is sent.

RDP starts with a short X.224 negotiation and only then switches to TLS, so this
sends that negotiation, completes a TLS handshake, and reads the certificate.
Windows uses a self-signed certificate unless someone set one up from a trusted
authority, so most home and office servers come back "not verified".
"""

import hashlib
import os
import socket
import ssl
import tempfile

from . import system

# TPKT + X.224 Connection Request + RDP_NEG_REQ asking for TLS and CredSSP (PROTOCOL_SSL | PROTOCOL_HYBRID)
NEG_REQUEST = bytes.fromhex("030000130ee000000000000100080003000000")


def _negotiate(host, port, timeout):
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    sock.sendall(NEG_REQUEST)
    resp = sock.recv(19)
    # byte 11: 0x02 = negotiation response (TLS allowed), 0x03 = failure (server only speaks plain RDP)
    if len(resp) < 19 or resp[11] != 0x02:
        sock.close()
        raise ConnectionError("the server doesn't offer TLS")
    return sock


def _describe(der):
    """Subject, issuer and expiry from DER, without a verified connection."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        info = ssl._ssl._test_decode_cert(path)  # CPython's own decoder
    except Exception:
        return {}
    finally:
        os.remove(path)

    def name(seq):
        parts = dict(x[0] for x in seq or ())
        return parts.get("commonName") or parts.get("organizationName") or ""

    return {"subject": name(info.get("subject")), "issuer": name(info.get("issuer")), "expires": info.get("notAfter", "")}


def fetch(host, port=3389, timeout=6, check_trust=True):
    """{fingerprint, fingerprintColons, subject, issuer, expires, selfSigned, trusted} or {"error": ...}."""
    if system.DEV:
        if "trusted" in host:
            return {"fingerprint": "ab" * 32, "fingerprintColons": ":".join(["AB"] * 32), "subject": host,
                    "issuer": "Contoso Root CA", "expires": "Mar 1 00:00:00 2027 GMT", "selfSigned": False, "trusted": True}
        fp = hashlib.sha256(host.encode()).hexdigest()
        return {"fingerprint": fp, "fingerprintColons": ":".join(fp[i:i + 2] for i in range(0, 64, 2)).upper(),
                "subject": host.split(".")[0].upper(), "issuer": host.split(".")[0].upper(),
                "expires": "Apr 12 08:00:00 2027 GMT", "selfSigned": True, "trusted": False}
    # 1. the certificate itself (no verification yet)
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(_negotiate(host, port, timeout), server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError, ConnectionError) as e:
        return {"error": str(e)}
    fp = hashlib.sha256(der).hexdigest()
    info = _describe(der)
    # 2. would a normal, verifying client accept it? (a certificate from a trusted authority, for this name)
    #    Skipped when Dot already knows which certificate to expect: one handshake instead of two.
    trusted = False
    try:
        if not check_trust:
            raise ConnectionError("not needed")
        with ssl.create_default_context().wrap_socket(_negotiate(host, port, timeout), server_hostname=host):
            trusted = True
    except (OSError, ssl.SSLError, ConnectionError):
        pass
    return {
        "fingerprint": fp,
        "fingerprintColons": ":".join(fp[i:i + 2] for i in range(0, 64, 2)).upper(),
        **info,
        "selfSigned": bool(info) and info.get("subject") == info.get("issuer"),
        "trusted": trusted,
    }
