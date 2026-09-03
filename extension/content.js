function clean(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function findExperienceSection() {
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, span, div"));
  const target = headings.find(el => {
    const text = clean(el.innerText).toLowerCase();
    return text === "experience" || text === "berufserfahrung";
  });

  if (!target) return null;
  return target.closest("section") || target.closest("div[data-view-name]") || target.parentElement?.parentElement || null;
}

function isExpandButton(button) {
  const text = clean(button.innerText || button.getAttribute("aria-label") || "").toLowerCase();
  if (!text) return false;
  const positive = ["see more", "show more", "mehr anzeigen", "mehr sehen", "weitere anzeigen"];
  const negative = ["show all", "see all", "alle anzeigen", "all activity", "all skills"];
  return positive.some(term => text.includes(term)) && !negative.some(term => text.includes(term));
}

async function expandExperienceText() {
  const experience = findExperienceSection();
  if (!experience) return;
  const buttons = Array.from(experience.querySelectorAll("button"))
    .filter(button => !button.disabled && isExpandButton(button))
    .slice(0, 30);
  for (const button of buttons) {
    try { button.click(); } catch (_) {}
  }
  if (buttons.length) await new Promise(resolve => setTimeout(resolve, 160));
}

function getHeaderContext() {
  const main = document.querySelector("main") || document.body;
  const lines = (main.innerText || "").split("\n").map(clean).filter(Boolean);
  return clean(lines.slice(0, 35).join(" | ")).slice(0, 1800);
}

function getExperienceText() {
  const experience = findExperienceSection();
  if (experience) {
    // Keep EVERYTHING inside Experience, including each job's own "Skills:" line.
    // Those job-level skills are important evidence for which technology was used in that role.
    return clean(experience.innerText).slice(0, 10000);
  }

  const body = clean(document.body.innerText);
  const lower = body.toLowerCase();
  const starts = [lower.indexOf("experience"), lower.indexOf("berufserfahrung")].filter(i => i >= 0);
  if (starts.length) {
    const start = Math.min(...starts);
    return body.slice(start, start + 10000);
  }
  return body.slice(0, 10000);
}

async function extractProfile() {
  // Experience-first: job descriptions AND the Skills lines attached to those jobs are included.
  // The separate global Skills section at the bottom of the LinkedIn profile is intentionally ignored.
  await expandExperienceText();
  const header = getHeaderContext();
  const experience = getExperienceText();
  const text = `PROFILE HEADER:\n${header}\n\nPROFESSIONAL EXPERIENCE (INCLUDING SKILLS ATTACHED TO EACH JOB):\n${experience}`;

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
