from __future__ import annotations
import base64, hashlib, hmac, html, os, re, time, unicodedata

CONTROL_RE = re.compile('[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]')
FILENAME_BAD = re.compile('[/\\\\:\\x00-\\x1f\\x7f]')
FILENAME_MAX = 255

MAX_BODY: int = 1_048_576

def create_signer(secret: str | bytes | None = None):
    """Create an HMAC-SHA256 cookie signer.

    Usage:
        signer = create_signer("my-secret")

        # Write
        set_cookie(req, "session", signer.sign("Fox42"))

        # Read
        user = signer.unsign(req["cookies"].get("session", ""))
        if user is None: ...  # invalid or expired

    If no secret is provided, one is generated at startup.
    This means signatures won't survive process restarts — fine
    for development, but pass an explicit secret in production.
    """
    if secret is None:
        secret = os.urandom(32)
    if isinstance(secret, str):
        secret = secret.encode()

    def _b64e(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _b64d(s: str) -> bytes:
        s += "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s.encode())

    def _mac(payload: str) -> str:
        sig = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        return _b64e(sig)

    def sign(value: str, ts: float | None = None) -> str:
        """Sign a value with a timestamp.

        Returns: ``base64(value).timestamp_hex.signature``
        """
        ts = ts or time.time()
        payload = f"{_b64e(value.encode())}.{int(ts):x}"
        return f"{payload}.{_mac(payload)}"

    def unsign(signed: str, max_age: int | None = 3600) -> str | None:
        """Verify and extract a signed value.

        Args:
            signed:  The signed cookie string.
            max_age: Maximum age in seconds. None = no expiry.

        Returns the original value, or None if the signature is
        invalid, the format is wrong, or the value has expired.
        """
        if not signed:
            return None
        parts = signed.split(".")
        if len(parts) != 3:
            return None
        enc_value, ts_hex, sig = parts
        payload = f"{enc_value}.{ts_hex}"
        if not hmac.compare_digest(sig, _mac(payload)):
            return None
        if max_age is not None:
            try:
                ts = int(ts_hex, 16)
            except ValueError:
                return None
            if time.time() - ts > max_age:
                return None
        try:
            return _b64d(enc_value).decode()
        except Exception:
            return None

    class _Signer:
        """Thin namespace — attribute access, not a class hierarchy."""
        __slots__ = ("sign", "unsign")
    s = _Signer()
    s.sign = sign
    s.unsign = unsign
    return s

class BodyTooLarge(Exception):
    """Raised when the request body exceeds the configured limit."""
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"Request body exceeds {limit} bytes")

async def read_body(req: dict, *, max_size: int = MAX_BODY) -> bytes:
    """Read the request body with caching and size enforcement.

    Safe to call multiple times — the result is cached on first read.
    Raises BodyTooLarge if the body exceeds max_size.

    Drop-in replacement for the original ``body()`` function.
    """
    # Return cached result
    if "_body_cache" in req:
        return req["_body_cache"]

    receive = req["internal_receive"]
    chunks: list[bytes] = []
    total = 0
    while True:
        msg = await receive()
        chunk = msg.get("body", b"")
        total += len(chunk)
        if total > max_size:
            req["_body_cache"] = b""  # prevent re-reads
            raise BodyTooLarge(max_size)
        chunks.append(chunk)
        if not msg.get("more_body"):
            break

    result = b"".join(chunks)
    req["_body_cache"] = result
    return result

def sanitize_html(text: str) -> str:
    """Escape HTML entities and strip control characters.

    Use for any user-provided text that will appear in HTML content.
    Does NOT strip tags — it escapes them, so ``<script>`` becomes
    ``&lt;script&gt;``, preserving the user's intent visibly.
    """
    text = CONTROL_RE.sub("", text)
    return html.escape(text, quote=True)

def sanitize_filename(name: str) -> str:
    """Sanitise a user-provided filename for safe display and storage.

    Strips path components, control characters, and leading dots.
    Returns "unnamed" if nothing remains.
    """
    # Take only the final path component
    name = name.split("/")[-1].split("\\")[-1]
    # Strip control chars and path separators
    name = FILENAME_BAD.sub("", name)
    # Normalise unicode to NFC (prevents homoglyph path tricks)
    name = unicodedata.normalize("NFC", name)
    # Strip leading dots (hidden files)
    name = name.lstrip(".")
    # Truncate
    name = name[:FILENAME_MAX]
    return name.strip() or "unnamed"

def validate_base64(data: str, max_decoded_size: int | None = None) -> bool:
    """Check that a string is valid base64 and optionally within size.

    This is a fast structural check — it doesn't decode the full payload,
    it validates the character set and checks the *encoded* length against
    the expected decoded size bound (base64 is ~4/3 of decoded size).
    """
    if not data:
        return False
    # base64 alphabet check (standard + urlsafe)
    if not re.fullmatch(r'[A-Za-z0-9+/\-_=\n\r]*', data):
        return False
    if max_decoded_size is not None:
        # Encoded length ≈ 4/3 * decoded length
        max_encoded = (max_decoded_size * 4 // 3) + 4
        if len(data) > max_encoded:
            return False
    return True

def validate_mime(mime: str) -> str | None:
    """Validate and normalise a MIME type string.

    Returns the lowercased type/subtype, or None if malformed.
    """
    m = re.fullmatch(r'([a-zA-Z0-9][a-zA-Z0-9!#$&\-^_]*)/([a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*)', mime.strip())
    return f"{m.group(1).lower()}/{m.group(2).lower()}" if m else None
