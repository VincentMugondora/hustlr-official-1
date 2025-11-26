from typing import Optional

class WhatsAppMessage:
    """
    Simple message model. Expand as needed for your use-case.
    """
    def __init__(self, from_number: str, text: str):
        self.from_number = from_number
        self.text = text

    @classmethod
    def from_webhook(cls, payload: dict):
        """
        Parses webhook payload from Meta into WhatsAppMessage instance.
        Adjust keys/indexing depending on Meta's actual payload structure!
        """
        try:
            # Example: extract first message
            entry = payload["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            messages = value.get("messages", [])
            if messages:
                from_number = messages[0]["from"]
                text = messages[0].get("text", {}).get("body", "")
                return cls(from_number, text)
            else:
                return cls("", "")
        except (KeyError, IndexError):
            return cls("", "")
