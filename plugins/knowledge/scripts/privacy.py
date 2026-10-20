"""The secret pattern. No sibling import: scripts/vault_plan.py loads this file by path."""
import re

SENSITIVE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[a-z]-[\w-]{10,}|\bsk-[\w-]{20,}"
    r"|(?i:api[_-]?key)\s*[:=]\s*\S{16,}|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"
    # Private key header, including PGP blocks.
    r"|-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
    # JWT: two base64url JSON segments and a signature.
    r"|\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}"
    r"|(?i:bearer)\s+[\w.~+/-]{20,}=*"
    # URL or DSN with a password.
    r"|\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"
    r"|(?i:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}",
)
