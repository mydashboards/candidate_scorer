function clean(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function isExpandButton(button) {
  const text = clean(button.innerText || button.getAttribute("aria-label") || "").toLowerCase();
  if (!text) return false;

  const positive = [
    "see more",
    "show more",
    "mehr anzeigen",
    "mehr sehen",
    "weitere anzeigen"
  ];

  const negative = [
    "show all",
    "see all",
    "alle anzeigen",
    "all experiences",
    "all activity",
    "all skills"
  ];

  return positive.some(term => text.includes(term)) && !negative.some(term => text.includes(term));
}

async function expandCollapsedText() {
  const buttons = Array.from(document.querySelectorAll("button"))
    .filter(button => !button.disabled && isExpandButton(button))
    .slice(0, 40);

  for (const button of buttons) {
    try {
      button.click();
    } catch (_) {}
  }

  if (buttons.length) {
    await new Promise(resolve => setTimeout(resolve, 180));
  }
}

async function extractProfile() {
  // Expand collapsed text blocks without navigating away from the profile.
  await expandCollapsedText();

  const text = clean(document.body.innerText).slice(0, 12000);
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
