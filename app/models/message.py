from typing import Optional


class WhatsAppMessage:
    """
    Message model supporting text and media for WhatsApp Cloud webhook.
    """
    def __init__(
        self,
        from_number: str,
        text: str = "",
        msg_type: str = "text",
        media_id: Optional[str] = None,
        media_mime: Optional[str] = None,
        media_caption: Optional[str] = None,
    ):
        self.from_number = from_number
        self.text = text or ""
        self.type = msg_type or "text"
        self.media_id = media_id
        self.media_mime = media_mime
        self.media_caption = media_caption

    @classmethod
    def from_webhook(cls, payload: dict):
        """
        Parses webhook payload from Meta into WhatsAppMessage instance.
        Supports text, image, document, video minimal fields.
        """
        try:
            entry = payload["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            messages = value.get("messages", [])
            if not messages:
                return cls("", "")

            msg = messages[0]
            from_number = msg.get("from", "")
            msg_type = msg.get("type", "text")

            # Text
            if msg_type == "text":
                text = (msg.get("text") or {}).get("body", "")
                return cls(from_number, text=text, msg_type="text")

            # Interactive (buttons and lists)
            if msg_type == "interactive":
                inter = msg.get("interactive") or {}
                i_type = inter.get("type")
                text = ""
                try:
                    if i_type == "button":
                        btn = inter.get("button") or {}
                        # Prefer user-visible text; fall back to payload/id
                        text = (btn.get("text") or btn.get("payload") or "")
                    elif i_type in ("list_reply", "list"):
                        lr = inter.get("list_reply") or {}
                        text = (lr.get("title") or lr.get("id") or "")
                except Exception:
                    text = ""
                return cls(from_number, text=text, msg_type="interactive")

            # Image
            if msg_type == "image":
                image = msg.get("image") or {}
                return cls(
                    from_number,
                    text=(image.get("caption") or ""),
                    msg_type="image",
                    media_id=image.get("id"),
                    media_mime=None,
                    media_caption=image.get("caption"),
                )

            # Document
            if msg_type == "document":
                doc = msg.get("document") or {}
                return cls(
                    from_number,
                    text=(doc.get("caption") or ""),
                    msg_type="document",
                    media_id=doc.get("id"),
                    media_mime=doc.get("mime_type"),
                    media_caption=doc.get("caption"),
                )

            # Video
            if msg_type == "video":
                vid = msg.get("video") or {}
                return cls(
                    from_number,
                    text=(vid.get("caption") or ""),
                    msg_type="video",
                    media_id=vid.get("id"),
                    media_mime=None,
                    media_caption=vid.get("caption"),
                )

            # Fallback: unsupported types
            return cls(from_number, text="", msg_type=msg_type)
        except Exception:
            return cls("", "")
