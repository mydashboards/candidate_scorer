function clean(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function extractProfile() {
  const text = clean(document.body.innerText).slice(0, 14000);
  const title = document.title
    .replace(/\s*\|\s*LinkedIn.*$/i, "")
    .replace(/\s*\|\s*Recruiter.*$/i, "")
    .trim();

  return {
    name: title || "Unknown candidate",
    profileUrl: location.href.split("?")[0],
    visibleProfileText: text,
    capturedAt: new Date().toISOString()
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "EXTRACT_PROFILE") {
    try {
      sendResponse({ ok: true, profile: extractProfile() });
    } catch (error) {
      sendResponse({ ok: false, error: String(error) });
    }
  }
  return true;
});
