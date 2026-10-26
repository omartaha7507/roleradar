// The small window that opens when you click the RoleRadar icon in Chrome.

import { api, currentProfile, serverUrl } from "./api.js";
import { readOpenJob } from "./linkedin.js";

const $ = (id) => document.getElementById(id);

function show(text, isError = false) {
  const box = $("message");
  box.textContent = text;
  box.className = "show" + (isError ? " error" : "");
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function openJobOnPage(tab) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readOpenJob });
  return result;
}

async function init() {
  $("server").value = await serverUrl();
  $("dashboard").onclick = async (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: await serverUrl() });
  };

  let profiles = [];
  try {
    profiles = (await api("/api/profiles")).filter((p) => p.valid);
    $("dot").classList.add("on");
  } catch {
    show("RoleRadar is not running. Start it with start.bat, then open this again.", true);
    document.querySelectorAll("button").forEach((b) => (b.disabled = true));
    return;
  }
  const saved = await currentProfile();
  $("profile").innerHTML = profiles
    .map((p) => `<option value="${p.name}">${p.display_name} (${p.name})</option>`)
    .join("");
  const chosen = profiles.find((p) => p.name === saved) || profiles.find((p) => p.name !== "example") || profiles[0];
  if (chosen) {
    $("profile").value = chosen.name;
    await chrome.storage.local.set({ profile: chosen.name });
  }

  const tab = await activeTab();
  const onLinkedIn = /^https:\/\/www\.linkedin\.com\/jobs\//.test(tab?.url || "");
  const onJob = onLinkedIn && /(\/jobs\/view\/|currentJobId=)/.test(tab.url);
  const onSearch = onLinkedIn && /\/jobs\/search/.test(tab.url);
  $("addJob").disabled = !onJob;
  $("applied").disabled = !onJob;
  $("addSearch").disabled = !onSearch;
}

$("profile").onchange = () => chrome.storage.local.set({ profile: $("profile").value });
$("server").onchange = () => chrome.storage.local.set({ server: $("server").value.trim() }).then(() => location.reload());

$("scrape").onclick = async () => {
  const profile = $("profile").value;
  try {
    const answer = await api(`/api/profiles/${profile}/scrape`, { method: "POST", body: {} });
    show(`Starting: ${answer.searches} searches. A LinkedIn tab will open; leave it in front until it closes.`);
    chrome.runtime.sendMessage({ type: "checkNow" });
  } catch (error) {
    show(error.message, true);
  }
};

$("addJob").onclick = async () => {
  try {
    const job = await openJobOnPage(await activeTab());
    if (!job?.title || !job?.company) throw new Error("Could not read this job. Open the job so its details show, then try again.");
    const answer = await api(`/api/profiles/${$("profile").value}/jobs`, {
      method: "POST",
      body: { source: "linkedin", jobs: [job] },
    });
    if (answer.updated) show("RoleRadar already had this job (refreshed it).");
    else if (answer.reposts) show("Added, but it is a re-post of a job RoleRadar already has.");
    else if (answer.dropped) show("Added, but the rule filter dropped it. See the reason on the dashboard.");
    else show("Added. It passed the rule filter and will be ranked next.");
  } catch (error) {
    show(error.message, true);
  }
};

$("applied").onclick = async () => {
  try {
    const job = await openJobOnPage(await activeTab());
    if (!job) throw new Error("Open the job first.");
    const saved = await api(`/api/profiles/${$("profile").value}/applied`, {
      method: "POST",
      body: { url: job.url, title: job.title, company: job.company },
    });
    show(`Marked applied: ${saved.title} at ${saved.company}`);
  } catch (error) {
    show(error.message, true);
  }
};

$("addSearch").onclick = async () => {
  try {
    const tab = await activeTab();
    const params = new URL(tab.url).searchParams;
    const label = [params.get("keywords"), params.get("location")].filter(Boolean).join(", ") || "LinkedIn search";
    const saved = await api(`/api/profiles/${$("profile").value}/searches`, {
      method: "POST",
      body: { label: label.slice(0, 80), url: tab.url },
    });
    let text = `Saved search: ${saved.label}`;
    if (saved.hints.length) {
      text += "\n\nTips:\n• " + saved.hints.join("\n• ");
      if (saved.suggested_url) {
        await api(`/api/profiles/${$("profile").value}/searches/${saved.id}`, {
          method: "PATCH",
          body: { url: saved.suggested_url },
        });
        text += "\n\nRoleRadar added the suggested filters for you.";
      }
    }
    show(text);
  } catch (error) {
    show(error.message, true);
  }
};

init();
