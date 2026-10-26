// The extension's background worker.
//
// Every minute it asks the local RoleRadar server "anything to scrape?".
// When the answer is a scrape request, it opens a LinkedIn tab in your
// Chrome, works through the saved searches page by page, sends the jobs to
// RoleRadar, and closes the tab. The tab stays in front while it works:
// LinkedIn does not load job lists in hidden tabs.

import { api } from "./api.js";
import { scrapeSearchPage } from "./linkedin.js";

const POLL_MINUTES = 1;

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("poll", { periodInMinutes: POLL_MINUTES }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("poll", { periodInMinutes: POLL_MINUTES }));
chrome.alarms.onAlarm.addListener((alarm) => alarm.name === "poll" && checkIn());

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type === "checkNow") {
    checkIn().then(() => reply({ ok: true }), (e) => reply({ ok: false, error: String(e) }));
    return true; // the reply comes later
  }
});

async function setBadge(text, color = "#0F6E56") {
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
}

async function busy() {
  const { scrapingSince } = await chrome.storage.session.get("scrapingSince");
  // A lock older than 30 minutes belongs to a run that was cut off.
  return scrapingSince && Date.now() - scrapingSince < 30 * 60 * 1000;
}

export async function checkIn() {
  if (await busy()) return;
  let task;
  try {
    task = await api("/api/extension/next");
    await chrome.storage.local.set({ lastContact: Date.now(), lastError: null });
  } catch (error) {
    await chrome.storage.local.set({ lastError: "RoleRadar is not running (start.bat)" });
    await setBadge("off", "#888780");
    return;
  }
  if (!task.request_id) {
    await setBadge("");
    return;
  }
  await chrome.storage.session.set({ scrapingSince: Date.now() });
  try {
    await runScrape(task);
  } finally {
    await chrome.storage.session.remove("scrapingSince");
  }
}

function pageUrl(searchUrl, page) {
  const url = new URL(searchUrl);
  if (page > 0) url.searchParams.set("start", String(page * 25));
  else url.searchParams.delete("start");
  return url.toString();
}

function waitForLoad(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    const listener = (id, info) => id === tabId && info.status === "complete" && done();
    const timer = setTimeout(done, timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function openInTab(tabId, url) {
  const loaded = waitForLoad(tabId);
  await chrome.tabs.update(tabId, { url, active: true });
  await loaded;
}

async function runScrape(task) {
  const { request_id: requestId, profile, searches } = task;
  const progress = (done, message) =>
    api(`/api/extension/requests/${requestId}/progress`, { method: "POST", body: { done, message } });
  const finish = (ok, message) =>
    api(`/api/extension/requests/${requestId}/finish`, { method: "POST", body: { ok, message } });

  await setBadge("…");
  const tab = await chrome.tabs.create({ url: "about:blank", active: true });
  const totals = { jobs: 0, passed: 0, dropped: 0 };
  let failure = null;

  try {
    for (const [index, search] of searches.entries()) {
      const status = await progress(index, `Scraping "${search.label}" (${index + 1} of ${searches.length})`);
      if (status.cancelled) {
        failure = "cancelled";
        break;
      }
      for (let page = 0; page < search.pages; page++) {
        await openInTab(tab.id, pageUrl(search.url, page));
        const [{ result }] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: scrapeSearchPage,
          args: [{}],
        });
        if (result.problem === "signed_out") {
          throw new Error("You are signed out of LinkedIn. Sign in in Chrome, then scrape again.");
        }
        if (!result.jobs.length) break; // no (more) results for this search
        const answer = await api(`/api/profiles/${profile}/jobs`, {
          method: "POST",
          body: { source: "linkedin", search_id: search.id, jobs: result.jobs },
        });
        totals.jobs += result.jobs.length;
        totals.passed += answer.passed;
        totals.dropped += answer.dropped;
      }
    }
  } catch (error) {
    failure = error.message || String(error);
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }

  if (failure === "cancelled") return setBadge("");
  const summary = `${searches.length} searches, ${totals.jobs} jobs read: ${totals.passed} passed the filter, ${totals.dropped} dropped`;
  if (!failure) await progress(searches.length, summary).catch(() => {});
  await finish(!failure, failure ? `Scrape failed: ${failure}` : summary).catch(() => {});
  await setBadge(failure ? "!" : String(totals.passed), failure ? "#A32D2D" : "#0F6E56");
}
