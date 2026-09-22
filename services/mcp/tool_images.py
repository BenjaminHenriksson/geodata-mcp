"""Attach rendered image content after the normal workspace audit completes."""

import base64
import json
from functools import wraps

from mcp.types import CallToolResult, ImageContent, TextContent


def with_image_content(fn):
    """Keep metadata structured and image bytes out of JSON/text token streams.

    Apply outside audit.tool: the audit retains the same authentication and
    attribution path, then this adapter converts only our private bytes field.
    Ordinary analysis responses keep their existing dictionary contract.
    """
    @wraps(fn)
    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        image = result.get("_perspective_image")
        if not isinstance(image, bytes):
            return result
        metadata = {key: value for key, value in result.items()
                    if key != "_perspective_image"}
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                ImageContent(type="image", mimeType="image/jpeg",
                             data=base64.b64encode(image).decode("ascii")),
            ],
            structuredContent=metadata,
            isError=bool(metadata.get("error")),
        )
    return wrapped
