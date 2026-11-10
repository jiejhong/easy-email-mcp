"""Explicit, bounded attachment bytes. Never open paths, URLs or archives."""
import base64
import binascii
from dataclasses import dataclass
import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import MailError

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024  # Config's hard per-file ceiling.
MAX_SEND_ARGUMENT_BYTES = 48 * 1024 * 1024  # Includes base64 and JSON overhead.


class SendAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    filename: str = Field(min_length=1, max_length=120, description="Plain filename, not a local path or URL")
    base64: str = Field(max_length=4 * ((MAX_ATTACHMENT_BYTES + 2) // 3), description="Standard padded base64 of the exact user-approved file bytes")
    content_type: str = Field(default="application/octet-stream", min_length=3, max_length=127, description="MIME type/subtype without parameters")


@dataclass(frozen=True)
class PreparedAttachment:
    filename: str
    content_type: str
    data: bytes

    def metadata(self):
        return {"filename": self.filename, "content_type": self.content_type,
                "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}


def safe_filename(name):
    name = (name or "attachment.bin").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", name).strip(" .")
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return (name or "attachment.bin")[:120]


def prepare_attachments(attachments, config):
    if attachments is None:
        return ()
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
        raise MailError("Use a list of at most 10 attachments")
    prepared, total = [], 0
    for value in attachments:
        try:
            item = SendAttachment.model_validate(value)
        except ValidationError:
            raise MailError("Invalid attachment fields; provide filename, base64 and optional content_type only") from None
        if safe_filename(item.filename) != item.filename:
            raise MailError("Use a safe plain attachment filename, not a path or URL")
        # No arbitrary MIME parameters, header injection or nested MIME messages.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*", item.content_type):
            raise MailError("Attachment content_type must be a MIME type/subtype without parameters")
        content_type = item.content_type.lower()
        if content_type.split("/", 1)[0] not in {"application", "audio", "font", "image", "model", "text", "video"}:
            raise MailError("Unsupported attachment content_type; use application/octet-stream for opaque files")
        # Reject size before decoding or allocating file bytes. Validate again
        # after decoding, since padding determines the exact decoded length.
        if len(item.base64) > 4 * ((config.max_attachment_bytes + 2) // 3):
            raise MailError("Attachment exceeds configured byte limit")
        estimated = (len(item.base64) // 4) * 3 - (len(item.base64) - len(item.base64.rstrip("=")))
        if estimated > config.max_attachment_bytes or total + estimated > config.max_message_bytes:
            raise MailError("Attachment or combined attachments exceed configured byte limit")
        try:
            data = base64.b64decode(item.base64, validate=True)
        except (ValueError, binascii.Error):
            raise MailError("Attachment base64 is invalid; use standard padded base64 without whitespace") from None
        if base64.b64encode(data).decode("ascii") != item.base64:
            raise MailError("Attachment base64 is not canonical padded base64")
        if len(data) > config.max_attachment_bytes or total + len(data) > config.max_message_bytes:
            raise MailError("Attachment or combined attachments exceed configured byte limit")
        total += len(data)
        prepared.append(PreparedAttachment(item.filename, content_type, data))
    return tuple(prepared)
