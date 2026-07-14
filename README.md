# Azazil Legal AI — A Contract Assistant That Shows Its Sources

## What is this, in one sentence?

You paste in a contract or ask a legal question in plain English, and Azazil picks the right specialist tool for the job — reviewing your contract against thousands of *real* filed contracts, benchmarking a merger clause against real M&A deals, drafting a new contract from real templates, or searching the live web — and always tells you exactly which real documents it's comparing you against.

```
   💬 "Can you review this NDA?"
              │
              ▼
   🧠  Azazil figures out which tool fits
              │
              ▼
   📚  Looks up REAL comparable contracts
              │
              ▼
   📝  Answer, with citations back to real clauses
```

---

## The single most important idea in this whole project

Most "AI contract review" tools just ask a language model "is this clause bad?" and trust whatever opinion comes back. Azazil refuses to do that. **Every finding must be backed by an actual comparable clause pulled from a real database** — and if nothing genuinely comparable exists, Azazil says so out loud instead of quietly making something up. This single design choice is why the project exists in its current shape, and it's worth understanding, because it explains almost every other decision in the codebase.

```
❌ The lazy way:              ✅ What Azazil actually does:
"This clause looks risky      "This clause looks unusual —
 because... [AI opinion]"      here are 3 real, similar clauses
                                from actual SEC-filed contracts,
                                and here's how yours differs"
```

---

## Meet the four tools — and how Azazil decides which one to use

There's no rigid menu or fixed flowchart deciding what happens next. Instead, one orchestrating AI reads your message and simply *chooses* the right tool for the job, the same way a paralegal would glance at what you handed them and know which specialist to route it to.

| Tool | What it's for | What it's grounded in |
|---|---|---|
| 📄 **review_contract** | General contracts — NDAs, employment agreements, consulting agreements | **CUAD** — a real database of clauses from actual SEC-filed contracts |
| 🤝 **benchmark_ma_provision** | Merger & acquisition specifics — termination fees, no-shop clauses, MAC-outs | **MAUD** — a database of real merger agreement provisions |
| ✍️ **draft_contract** | Writing a brand-new contract, or revising one after a review | A library of real contract templates |
| 🌐 **web_search** | Anything outside the contract databases — case law, current events, general legal questions | Live web search (Tavily) |

If your question doesn't need any of these — like "what does *force majeure* mean?" — Azazil just answers directly, without pretending it needs to "look something up" for a definition it already knows.

### Why M&A gets its own separate tool

You might wonder why merger agreements aren't just reviewed by the same general-purpose tool. The reason: in general contracts, a clause is either standard or it's a red flag — fairly binary. But in M&A, "market standard" is usually a *range* (a termination fee of 2–5% of deal value might all be perfectly normal), and the comparison database itself is different (real merger agreements, not general commercial contracts). Treating it as its own specialized tool, with its own database, keeps the comparisons honest instead of comparing apples (a merger agreement) to oranges (a random NDA).

---

## What happens when a clause looks risky: the auto-negotiation follow-up

Here's a detail that a user would genuinely appreciate: if either the contract-review tool or the M&A-benchmark tool flags a clause as **Medium or High severity**, Azazil doesn't just stop at "this is risky" and leave you there. It automatically pulls in a *third* internal tool — negotiation guidance — grounded in a database of real past deal lessons (fallback positions, escalation paths, and the classic negotiation concepts of BATNA and ZOPA — essentially "what's your walk-away point" and "where do both sides' acceptable ranges overlap").

```
   🚩 Clause flagged Medium/High severity
              │
              ▼ (automatic — you don't ask for this separately)
   💡 "Here's a fallback position you could propose instead,
       based on how similar negotiations were actually resolved"
```

This negotiation tool is deliberately **not** something you can invoke directly by asking for it — it only ever shows up attached to an actual flagged finding. That's intentional: negotiation advice without a specific clause and a specific problem to solve isn't useful; it needs something concrete to be "guidance about."

---

## The part that keeps this honest: what gets filtered out before the AI even sees it

Before any retrieved "comparable clause" reaches the writing AI, the system quietly throws away:

- Fragments that are clearly not real clauses — page headers, table-of-contents lines, bare dates (anything under about 120 characters)
- Comparisons that are a much worse match than the best result for the same search — so a mediocre near-miss doesn't get treated as equally relevant as a strong match

And critically: **the evidence shown to you only includes the comparisons the AI's own explanation actually cites by number** — not every single thing the search happened to retrieve. This keeps the final report from being padded out with irrelevant fragments that had no real bearing on the conclusion, which would just create noise and false confidence.

---

## How "search" actually works here (and why there's no fancy re-ranking)

A detail worth appreciating if you've heard AI-search buzzwords before: this system deliberately has **no intent-classifier step, no query expansion, no result-fusion algorithm, no reranking model**. Those techniques exist to squeeze out marginal accuracy gains in large, complex retrieval systems — but they add real complexity and new failure points. Here, the job of "which tool do I need" is handled by the orchestrator AI simply *picking a tool*, the same way it would pick a word — no separate classification system bolted alongside it.

Under the hood, your contract text (or the clause you're asking about) is turned into a numeric "fingerprint" (called an embedding) and compared against a database of other fingerprinted clauses using plain vector similarity search — essentially "find the clauses whose fingerprint is mathematically closest to this one." It's simple on purpose.

---

## The four real-world databases behind the scenes

| Database | Contents |
|---|---|
| `legal_risks` | Clauses extracted from **CUAD** — a public dataset of real, SEC-filed commercial contracts |
| `maud_clauses` | Provisions from **MAUD** — a dataset of real merger agreements |
| `negotiation_playbook` | Lessons and fallback positions from past real negotiations |
| `contract_templates` | Template contracts used as the basis for drafting |

These live in a Postgres database with a special extension (`pgvector`) that lets it store and search those numeric "fingerprints" efficiently — it's the same kind of database engine you'd use for any app, just with an add-on built for similarity search rather than exact matching.

---

## Two very different jobs, kept strictly apart: reading vs. writing

There are two separate modules for touching the database — one that can only **read** (used by every tool while you're chatting), and one that can only **write** (used exclusively by one-time ingestion scripts that load new contracts into the database). The running chat app never imports the "write" module at all. This is a simple but meaningful safety boundary: nothing you type in a conversation could ever accidentally wipe or alter the underlying legal databases — that capability simply isn't reachable from the chat path.

```
   💬 Chat conversation  ──▶  📖 read-only search   ──▶  the 4 databases
   🛠️ Ingestion scripts   ──▶  ✍️ write/truncate      ──▶  the 4 databases
        (run manually, separately, never by the chatbot itself)
```

---

## Two apps, one job each

- **The backend** (FastAPI) holds all the real logic: the orchestrator, the four tools, the database searches, and every AI call. It's *stateless* — meaning it doesn't remember your conversation between messages on its own. Instead, every single message you send includes your *entire* conversation history so far, and the backend just returns the updated history back to you.
- **The frontend** (Streamlit) is the chat window. It's the one thing actually holding onto "what have we talked about so far" (in the browser session), and it just calls the backend over plain HTTP each time you send a message.

This stateless-backend design is simple and reliable — there's no server-side memory to lose track of, expire, or get corrupted; the whole conversation travels with each request.

---

## Watching your own usage: the Metrics tab

Every single AI call and every tool execution gets logged — which model was used, how long it took, roughly how many tokens were spent, and whether it succeeded. When the AI reports real token counts (which Groq usually provides), those are used directly; otherwise a simple estimate (characters divided by four) fills the gap so the numbers are never blank. This isn't a legal-accuracy score — it's closer to a usage and performance log, similar to what you'd see on a cloud provider's billing dashboard, letting you see what the assistant has actually been doing under the hood.

---

## Why this design earns trust rather than assumes it

The recurring theme across every one of Azazil's tools is the same: **retrieve something real first, then let the AI explain the comparison — never let the AI free-associate an opinion with nothing behind it.** Every flagged clause points back to an actual filed contract. Every M&A benchmark points back to a real merger agreement. Every negotiation suggestion points back to a real past deal outcome. If the underlying database genuinely has nothing comparable, Azazil is built to say exactly that, rather than paper over the gap with a plausible-sounding guess — which is precisely the failure mode that legal users care most about avoiding.
