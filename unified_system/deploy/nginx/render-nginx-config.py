#!/usr/bin/env python3
"""Render the reviewed host Nginx template without evaluating arbitrary shell input."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
MIN_CERT_VALIDITY_SECONDS = 14 * 24 * 60 * 60


class ConfigError(ValueError):
    pass


def validate_domain(value: str) -> str:
    if len(value) > 253 or not DOMAIN_RE.fullmatch(value):
        raise ConfigError("PUBLIC_DOMAIN must be a lowercase ASCII FQDN (IDNs must use punycode)")
    return value


def validate_absolute_path(value: str, label: str) -> Path:
    path = Path(value)
    if (
        len(value) > 4096
        or not PATH_RE.fullmatch(value)
        or ".." in path.parts
        or "${" in value
    ):
        raise ConfigError(f"{label} must be an absolute path without whitespace or parent traversal")
    return path


def _openssl(*args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        if input_bytes is None:
            result = subprocess.run(
                ["openssl", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
        else:
            result = subprocess.run(
                ["openssl", *args], input=input_bytes, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
    except FileNotFoundError as exc:
        raise ConfigError("OpenSSL is required to validate the HTTPS certificate and key") from exc
    if result.returncode != 0:
        raise ConfigError("OpenSSL certificate/key validation failed; no private-key data was emitted")
    return result.stdout


def validate_tls_files(domain: str, fullchain: Path, private_key: Path) -> None:
    if os.geteuid() != 0:
        raise ConfigError("Render HTTPS configuration as root so private-key ownership and mode can be checked")
    if not fullchain.is_file() or not private_key.is_file():
        raise ConfigError("The configured certificate chain and private-key files must already exist")
    key_stat = private_key.stat()
    if key_stat.st_uid != 0 or stat.S_IMODE(key_stat.st_mode) & 0o077:
        raise ConfigError("The TLS private key must be root-owned and unreadable by group/other")

    _openssl("x509", "-in", str(fullchain), "-noout", "-checkhost", domain)
    _openssl("x509", "-in", str(fullchain), "-noout", "-checkend", str(MIN_CERT_VALIDITY_SECONDS))

    cert_public_pem = _openssl("x509", "-in", str(fullchain), "-pubkey", "-noout")
    cert_public_der = _openssl("pkey", "-pubin", "-outform", "DER", input_bytes=cert_public_pem)
    key_public_der = _openssl("pkey", "-in", str(private_key), "-pubout", "-outform", "DER")
    if not hmac.compare_digest(hashlib.sha256(cert_public_der).digest(), hashlib.sha256(key_public_der).digest()):
        raise ConfigError("The TLS certificate and private key do not match")


def render_config(
    mode: str,
    environ: dict[str, str] | None = None,
    enable_hsts: bool = False,
) -> str:
    env = os.environ if environ is None else environ
    domain = validate_domain(env.get("PUBLIC_DOMAIN", ""))
    if enable_hsts and mode != "https":
        raise ConfigError("HSTS can only be rendered for the HTTPS template")
    replacements = {
        "${PUBLIC_DOMAIN}": domain,
        "${HSTS_HEADER}": 'add_header Strict-Transport-Security "max-age=15552000" always;'
        if enable_hsts else "# HSTS withheld: enable only after public HTTPS is verified.",
    }
    if mode == "https":
        fullchain = validate_absolute_path(env.get("TLS_FULLCHAIN_PATH", ""), "TLS_FULLCHAIN_PATH")
        private_key = validate_absolute_path(env.get("TLS_PRIVATE_KEY_PATH", ""), "TLS_PRIVATE_KEY_PATH")
        validate_tls_files(domain, fullchain, private_key)
        replacements.update({
            "${TLS_FULLCHAIN_PATH}": str(fullchain),
            "${TLS_PRIVATE_KEY_PATH}": str(private_key),
        })
        template_path = HERE / "unified-learning.conf.template"
    elif mode == "http":
        template_path = HERE / "unified-learning-http.conf.template"
    else:
        raise ConfigError("mode must be http or https")

    rendered = template_path.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    if re.search(r"\$\{[A-Z][A-Z0-9_]*\}", rendered):
        raise ConfigError("The rendered configuration still contains an unresolved template placeholder")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("http", "https"), help="http=temporary ACME bootstrap; https=production site")
    parser.add_argument(
        "--enable-hsts", action="store_true",
        help="emit HSTS only after the real domain and public HTTPS have been independently verified",
    )
    args = parser.parse_args()
    try:
        sys.stdout.write(render_config(args.mode, enable_hsts=args.enable_hsts))
    except (ConfigError, OSError) as exc:
        print(f"Nginx config rendering failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
