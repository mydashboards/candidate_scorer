function clean(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function getProfileText() {
  const root = document.querySelector("main") || document.body;
  const raw = root.innerText || document.body.innerText || "";
  const lines = raw.split("\n").map(line => line.trim()).filter(Boolean);

  const experienceIndex = lines.findIndex(line => {
    const t = line.toLowerCase();
    return t === "experience" || t === "berufserfahrung";
  });

  // Include the complete candidate profile from the top: headline, About/Summary,
  // Top Skills, Experience, job descriptions and Skills attached to individual jobs.
  // Stop only at the separate global Skills section near the bottom.
  let stopIndex = lines.length;
  if (experienceIndex >= 0) {
    for (let i = experienceIndex + 1; i < lines.length; i++) {
      const t = lines[i].toLowerCase();
      const isGlobalSkillsHeading = t === "skills" || t === "kenntnisse" || t === "fähigkeiten";
      if (isGlobalSkillsHeading) {
        stopIndex = i;
        break;
      }
    }
  }

  // Avoid Recruiter sidebar/recommendation text becoming candidate evidence.
  const profileLines = lines.slice(0, stopIndex).filter(line => {
    const t = line.toLowerCase();
    return t !== "similar profiles" &&
      t !== "recruiting tools" &&
      t !== "save to pipeline";
  });

  return clean(profileLines.join("\n")).slice(0, 20000);
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
