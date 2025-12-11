const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const qrcode = require("qrcode-terminal");
const express = require("express");
const axios = require("axios");
const pino = require("pino");

const PORT = process.env.BAILEYS_PORT || 3000;
const FASTAPI_URL = process.env.FASTAPI_URL || "https://hustlr-official-1-1.onrender.com";

async function startBaileys() {
  const { state, saveCreds } = await useMultiFileAuthState("./baileys_auth");
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "silent" }),
    browser: ["HustlrBot", "Chrome", "1.0.0"],
  });

  // Always keep the latest active socket in this reference so that
  // the Express /send-text route uses a valid connection, even after
  // Baileys reconnects internally.
  sockRef = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      console.log("Scan this QR to connect WhatsApp:");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const payload = lastDisconnect?.error?.output?.payload;

      console.log(
        "connection.update close:",
        JSON.stringify(
          {
            statusCode,
            payload,
          },
          null,
          2
        )
      );

      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log("connection closed. reconnect:", shouldReconnect, "status:", statusCode);
      if (shouldReconnect) {
        // This will update sockRef again because startBaileys assigns it.
        startBaileys().catch((err) => console.error("reconnect failed", err));
      }
    } else if (connection === "open") {
      latestQR = null;
      console.log("✅ Baileys connected to WhatsApp");
    }
  });

  sock.ev.on("messages.upsert", async (msg) => {
    try {
      if (!msg.messages || !msg.messages[0]) return;
      const m = msg.messages[0];

      // Ignore own messages
      if (m.key.fromMe) return;

      const remoteJid = m.key.remoteJid || "";
      const senderPnJid = m.key.senderPn || "";
      // Prefer the sender phone JID when available (e.g. 263...@s.whatsapp.net),
      // fall back to the chat JID. Then normalize to plain number by stripping
      // anything after '@' so Python always sees a clean phone string.
      const fromJid = senderPnJid || remoteJid;
      const from = fromJid.split("@")[0];

      let text = "";
      if (m.message?.conversation) {
        text = m.message.conversation;
      } else if (m.message?.extendedTextMessage?.text) {
        text = m.message.extendedTextMessage.text;
      } else if (m.message?.locationMessage) {
        const loc = m.message.locationMessage;
        const name = loc.name || "";
        const address = loc.address || "";
        const lat = loc.degreesLatitude;
        const lng = loc.degreesLongitude;
        const parts = [];
        if (name) parts.push(name);
        if (address) parts.push(address);
        if (lat !== undefined && lng !== undefined) {
          parts.push(`(${lat},${lng})`);
        }
        text = parts.join(" ") || "[location shared]";
      } else if (m.message?.buttonsResponseMessage?.selectedButtonId) {
        text = m.message.buttonsResponseMessage.selectedButtonId;
      } else if (
        m.message?.listResponseMessage?.singleSelectReply?.selectedRowId
      ) {
        text = m.message.listResponseMessage.singleSelectReply.selectedRowId;
      }

      console.log("📩 Incoming message from", from, "text:", text);

      await axios.post(`${FASTAPI_URL}/api/whatsapp/baileys-webhook`, {
        from,
        text,
        rawMessage: m,
      });
    } catch (err) {
      console.error("Error handling incoming message", err);
    }
  });

  return sock;
}

let sockRef;
let latestQR = null;

async function startServer() {
  sockRef = await startBaileys();

  const app = express();
  app.use(express.json({ limit: "1mb" }));

  app.post("/send-text", async (req, res) => {
    try {
      const { to, text } = req.body;
      if (!to || !text) {
        return res.status(400).json({ error: "Missing 'to' or 'text'" });
      }

      // Sanitize phone number: keep digits only and build proper JID
      const digits = String(to).replace(/[^\d]/g, "");
      if (!digits) {
        return res.status(400).json({ error: "Invalid phone number format", to });
      }

      const jid = `${digits}@s.whatsapp.net`;
      const preview = String(text).slice(0, 80) + (String(text).length > 80 ? "…" : "");
      console.log(`[send-text] request to=${to} digits=${digits} jid=${jid} preview="${preview}"`);

      // Ensure number is on WhatsApp to avoid Baileys timeouts
      const existsArr = await sockRef.onWhatsApp(jid);
      const exists = Array.isArray(existsArr) && existsArr[0] && existsArr[0].exists;
      if (!exists) {
        console.warn(`[send-text] number NOT on WhatsApp: ${digits} (${jid})`);
        return res.status(404).json({ error: "Number is not on WhatsApp", number: digits, jid });
      }

      console.log(`[send-text] sending to ${digits} (${jid})…`);
      await sockRef.sendMessage(jid, { text });
      console.log(`[send-text] ✅ sent to ${digits} (${jid}), bytes=${String(text).length}`);
      res.json({ status: "sent", number: digits, jid });
    } catch (err) {
      console.error("[send-text] ❌ error sending message", err);
      const msg = (err && err.message) || "Failed to send message";
      res.status(500).json({ error: msg });
    }
  });

  app.post("/notify-admins", async (req, res) => {
    try {
      let { admins, text } = req.body || {};
      if (!Array.isArray(admins) || admins.length === 0) {
        admins = [
          '+263783961640',
          '+263775251636',
          '+263777530322',
          '+16509965727'
        ];
      }
      if (!text) {
        text = "You have been added as a Hustlr admin. You will receive approval and system notifications here.";
      }
      const results = [];
      for (const to of admins) {
        const digits = String(to).replace(/[^\d]/g, "");
        if (!digits) {
          results.push({ to, status: "skipped", error: "invalid number" });
          continue;
        }
        const jid = `${digits}@s.whatsapp.net`;
        try {
          const existsArr = await sockRef.onWhatsApp(jid);
          const exists = Array.isArray(existsArr) && existsArr[0] && existsArr[0].exists;
          if (!exists) {
            results.push({ to: digits, jid, status: "failed", error: "not on WhatsApp" });
            continue;
          }
          await sockRef.sendMessage(jid, { text });
          results.push({ to: digits, jid, status: "sent" });
        } catch (e) {
          const emsg = (e && e.message) || "send failed";
          results.push({ to: digits, jid, status: "failed", error: emsg });
        }
      }
      res.json({ status: "done", count: results.length, results });
    } catch (err) {
      const msg = (err && err.message) || "Failed to notify admins";
      res.status(500).json({ error: msg });
    }
  });

  app.get("/health", (req, res) => {
    res.json({ status: "ok" });
  });

  app.get("/qr", (req, res) => {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    if (!latestQR) {
      return res.send(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>WhatsApp QR</title></head><body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;"><h2>No QR available</h2><p>Already connected, or QR not generated yet. Keep this page open and refresh after restarting or unlink to regenerate.</p><button onclick="location.reload()">Refresh</button></body></html>`);
    }
    const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>WhatsApp QR</title></head><body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;"><h2>Scan to link WhatsApp</h2><canvas id="qr" style="max-width: 320px; width: 100%; height: auto;"></canvas><p>On your phone: WhatsApp > Linked devices > Link a device.</p><script src="https://cdn.jsdelivr.net/npm/qrcode/build/qrcode.min.js"></script><script>const val = ${JSON.stringify(latestQR)};QRCode.toCanvas(document.getElementById('qr'), val, { width: 320 }, function (error) { if (error) console.error(error); });</script></body></html>`;
    res.send(html);
  });

  app.listen(PORT, () => {
    console.log(`Baileys service listening on port ${PORT}`);
  });
}

startServer().catch((err) => {
  console.error("Failed to start Baileys service", err);
});
