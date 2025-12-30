import hmac
import hashlib
import json
from typing import Mapping, Any
from config import settings


def verify_whatsapp_signature(headers: Mapping[str, str], payload: Any) -> bool:
    """Verify Meta/WhatsApp X-Hub-Signature-256 if enabled.

    Returns True when either verification is disabled (default) or the signature matches.
    Non-breaking: falls back to True when secret is missing or headers absent and the feature flag is off.
    """
    if not getattr(settings, 'ENABLE_WHATSAPP_SIGNATURE_VERIFICATION', False):
        return True

    app_secret = (getattr(settings, 'WHATSAPP_APP_SECRET', None) or '').encode('utf-8')
    if not app_secret:
        return False

    sig_hdr = headers.get('x-hub-signature-256') or headers.get('X-Hub-Signature-256')
    if not sig_hdr or not sig_hdr.lower().startswith('sha256='):
        return False

    try:
        body_bytes = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    except Exception:
        # Last resort: try to bytes() it
        try:
            body_bytes = bytes(payload)
        except Exception:
            return False

    expected = 'sha256=' + hmac.new(app_secret, msg=body_bytes, digestmod=hashlib.sha256).hexdigest()
    # Constant-time comparison
    return hmac.compare_digest(expected, sig_hdr)


def verify_baileys_hmac(headers: Mapping[str, str], raw_body: bytes) -> bool:
    """Verify HMAC for local Baileys webhook if enabled.

    Expect header: X-Baileys-Signature: sha256=<hex>
    """
    if not getattr(settings, 'ENABLE_BAILEYS_HMAC_VERIFICATION', False):
        return True
    secret = (getattr(settings, 'BAILEYS_WEBHOOK_SECRET', None) or '').encode('utf-8')
    if not secret:
        return False
    sig_hdr = headers.get('x-baileys-signature') or headers.get('X-Baileys-Signature')
    if not sig_hdr or not sig_hdr.lower().startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(secret, msg=raw_body or b'', digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_hdr)
