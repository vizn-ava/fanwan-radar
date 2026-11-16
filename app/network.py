"""Reuse a verified TLS context instead of loading certificates per request."""
from functools import lru_cache
import os
import ssl

import certifi


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    # Certificate verification and hostname checks remain enabled. Explicit
    # certificate configuration is honored; no verify=False performance shortcut.
    return ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or certifi.where(),
                                      capath=os.getenv('SSL_CERT_DIR') or None)
