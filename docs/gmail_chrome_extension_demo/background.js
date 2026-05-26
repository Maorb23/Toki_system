const DEFAULTS = {
  backendUrl: "",
  integrationToken: "",
  organizationId: "1",
  senderEmail: "",
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "receiverAwareApi") return false;

  chrome.storage.sync.get(DEFAULTS, async (settings) => {
    try {
      const backendUrl = trimTrailingSlash(settings.backendUrl || "");
      const token = settings.integrationToken || "";
      if (!backendUrl) throw new Error("Backend URL is missing. Set it in the extension options.");
      if (!token) throw new Error("Integration token is missing. Set it in the extension options.");

      const response = await fetch(`${backendUrl}${message.path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Gmail-Integration-Token": token,
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify(message.payload || {}),
      });
      const text = await response.text();
      let data = {};
      try {
        data = JSON.parse(text);
      } catch (error) {
        data = { error: friendlyHttpError(text, response.status) };
      }
      sendResponse({ ok: response.ok, status: response.status, data });
    } catch (error) {
      sendResponse({ ok: false, status: 0, data: { error: error.message } });
    }
  });

  return true;
});

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function friendlyHttpError(text, status) {
  const body = String(text || "").trim();
  if (!body) return `Backend returned HTTP ${status}`;

  if (body.includes("ERR_NGROK_3004")) {
    return "ngrok could not reach Django or received an incomplete response. Check that python manage.py runserver and ngrok are both still running, then retry.";
  }

  if (/^\s*<!doctype html|^\s*<html/i.test(body)) {
    const title = extractTagText(body, "title");
    const heading = extractTagText(body, "h1");
    const readable = [title, heading].filter(Boolean).join(": ");
    return readable
      ? `Backend returned HTML instead of JSON: ${readable}`
      : `Backend returned HTML instead of JSON (HTTP ${status}).`;
  }

  return body.length > 500 ? `${body.slice(0, 500)}...` : body;
}

function extractTagText(html, tagName) {
  const match = html.match(new RegExp(`<${tagName}[^>]*>([\\s\\S]*?)<\\/${tagName}>`, "i"));
  if (!match) return "";
  return match[1]
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
