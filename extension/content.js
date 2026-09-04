function clean(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function getProfileText() {
  const main = document.querySelector("main") || document.body;
  const body = clean(main.innerText || document.body.innerText || "");
  const lower = body.toLowerCase();

  // Keep some profile header/About context, then take the Experience block directly
  // from the page text. This is faster and more robust than depending on LinkedIn's DOM nesting.
  const header = body.slice(0, 2600);
  const markers = ["experience", "berufserfahrung"];
  const starts = markers.map(m => lower.indexOf(m)).filter(i => i >= 0);

  let experience = "";
  if (starts.length) {
    const start = Math.min(...starts);
    experience = body.slice(start, start + 12000);
  } else {
    experience = body.slice(0, 12000);
  }

  return `PROFILE HEADER / ABOUT:\n${header}\n\nEXPERIENCE:\n${experience}`;
}

async function extractProfile() {
  const text = getProfileText();
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
    extractProfile()
      .then(profile => sendResponse({ ok: true, profile }))
      .catch(error => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
});
