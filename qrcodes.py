from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass

SCHEME = "messenger"


@dataclass
class ParsedProfile:
    username: str
    key_b64: str


def make_profile_uri(username: str, key_b64: str) -> str:
    return f"{SCHEME}://{username}/{urllib.parse.quote(key_b64, safe='')}"


def parse_profile_uri(payload: str) -> ParsedProfile | None:
    try:
        prefix = f"{SCHEME}://"
        if payload.startswith(prefix):
            rest = payload[len(prefix):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                username = parts[0]
                key_b64 = urllib.parse.unquote(parts[1])
                if username and key_b64:
                    return ParsedProfile(username=username, key_b64=key_b64)
    except Exception:
        pass
    return None


def make_profile_png(username: str, key_b64: str) -> bytes:
    """Generate a QR code PNG for a user profile."""
    try:
        import io

        import qrcode  # type: ignore

        uri = make_profile_uri(username, key_b64)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Minimal 8x8 white PNG placeholder when qrcode isn't installed.
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAB"
            "mJLR0QA/wD/AP+gvaeTAAAADUlEQVQI12P4z8BQDwAEgAF/"
            "QualIQAAAABJRU5ErkJggg=="
        )


def decode_qr_from_image_bytes(data: bytes) -> str | None:
    """Decode a QR code from image bytes. Returns the QR payload or None."""
    try:
        import io

        from PIL import Image  # type: ignore
        from pyzbar import pyzbar  # type: ignore

        img = Image.open(io.BytesIO(data))
        codes = pyzbar.decode(img)
        if codes:
            return codes[0].data.decode("utf-8")
    except ImportError:
        pass
    except Exception:
        pass
    return None
