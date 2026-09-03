const analyzeBtn = document.getElementById("analyze");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const scoreEl = document.getElementById("score");
const decisionEl = document.getElementById("decision");
const summaryEl = document.getElementById("summary");
const criteriaEl = document.getElementById("criteria");

let lastProfileUrl = null;
let analyzingUrl = null;
let failedProfileUrl = null;
let debounceTimer = null;

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function extractFromTab(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_PROFILE" });
  } catch (_) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_PROFILE" });
  }
}

function render(data) {
  scoreEl.textContent = data.score;
  decisionEl.textContent = data.decision;
  summaryEl.textContent = data.summary;
  criteriaEl.innerHTML = "";
  for (const item of data.criteria) {
    const row = document.createElement("div");
    row.className = "criterion";
    row.innerHTML = `<div class="criterionTop"><span>${item.label}</span><span>${item.points}/${item.max_points}</span></div><div class="evidence">${item.status.toUpperCase()} — ${item.evidence}</div>`;
    criteriaEl.appendChild(row);
  }
  resultEl.classList.remove("hidden");
}

async function analyzeCurrentProfile(force = false) {
  let url = null;
  try {
    const tab = await getActiveTab();
    if (!tab?.id || !tab.url?.includes("linkedin.com")) return;
    url = tab.url.split("?")[0];
    if (!force && (url === lastProfileUrl || url === analyzingUrl || url === failedProfileUrl)) return;

    if (force) failedProfileUrl = null;
    analyzingUrl = url;
    resultEl.classList.add("hidden");
    statusEl.textContent = "Analyzing...";
    await new Promise(resolve => setTimeout(resolve, 450));

    const extracted = await extractFromTab(tab.id);
    if (!extracted?.ok) throw new Error(extracted?.error || "Could not read profile.");
    const response = await fetch("http://127.0.0.1:3847/analyze", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(extracted.profile)});
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();

    const currentTab = await getActiveTab();
    const currentUrl = currentTab?.url?.split("?")[0];
    if (currentUrl !== url) {
      analyzingUrl = null;
      scheduleAutoAnalyze(100);
      return;
    }

    render(data);
    lastProfileUrl = url;
    failedProfileUrl = null;
    analyzingUrl = null;
    statusEl.textContent = "Done — next profile will analyze automatically.";
  } catch (error) {
    analyzingUrl = null;
    failedProfileUrl = url;
    statusEl.textContent = `Error: ${error.message}\n\nAuto-retry stopped. Click Refresh to retry.`;
  }
}

function scheduleAutoAnalyze(delay = 250) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => analyzeCurrentProfile(false), delay);
}

analyzeBtn.textContent = "Refresh";
analyzeBtn.addEventListener("click", () => analyzeCurrentProfile(true));
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => { if (tab.active && (changeInfo.url || changeInfo.status === "complete")) scheduleAutoAnalyze(changeInfo.url ? 300 : 100); });
chrome.tabs.onActivated.addListener(() => scheduleAutoAnalyze(150));
setInterval(async () => {
  const tab = await getActiveTab();
  if (!tab?.url?.includes("linkedin.com")) return;
  const url = tab.url.split("?")[0];
  if (url !== lastProfileUrl && url !== analyzingUrl && url !== failedProfileUrl) scheduleAutoAnalyze(100);
}, 500);
scheduleAutoAnalyze(150);
