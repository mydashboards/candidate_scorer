const analyzeBtn = document.getElementById("analyze");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const scoreEl = document.getElementById("score");
const decisionEl = document.getElementById("decision");
const summaryEl = document.getElementById("summary");
const criteriaEl = document.getElementById("criteria");

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function extractFromTab(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_PROFILE" });
  } catch (_) {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"]
    });
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
    row.innerHTML = `
      <div class="criterionTop">
        <span>${item.label}</span>
        <span>${item.points}/${item.max_points}</span>
      </div>
      <div class="evidence">${item.status.toUpperCase()} — ${item.evidence}</div>
    `;
    criteriaEl.appendChild(row);
  }

  resultEl.classList.remove("hidden");
}

analyzeBtn.addEventListener("click", async () => {
  resultEl.classList.add("hidden");
  statusEl.textContent = "Analyzing...";

  try {
    const tab = await getActiveTab();
    if (!tab?.id || !tab.url?.includes("linkedin.com")) {
      throw new Error("Open a LinkedIn profile first.");
    }

    const extracted = await extractFromTab(tab.id);
    if (!extracted?.ok) {
      throw new Error(extracted?.error || "Could not read profile.");
    }

    const response = await fetch("http://127.0.0.1:3847/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(extracted.profile)
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(err);
    }

    const data = await response.json();
    render(data);
    statusEl.textContent = "Done.";
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  }
});
