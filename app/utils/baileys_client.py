import os
from typing import Dict, Any, List, Optional
import asyncio

import httpx


class BaileysClient:
    """Simple client to send messages via local Baileys Node service.

    This implements the minimal interface used by MessageHandler so
    it can be swapped with the Cloud API client.
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = base_url or os.getenv("BAILEYS_SERVICE_URL", "http://localhost:3000")

    async def send_text_message(
        self,
        to_number: str,
        message: str,
        preview_url: bool = False,
    ) -> Dict[str, Any]:
        payload = {"to": to_number, "text": message}
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/send-text",
                        json=payload,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                # Retry only on transient server-side errors
                if attempt == 0 and e.response is not None and e.response.status_code in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(1.0)
                    continue
                last_exc = e
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                # Transient network errors: retry once after a short delay
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                last_exc = e
                break
            except Exception as e:
                last_exc = e
                break
        # If we get here, both attempts failed
        if last_exc:
            raise last_exc
        raise RuntimeError("Unknown error sending WhatsApp message via Baileys")

    async def send_interactive_buttons(
        self,
        to_number: str,
        header_text: str,
        body_text: str,
        buttons: List[Dict[str, Any]],
        footer_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fallback implementation: render buttons as a numbered text list.

        Baileys can send real interactive messages, but for simplicity we
        render them as plain text options that still work with the existing
        conversation logic (user can reply with a number or option text).
        """

        lines: List[str] = []
        if header_text:
            lines.append(header_text)
        if body_text:
            lines.append(body_text)

        lines.append("")
        for idx, btn in enumerate(buttons, start=1):
            title = btn.get("title", "")
            lines.append(f"{idx}) {title}")

        if footer_text:
            lines.append("")
            lines.append(footer_text)

        text = "\n".join(lines)
        return await self.send_text_message(to_number, text)
