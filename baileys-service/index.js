const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const qrcode = require("qrcode-terminal");
const express = require("express");
const axios = require("axios");
const pino = require("pino");

const PORT = process.env.BAILEYS_PORT || 3000;
const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

async function startBaileys() {
  const { state, saveCreds } = await useMultiFileAuthState("./baileys_auth");
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: true,
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
      console.log("Scan this QR to connect WhatsApp:");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log("connection closed. reconnect:", shouldReconnect, "status:", statusCode);
      if (shouldReconnect) {
        // This will update sockRef again because startBaileys assigns it.
        startBaileys().catch((err) => console.error("reconnect failed", err));
      }
    } else if (connection === "open") {
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

      const jid = to.includes("@") ? to : `${to}@s.whatsapp.net`;
      await sockRef.sendMessage(jid, { text });
      res.json({ status: "sent" });
    } catch (err) {
      console.error("Error sending message", err);
      res.status(500).json({ error: "Failed to send message" });
    }
  });

  app.get("/health", (req, res) => {
    res.json({ status: "ok" });
  });

  app.listen(PORT, () => {
    console.log(`Baileys service listening on port ${PORT}`);
  });
}

startServer().catch((err) => {
  console.error("Failed to start Baileys service", err);
});
