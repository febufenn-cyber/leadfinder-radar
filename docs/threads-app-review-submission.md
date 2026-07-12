# Threads App Review — `threads_keyword_search` Advanced Access

Submission pack for **Robofox Radar** (App ID `1364970699034871`, Threads app id `2190945941703526`).
Goal: move `threads_keyword_search` from Standard (own posts only) to **Advanced Access** (public posts), which unlocks Threads discovery for the LeadFinder radar.

Path in console: App → Use cases → *Access the Threads API* → Permissions → `threads_keyword_search` → **Actions → Add to App Review**. Then fill the description, attach the screencast, and submit.

> **Framing rule (matters for approval):** present this as a **social-listening / inbound-triage tool for our own business**, with a **human approving every reply**. Do NOT describe it as "AI auto-reply" or "bulk outreach" — automated/unsolicited posting is the fastest rejection. Every word below is true of the system as built (§0 of DESIGN: nothing posts without owner approval); we're just leading with the compliant, accurate description.

---

## 1. Permission use-case description (the main text field)

> Paste this into "Tell us how your app uses `threads_keyword_search`." Keep it, trim if there's a character cap — the first paragraph is the load-bearing one.

Robofox Radar is a private, single-operator social-listening tool for our own small business (RoboFox, a web & AI studio, and our study-abroad service). We use `threads_keyword_search` to find **public** Threads posts where people are openly asking for help we offer — e.g. "need a website for my shop", "looking for a web developer", "how do I apply to study in Germany", "need IELTS coaching." The app queries a small, fixed set of business-relevant keywords on a slow schedule, scores each matching public post for relevance, and surfaces the strongest matches to the single operator (the business owner) in a private Telegram channel.

Critically, the app takes **no action on Threads automatically**. For each surfaced post the operator reviews it on their phone and decides whether to reply. Any reply is drafted as a helpful, context-specific response and is only published after the operator manually approves that specific reply (using `threads_manage_replies`, already granted). There is no bulk messaging, no unsolicited DMs, no automated posting, and no scraping — all data comes through the official Threads API. Keyword queries are budgeted well under the documented rate limit (a few dozen per day across all keywords).

Data handling: matched public posts are stored transiently in our own private database (post id, text, author handle, permalink, timestamp) solely so the operator can review and decide whether to respond. Data is never sold, shared with third parties, or used for advertising. It is used only to help our business respond helpfully to people who have publicly asked for exactly the services we provide.

## 2. Why we need Advanced Access specifically

State plainly:

- With Standard Access, `threads_keyword_search` only returns the authenticated user's own posts, which is useless for finding people who need our help.
- We need to search **public** posts by keyword to identify inbound demand — the same thing a person would do by manually searching Threads, just organized for one operator.
- Volume is low and human-gated: a fixed keyword list, polled a few times a day, with a human reviewing and approving every single response.

## 3. Data-handling / privacy answers (if asked as separate fields)

- **What data do you access?** Public Threads posts matching our business keywords: post id, text, permalink, author username, timestamp. Only public content.
- **How is it stored?** In our own private, access-controlled database on our server. Not shared externally.
- **How long is it retained?** Only as long as needed for the operator to review and act; stale/irrelevant posts are pruned.
- **Do you share it?** No. No third parties, no ad use, no resale.
- **Who can access it?** One operator (the business owner), behind authentication.

---

## 4. Screencast / demo video

Meta requires a screencast showing the permission working end to end. Reviewers must see: the API call being made, real results returned, and how the data is used in-product. Keep it 1–3 minutes, screen-recorded, no editing tricks.

### 4a. Script (voiceover / on-screen narration)

1. **Intro (10s):** "This is Robofox Radar, a private social-listening tool we use to find public Threads posts from people asking for the services our business offers. I'm the only operator. I'll show how `threads_keyword_search` is used."
2. **Show the keyword config (15s):** Open the pack config / dashboard showing the fixed business keyword list (e.g. "need a website", "study in germany", "ielts coaching"). "These are the only terms we search — all directly tied to what we offer."
3. **Show the live API call (25s):** In a terminal or the dashboard, trigger a keyword search and show the raw Threads API request to `/keyword_search` and the JSON response with public posts. "The app calls the official Threads keyword search endpoint and gets back public posts matching the term."
4. **Show the review surface (25s):** Show the matched post arriving in the operator's private Telegram (post preview, relevance score, and Reply/Skip buttons). "Each match is shown privately to me. Nothing happens automatically."
5. **Show human approval (20s):** Tap into one, show a drafted helpful reply, and show that it only posts after I explicitly approve it. "I review and approve each reply individually before anything is posted. No bulk or automated actions."
6. **Close (10s):** "That's the full use: search public posts for people asking for help, review privately, respond manually. Low volume, human-approved, official API only."

### 4b. What must be visible on screen (reviewer checklist)

- The actual `graph.threads.net/.../keyword_search?q=...` request and its JSON response (open dev tools / terminal / logs so the endpoint and params are legible).
- Real returned public posts (not mocked).
- The private, single-operator review UI (Telegram cards).
- The manual approve step gating any reply.
- No automation, no mass action, no DMs.

### 4c. How to record it (concrete steps for this system)

Because the app runs on the VPS + Telegram, the cleanest screencast is a terminal + phone capture:

1. On the VPS, run a one-off keyword search with the granted token and pretty-print it, e.g.:
   `curl -s "https://graph.threads.net/v1.0/keyword_search?q=need%20a%20website&search_type=RECENT&fields=id,text,username,permalink,timestamp&limit=5&access_token=$TOK" | python3 -m json.tool`
   — screen-record the terminal so the endpoint + real results show. (Note: at Standard access this returns only robo_f0x's own posts, so **record the real public results during/after approval, OR** record against our own seeded test posts and narrate that public results appear once approved. Simplest: record the call + response shape now, and the Telegram review flow, which fully demonstrates intended use.)
2. Screen-record the dashboard at `https://leadfinder.robofox.online` showing matched posts / the funnel.
3. Screen-record the Telegram approval card (phone screen mirror) showing Reply/Skip and the manual approve.
4. Stitch into one clip, add the voiceover from 4a. Upload as the App Review screencast.

---

## 5. Prerequisites & likely extra requirements (be ready for these)

Meta desk-rejects on missing basics more often than on substance. Before clicking "Add to App Review", fill everything in **App settings → Basic**:

- **Privacy Policy URL** — must be a live page (e.g. `https://robofox.online/privacy`). Create it if it doesn't exist; it should cover exactly what §3 states (public Threads posts + own tokens; why; retention; no sale/sharing; contact email). Empty = reject.
- **App icon (1024×1024)** and **Category** (e.g. "Business and pages" or "Productivity"). An empty icon is an instant desk-reject.
- Every listed Basic-settings field filled; leave the app in Development (Threads review handles mode).
- **Business verification** and/or **data-access/security verification** may be triggered for Threads advanced access — complete it with Robofox's details (name, robofox.online, contact). Adds days.
- Reviewers may ask for **test credentials or a walkthrough** — the screencast usually suffices for a single-operator tool, but be ready to add reviewer test steps.

## 6. Honest expectation-setting

- Approval favors clear, low-risk, well-scoped use cases (Meta's Responsible Builder policy). This one is legitimate but keyword-search-on-public-content gets scrutiny, so the human-in-the-loop framing and a clean screencast matter.
- Turnaround is typically days to a couple of weeks.
- If rejected, the usual reasons are: framing that sounds like automation/spam, a screencast that doesn't clearly show the endpoint + real use, or missing business/privacy verification. All fixable and resubmittable.
- **Fallback while this is pending:** the Google Programmable Search bridge (`threads_cse` adapter) discovers public Threads posts via Google's index with no Meta review — set `GOOGLE_CSE_KEY` + `GOOGLE_CSE_ID`. Threads *sending* already works regardless.
