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
      lastQRTime = Date.now();
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
      console.log("✅ Baileys connected to WhatsApp");
      latestQR = null;
      lastQRTime = 0;
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
let lastQRTime = 0;

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

  app.get("/", (req, res) => {
    res.redirect("/qr");
  });

  app.get("/qr-data", (req, res) => {
    res.json({ qr: latestQR, ageMs: latestQR ? Date.now() - lastQRTime : null });
  });

  app.get("/qr", (req, res) => {
    res.type("html").send(`<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>WhatsApp QR</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 20px; display: flex; align-items: center; justify-content: center; min-height: 100vh; background:#0b1020; color:#eaeef7; }
    .card { background: #121933; border: 1px solid #1f2a4d; border-radius: 12px; padding: 24px; width: min(420px, 92vw); box-shadow: 0 12px 30px rgba(0,0,0,.25); }
    h1 { font-size: 18px; margin: 0 0 12px; }
    #qr { display:flex; align-items:center; justify-content:center; background:#fff; padding:12px; border-radius:10px; }
    .muted { color:#9fb2d9; font-size: 12px; margin-top: 10px; }
    button { margin-top: 12px; background: #2b6ff6; color: #fff; border: 0; padding: 10px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .badge { display:inline-block; background:#1f2a4d; color:#9fb2d9; padding:4px 8px; border-radius:6px; font-size:12px; margin-left:8px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Scan WhatsApp QR <span class="badge">auto-refresh</span></h1>
    <div id="qr" style="min-height: 320px; min-width: 320px;"></div>
    <div class="muted" id="status">Waiting for QR...</div>
    <button id="refresh">Refresh now</button>
  </div>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
  <script>
    const qrEl = document.getElementById('qr');
    const statusEl = document.getElementById('status');
    const refreshBtn = document.getElementById('refresh');
    let qrcodeObj = null;

    function renderQR(data) {
      qrEl.innerHTML = '';
      if (!data) {
        statusEl.textContent = 'No QR yet. If you are connected, no QR will be shown.';
        return;
      }
      qrcodeObj = new QRCode(qrEl, { text: data, width: 300, height: 300, correctLevel: QRCode.CorrectLevel.M });
      statusEl.textContent = 'Open WhatsApp → Linked Devices → Link a device';
    }

    async function fetchQR() {
      try {
        const res = await fetch('/qr-data', { cache: 'no-store' });
        const { qr } = await res.json();
        renderQR(qr);
      } catch (e) {
        statusEl.textContent = 'Failed to load QR. Retrying...';
      }
    }

    refreshBtn.addEventListener('click', fetchQR);
    setInterval(fetchQR, 3000);
    fetchQR();
  </script>
</body>
</html>`);
  });

  app.listen(PORT, () => {
    console.log(`Baileys service listening on port ${PORT}`);
  });
}

startServer().catch((err) => {
  console.error("Failed to start Baileys service", err);
});
