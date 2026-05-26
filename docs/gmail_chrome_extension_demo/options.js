const DEFAULTS = {
  backendUrl: "",
  integrationToken: "",
  organizationId: "1",
  senderEmail: "",
};

chrome.storage.sync.get(DEFAULTS, (settings) => {
  for (const key of Object.keys(DEFAULTS)) {
    document.getElementById(key).value = settings[key] || "";
  }
});

document.getElementById("save").addEventListener("click", () => {
  const settings = {};
  for (const key of Object.keys(DEFAULTS)) {
    settings[key] = document.getElementById(key).value.trim();
  }
  chrome.storage.sync.set(settings, () => {
    document.getElementById("status").textContent = "Saved.";
  });
});
