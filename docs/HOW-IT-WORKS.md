# How RoleRadar works

This guide explains the whole project in plain language. Read it top to
bottom once; after that, use it as a reference.

## 1. The idea in one paragraph

Think of RoleRadar as a small recruitment office working for you.

- A **scout** (the Chrome extension) walks through LinkedIn and brings back
  every job ad it finds.
- A **doorman** (the rule filter) throws out ads that are obviously wrong for
  you: "5+ years required", "German C1", "Head of Data".
- An **assistant** (a free AI model on your laptop) reads the rest and gives
  each one a score: how well do you fit?
- A **writer** (Claude) writes a CV and cover letter for the best ones, using
  only facts from your profile.
- A **manager's desk** (the dashboard) shows you everything. You read the
  documents and apply yourself.

The office has a **filing cabinet** (the database) where every job, score and
decision is kept, and a **receptionist** (the server) who passes messages
between everyone.

## 2. The parts

| Part | In the office story | What it really is | Files |
| --- | --- | --- | --- |
| Server | Receptionist | A small web server on your laptop. Everything talks to it. | `roleradar/api/` |
| Database | Filing cabinet | One file, `data/roleradar.db`, with tables like a spreadsheet | `db.py`, `store.py` |
| Profile | Your file | Three text files describing you and your rules | `profiles/<you>/` |
| Extension | Scout | A Chrome add-on that reads LinkedIn in your signed-in browser | `extension/` |
| Rule filter | Doorman | Pattern matching on the ad's text, instant and free | `rules.py` |
| Ranker | Assistant | A local AI model (llama 3.1) run by the Ollama app | `ranker.py`, `ollama.py` |
| Writer | Writer | Claude Code, run in the background without a window | `builder.py`, `documents.py` |
| Dashboard | Manager's desk | A web page served by the server | `static/` |

## 3. One job's journey, step by step

Let's follow one real-looking job: **"Junior AI Engineer (m/w/d)" at Acme GmbH
in Berlin.**

### Step 1: You save a search

You open a LinkedIn job search in Chrome and click the RoleRadar icon, *Save
this LinkedIn search*. The extension sends the URL to the server, which stores
it in the `searches` table.

The server also checks the URL. If it has no "past week" filter or no "entry
level" filter, it adds them. Why: the cheapest way to avoid a bad job is for
LinkedIn never to show it.

*Code:* `searches.py` (the advice), `routes_searches.py` (saving).

### Step 2: You press "Scrape LinkedIn"

The dashboard tells the server "please scrape". The server can't open LinkedIn
itself: LinkedIn only shows jobs to signed-in people and blocks robots. So the
server just writes a note in the database: *a scrape is waiting*.

*Code:* `scraping.py`, function `request_scrape`.

### Step 3: The extension picks up the note

Every minute, the extension asks the server: "anything for me?" This time the
answer is yes, along with the list of searches. The extension opens a LinkedIn
tab in your Chrome, goes through each search page, clicks each job card, and
reads the title, company, location and description.

It sends the jobs to the server in batches of about 25.

Why the extension always asks and the server never calls it: the server
doesn't need to know where Chrome is, and nothing breaks if Chrome was closed
for a while. The note simply waits.

*Code:* `extension/background.js` (the loop), `extension/linkedin.js` (reading
the page).

### Step 4: The server receives the job and checks its shape

Our job arrives as data:

```json
{"url": "https://www.linkedin.com/jobs/view/4012345678/",
 "title": "Junior AI Engineer (m/w/d)", "company": "Acme GmbH",
 "location": "Berlin, Germany", "description": "You have 1 year of ..."}
```

Before any of our code runs, **FastAPI** compares it to a description of what
a job must look like (a title of 1 to 300 characters, a URL starting with
http, and so on). A broken job is rejected right there with a clear error.

*Code:* `api/schemas.py` (the description), `api/routes_jobs.py` (the door).

### Step 5: Have we seen this job before?

The server gives the job two fingerprints:

- **Same ad?** LinkedIn's job number: `linkedin:4012345678`. If we have it,
  RoleRadar just refreshes it.
- **Same job, new ad?** The company and title, cleaned up:
  `acme|junior ai engineer`. "GmbH" and the German gender marker "(m/w/d)" are
  removed. If an older job has the same fingerprint, this one is a re-post and
  is dropped. If you already applied to the old one, it says so.

*Code:* `keys.py`, `store.py` (`ingest_jobs`).

### Step 6: The doorman (rule filter)

Straight away, the rules read the description and compare it to your
`profile.yaml`:

| Rule | It looks for | Our job |
| --- | --- | --- |
| Years | "3+ years of experience", "mindestens 3 Jahre Berufserfahrung" | "1 year": fine |
| Languages | "Fluent German (C1)", "sehr gute Deutschkenntnisse" | "German is a plus": a wish, not a rule |
| Ad language | Is the whole ad in German? | No |
| Seniority | "Staff", "Lead", "Head of" in the title | No |
| Lane and location | Your out-of-lane list, excluded places | No |

Result: **passed**. It waits for the assistant.

Two promises: the filter only drops on a clear requirement ("ideally 5 years"
never drops a job), and every drop keeps the exact sentence that caused it.

*Code:* `rules.py` (the rules, using patterns called regular expressions),
`filtering.py` (running them).

### Step 7: The assistant scores it (ranking)

You press *Rank*. For each waiting job, RoleRadar sends the local AI a short
message: a summary of you (target roles, years, languages, skills, projects)
and the **requirement sentences** of the ad. Company history and benefits are
left out because they don't change the fit, and every word costs the model
time on a laptop.

The model answers in a fixed format:

```json
{"fit": 86, "junior_friendly": true, "matches": ["Python", "PyTorch"],
 "gaps": ["Kubernetes"], "reason": "Strong match on the core stack"}
```

RoleRadar then adds fixed bonuses from your profile:

```
fit 86  +  Berlin bonus 12  +  title matches a target role 8  =  106
```

A fit below your `min_fit` (45) is dropped as a weak fit, with the model's
reason.

Three speed tricks: the answer is **cached** (the same job is never asked
twice), it runs in the **background** (the dashboard stays usable), and the
*Stop* button finishes the current job and stops cleanly.

*Code:* `ranker.py`, `ollama.py`, `workers.py`.

### Step 8: The writer (CV and cover letter)

You press *Write applications*. For each of the best jobs:

1. **Live check:** RoleRadar loads the posting. If it says "no longer accepting
   applications", the job is marked closed and skipped.
2. **Claude writes:** Claude Code gets your `identity.md`, `projects.md` and
   the full ad, with strict rules (only your facts, one page, no invented
   numbers), and returns the CV and letter as structured data.
3. **Fact check:** every employer, school and project in the answer must
   appear in your profile files. If Claude wrote one that isn't there, the
   answer is rejected and Claude gets one more try, told exactly what was
   wrong.
4. **Layout:** RoleRadar puts the words into its own clean, one-column design,
   and Chrome prints it to PDF.

Claude decides the words. RoleRadar decides the facts and the look.

The files land in `data/applications/<you>/<date>/Acme-GmbH_Junior-AI-Engineer_<id>/`.

*Code:* `livecheck.py`, `builder.py`, `documents.py`, `pdf.py`.

### Step 9: You review and apply

The job is now in *To review* with its score, a one-line fit summary, flags
such as "Asks for Kubernetes", and buttons for the CV, letter and posting. You
read them, apply on the company's site, and press *Applied*.

### Step 10: RoleRadar remembers

Applied jobs stay in the database for good. If Acme re-posts the same role
next month, Step 5 recognises it and drops it with "Already applied".

## 4. Why it is built this way

Each choice solves a specific problem.

| Problem | Choice |
| --- | --- |
| A local AI on a laptop takes about a minute per job | Free rules and LinkedIn's own filters remove most jobs first; the AI reads only the requirement sentences; answers are cached |
| AI writers sometimes invent experience | Claude only gets your files, and its answer is fact-checked against them before anything is saved |
| LinkedIn blocks robots | Scraping happens in your own signed-in Chrome, at human speed |
| Many JSON files can corrupt each other or lock on Windows | One SQLite database, with transactions: a batch is saved completely or not at all |
| Friends need different rules (no German rule for Cairo) | Every rule lives in each person's `profile.yaml`, not in the code |
| Long tasks froze the old dashboard, and progress was wrong | Background threads, and a `runs` table with real "done of total" numbers |
| Postings close before you apply | Past-week search filter, plus a live check right before writing |
| You can't trust a black box | Every drop shows its evidence sentence; every score shows its parts |

## 5. Words you will meet

| Word | Meaning here |
| --- | --- |
| Server | A program that waits for requests and answers them. Ours runs on your laptop at `127.0.0.1:8787`. |
| API | The list of requests the server understands, like `POST /api/profiles/omar/rank`. |
| Endpoint | One of those requests. |
| GET / POST | GET asks for information; POST sends something or starts an action. |
| FastAPI | The Python library the server is built with. It checks incoming data automatically and makes the `/docs` page. |
| JSON | The text format all parts use to send data to each other. |
| Schema / validation | A description of what valid data looks like, and the check against it. |
| SQLite | A database stored in a single file. |
| Migration | A numbered script that upgrades the database. Your data is kept when RoleRadar gets new features. |
| Transaction | A group of database changes that happen all together, or not at all. |
| Regular expression (regex) | A pattern that matches text, like "a number, then 'years', then 'experience'". |
| LLM | Large language model: the kind of AI that reads and writes text. |
| Ollama | The app that runs open LLMs on your own computer. |
| Cache | Saved answers, so the same question is never asked twice. |
| Background thread | Work that runs alongside the server, so the dashboard keeps responding. |
| Headless | Running a program without a window (Claude Code, Chrome for PDFs). |
| Extension (Manifest V3) | A Chrome add-on; "Manifest V3" is the current format Chrome requires. |
| Test | A small program that checks one behaviour automatically. RoleRadar has 146. |

## 6. Explaining it in an interview

**In 30 seconds:**

> "RoleRadar is a local job search pipeline I built in Python. A Chrome
> extension collects LinkedIn jobs in my own browser and sends them to a
> FastAPI server with a SQLite database. A rule layer drops clear mismatches
> like required years or German level, with the evidence sentence for each
> drop. A local LLM through Ollama ranks the rest, using structured JSON output
> and caching. For the top matches, Claude writes a CV and cover letter, which
> my code fact-checks against my profile before rendering a PDF. The design
> principle is cheap filtering first and expensive AI last."

**Questions you may get, with answers:**

- *Why not send every job to the AI?* On a laptop each job takes about a
  minute. Rules handle the clear-cut cases in milliseconds, so the model only
  sees jobs where judgement matters.
- *How do you stop the AI from inventing things in the CV?* It only receives
  my profile files, and the output is checked: every employer, school and
  project must exist in those files, or it is rejected and retried.
- *Why SQLite and not a big database?* It is a single-user tool on a laptop.
  SQLite needs no server, is one file, and supports transactions and safe
  concurrent reads.
- *Why FastAPI?* Automatic validation of every request, an interactive docs
  page, and clean separation into routes. It also made background tasks and
  progress reporting straightforward.
- *How do you test something that depends on LinkedIn and AI models?* The AI
  and job sites are replaced by small fakes in the tests. The scraper is run
  in a real headless Chromium against fake LinkedIn pages, including one where
  every CSS class is renamed, to test the fallbacks.
- *What would you improve next?* More job boards (for example Wuzzuf for
  Egypt), and measuring the ranker's accuracy against my own decisions.

## 7. Reading the code

A good order, from simplest to most involved:

1. `profiles/example/profile.yaml`: the rules, in plain YAML
2. `roleradar/keys.py`: short, and the re-post trick is neat
3. `roleradar/rules.py`: the filter; read one rule at a time
4. `roleradar/api/routes_jobs.py`: how a request becomes a function call
5. `roleradar/ranker.py`: prompt, cache, score
6. `roleradar/builder.py` and `documents.py`: writing and fact-checking
7. `extension/background.js`: the scraping loop

Every file starts with a comment explaining what it is for.
