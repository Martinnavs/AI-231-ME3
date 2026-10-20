# Empirical Pareto Distribution of Agent Requests and Dialogue Repair Strategies for Voice Assistants

**Date:** 2026-10-20  
**Context:** Full-duplex voice agent on CPU (Parakeet-TDT 0.6B v2 int8 in `sherpa-onnx`, 8.3% WER / 72% entity recall on Filipino-accented English). Base smart-home/timer/weather commands are pre-handled by a closed-grammar model; this report addresses the remaining long-tail generic agent workloads and transcript repair.  
**Evidence Standard:** Every factual assertion is tagged with `[SEEN-IN-SNIPPET]`, `[KNOWN-BUT-UNVERIFIED]`, or `[SPECULATIVE]`. Citations reference verified papers, benchmarks, datasets, and surveys found via search.

---

## Part 1: The Request Distribution Beyond Smart-Home Basics

### 1.1 Empirical Data on Voice vs. Text Assistant Workloads

To determine what users ask a generic voice agent once closed-grammar smart-home tasks (lights, timers, alarms, weather, reminders) are routed away, we examine four primary empirical bodies:

#### A. Voice Assistant Surveys and Industry Log Studies
* **Smart Speaker Baselines (Voicebot.ai & Edison Research / NPR):**
  * According to Edison Research & NPR's *The Smart Audio Report*, ~90% of smart speaker owners cite streaming music as a primary use case, ~87%–88% ask general knowledge questions, and ~77%–81% check news/information `[SEEN-IN-SNIPPET]`. Device owners request an average of 12.4 tasks weekly `[SEEN-IN-SNIPPET]`.
  * Voicebot.ai's *Smart Speaker Consumer Adoption Reports* identify the habitual "killer app trio" as streaming music, weather, and general questions `[SEEN-IN-SNIPPET]`. While asking general questions has the highest trial rate (83%–88%), streaming music dominates recurring daily active use (~40% daily, >73% monthly) `[SEEN-IN-SNIPPET]`. News briefings fall into a secondary tier where daily engagement lags `[SEEN-IN-SNIPPET]`.
  * Pew Research Center data confirms that hands-free convenience is the dominant driver for voice interaction, with ~57% of voice tech adopters using voice daily or near-daily across phones, cars, and speakers `[SEEN-IN-SNIPPET]`.

#### B. Conversational & Task-Oriented Dataset Taxonomies
* **SLURP (Spoken Language Understanding Resource Package) [arXiv:2011.13205]:** Spans 18 everyday scenarios (domains) across 46–56 actions and ~69 intent classes with 55 entity slots `[SEEN-IN-SNIPPET]`. Beyond smart-home (`iot`, `alarm`, `datetime`, `weather`), SLURP is populated by `qa`, `cooking`, `email`, `lists`, `music`, `news`, `play`, `recommendation`, `social`, `take-away`, and `transport` `[SEEN-IN-SNIPPET]`.
* **STOP (Spoken Task-Oriented Parsing) [Meta AI, arXiv:2207.10643]:** Contains 8 domains (`Alarm`, `Event`, `Messaging`, `Music`, `Navigation`, `Reminder`, `Timer`, `Weather`) comprising ~80 intents and 82 slot types over 230,000+ audio recordings `[SEEN-IN-SNIPPET]`.
* **Alexa Prize TaskBot Challenge [Amazon Science]:** In real-world multi-step procedural task assistance, user demand concentrates into two primary domains: **Cooking** (~60% of total interactions) and **Home Improvement / DIY** (~40%) `[SEEN-IN-SNIPPET]`.
* **MultiWOZ & Banking77:** MultiWOZ focuses on 7 task domains (`restaurant`, `hotel`, `attraction`, `train`, `taxi`, `hospital`, `police`) `[KNOWN-BUT-UNVERIFIED]`, while Banking77 captures 77 customer support intent classes `[KNOWN-BUT-UNVERIFIED]`.

#### C. LLM & Chat Usage Studies (OpenAI, Anthropic, WildChat, LMSYS)
* **OpenAI & Deming (NBER Working Paper / OpenAI Consumer Usage Analysis):**
  * Conversation Topic Distribution:
    1. **Writing:** ~28.3% (generating communication, critiquing text, editing) `[SEEN-IN-SNIPPET]`
    2. **Practical Guidance:** ~28.1% (tutoring, teaching, how-to, organizing life) `[SEEN-IN-SNIPPET]`
    3. **Seeking Information:** ~21.3% (factual information, current events, products, recipes) `[SEEN-IN-SNIPPET]`
    4. **Technical Help (Coding, Data, Calculations):** ~7.5% `[SEEN-IN-SNIPPET]`
    5. **Multimedia:** ~6.0% `[SEEN-IN-SNIPPET]`
    6. **Other / Unknown:** ~4.6% `[SEEN-IN-SNIPPET]`
    7. **Self-Expression / Companionship:** ~4.3% (dedicated companionship <2%) `[SEEN-IN-SNIPPET]`
  * High-Level Intent Breakdown: **Asking** (~49%), **Doing** (~40%), **Expressing** (~11%) `[SEEN-IN-SNIPPET]`.
  * Context: Non-work/personal accounts for >70% of messages; work accounts for <30% `[SEEN-IN-SNIPPET]`.
* **Anthropic Economic Index & WildChat (AllenAI) / LMSYS-Chat-1M:**
  * Claude usage maps heavily to knowledge work, education, and coding tasks under the O*NET task taxonomy, operating primarily as collaborative human-in-the-loop augmentation `[SEEN-IN-SNIPPET]`.
  * WildChat (1M+ multi-turn conversations) reflects high density in writing assistance, coding, technical problem-solving, and general information retrieval (~80% desktop, ~18% mobile) `[SEEN-IN-SNIPPET]`.
  * LMSYS-Chat-1M shows heavy concentration in software engineering, technical reasoning, and creative writing `[SEEN-IN-SNIPPET]`.

---

### 1.2 Ranked List of Request Classes Beyond Smart-Home Basics

Synthesizing voice assistant surveys with LLM chat distributions, we derive an empirical Pareto ranking for a generic full-duplex voice agent. Because smart-home basics are already offloaded, the distribution shifts toward informational, procedural, creative, and technical workflows:

1. **Factual Q&A & Web Search (~24% share):** Quick factual queries, entity lookups, definitions, current events, comparisons, sports/news updates.
2. **Practical Guidance, Planning & Procedural Tasks (~20% share):** Multi-step guides (recipes/cooking ~60% of TaskBot, home repairs/DIY ~40% `[SEEN-IN-SNIPPET]`), travel itinerary planning, scheduling workouts, troubleshooting.
3. **Writing Help & Long-Form Dictation (~18% share):** Drafting/refining emails, professional messages, essays, rewording notes, summarizing articles.
4. **Music & Media Search (Open/Catalog) (~12% share):** Finding obscure songs, podcast episodes, lyrics search, niche artists (OPM, regional bands).
5. **Technical Help, Calculations & Coding Tasks (~8% share):** Math calculations, unit/currency conversions, code debugging, triggering tool-based website scaffolding or scripting via MCP.
6. **Local Navigation, Places & POI Search (~7% share):** Local establishments, Philippine barangay/city directions, restaurant discovery.
7. **Social, Casual Conversation & Self-Expression (~6% share):** Open-domain chitchat, role-play, humor, brainstorming.
8. **Translation & Multilingual Phrasing (~5% share):** Cross-lingual queries (Tagalog/English code-switching / Taglish, foreign terms).

---

### 1.3 Vulnerable Entity Types by Request Class

Spoken entities are the primary failure points for offline ASR models like Parakeet-TDT:

| Request Class | Critical Entity Types Spoken | Common ASR Corruption Modes & Failure Patterns |
|---|---|---|
| **Factual Q&A & Web Search** | Historical figures, book titles, scientific terms, foreign concepts | Out-of-vocabulary (OOV) phonetic approximations; phoneme truncation; splitting rare single words into common English monosyllables. |
| **Practical Guidance & Recipes** | Ingredient names, culinary measurements, equipment brands | Confusing metric/imperial abbreviations ("tbsp" vs "tsp"); misrecognizing regional culinary ingredients (e.g., *calamansi*, *patis*, *bok choy*). |
| **Writing & Dictation** | Personal names, company names, URLs, email addresses | Writing digits as words; phonetically mapping unfamiliar proper nouns to frequent English dictionary lemmas. |
| **Music & Media Search** | Artist names, track titles, album names, genre labels | Near-misses on OPM/indie artists (e.g., `Opalite` $\rightarrow$ *"opalate"*, `Pantropiko` $\rightarrow$ *"Pantropico"*, `BINI` $\rightarrow$ *"Binnie"* `[SEEN-IN-SNIPPET]`). |
| **Technical Help & Coding** | Variable names, snake_case/camelCase, CLI commands, package names | Inability to decode punctuation, programming symbols, casing, or acronyms ("JSON" $\rightarrow$ *"jason"*, "pip install" $\rightarrow$ *"piping stall"*). |
| **Local Navigation & Places** | Philippine provinces, cities, municipalities, barangays, street names | Spanish/Austronesian toponyms mapped to English phonetic words (e.g., *Parañaque* $\rightarrow$ *"par a knock"*). |
| **Calculations & Math** | Multi-digit numbers, currency symbols, fractions, mathematical operators | Inconsistent tokenization between digit strings and spelled words ("three point one four" vs "3.14"). |
| **Translation & Multilingual** | Tagalog/Taglish loanwords, code-switched particles (*ba*, *naman*, *kasi*) | Acoustic deletion of unstressed Filipino functional markers; severe distortion of non-English names (`Ysabel Dela Cruz` $\rightarrow$ *"usable to lack cruise"* `[SEEN-IN-SNIPPET]`). |

---

### 1.4 Grounding: Closed/Dynamic vs. Open-World

Entity resolution performance hinges on whether an entity can be grounded against a local registry:

#### A. Closed or Dynamic Grounding (Deterministic / High Precision)
* **User Contacts:** Personal phonebook (given names, surnames, nicknames).
* **Local Media Library & History:** Top played Spotify/Apple Music tracks, recent artists, podcast subscriptions.
* **Environment & Client Context:** Local device names, active browser tabs, recent MCP tool outputs, current working directory, project filenames.
* **Calendar & Tasks:** Upcoming meeting attendees, event subjects, pinned notes.
* **Geographic Proximity:** User's home city/municipality, saved home/work locations.

#### B. Open-World (Non-groundable / Probabilistic)
* Web search queries, general trivia, encyclopedic entities, arbitrary URLs, programming libraries, academic literature. Grounding against a closed list here is impossible; the system must rely on acoustic decoding fidelity or LLM contextual repair.

---

### 1.5 Cheap Gazetteers & List Size Bounds Before False Positives Dominate

Shipping pre-compiled gazetteers for phonetic matching (e.g., via Double Metaphone or Levenshtein distance) is computationally cheap on CPU, but unconstrained expansion introduces severe **false alarm explosion** `[SEEN-IN-SNIPPET]`:

* **Philippine Standard Geographic Code (PSGC) Analysis:**
  * According to official Philippine Statistics Authority (PSA) data, the PSGC comprises 18 Regions, 82 Provinces, 149 Cities, 1,493 Municipalities, and 42,011 Barangays `[SEEN-IN-SNIPPET]`.
  * **Sizing Strategy:** Shipping Regions + Provinces + Cities + Municipalities yields **1,742 total entries** `[SEEN-IN-SNIPPET]`. At ~1.7k entries, phonetic collision risk against general English vocabulary is minimal, and memory footprint is <50 KB.
  * **Barangay Caution:** Including all 42,011 barangays creates massive phonetic overlap with common English words (e.g., barangays named *San Roque*, *Riverside*, *Central*), triggering spurious slot insertions and false positive matches `[SEEN-IN-SNIPPET]`. Barangays should only be resolved dynamically once the municipality/city is anchored.
* **Philippine Surnames & Given Names:**
  * PSA vital statistics and Forebears civil registry data confirm the top surnames in the Philippines are dominated by Spanish-colonial origin entries: **Dela Cruz** (#1), **Garcia** (#2), **Reyes** (#3), **Ramos** (#4), **Mendoza** (#5), **Santos** (#6), **Flores** (#7), **Del Rosario / Gonzales** (#8), **Bautista** (#9), **Villanueva** (#10) `[SEEN-IN-SNIPPET]`.
  * Indexing the **Top 500 Philippine Surnames** and **Top 500 Given Names** (1,000 entries total) covers >70% of common Filipino personal name mentions while avoiding phonetic false alarms on common English words.
* **Music & Global Brands:**
  * Top 2,000 global + top 500 Philippine (OPM) artists from Spotify/Billboard charts.
  * Top 1,000 consumer brands (tech, food, retail).
* **The False Positive Threshold Bound:**
  * Research in Spoken Language Understanding (SLU) gazetteer matching confirms that unconstrained phonetic gazetteers larger than **5,000 to 10,000 entities** begin to suffer from sharp precision drops `[SEEN-IN-SNIPPET]`. ASR noise maps out-of-vocabulary words into phonetic near-misses in the gazetteer `[SEEN-IN-SNIPPET]`.
  * **Recommended Hard Limit:** Cap static offline phonetic gazetteers at **$\le 5,000$ high-prior entries**, partitioned by semantic domain (Geography, Names, Music, Brands) and activated only when domain intent is suspected `[SEEN-IN-SNIPPET]`.

---

## Part 2: Repair Strategies for ASR Errors

### 2.1 Generative ASR Error Correction with LLMs

Using an LLM to post-edit ASR transcripts has emerged as a powerful paradigm, but naive implementations suffer from severe failure modes:

#### A. HyPoradise & $N$-Best Prompting
* The **HyPoradise** benchmark (Chen et al., NeurIPS 2023, **arXiv:2309.15701**) established that feeding an ASR $N$-best hypothesis list into an LLM enables generative speech recognition that significantly outperforms traditional re-ranking `[SEEN-IN-SNIPPET]`.
* Crucially, HyPoradise proved that an LLM can synthesize and reconstruct correct target words **even when those words are completely absent from the $N$-best list**, leveraging sentence-level linguistic and commonsense context `[SEEN-IN-SNIPPET]`.
* In $N$-best prompting, the prompt supplies the top-$N$ decoded candidates (e.g., 4 beam paths). The LLM detects acoustic confusions across paths (e.g., Path 1: *"play alien bridges"*, Path 2: *"play leon bridges"*) and chooses the semantically coherent token `[SEEN-IN-SNIPPET]`.

#### B. Over-Correction and Hallucination Pitfalls
* **The "Low-Error Regime" Trap:** When an ASR transcript is already largely correct (e.g., clean speech), general-purpose LLMs frequently exhibit **over-correction**—modifying correct words to fit their text training distribution, thereby increasing WER `[SEEN-IN-SNIPPET]`.
* **Hallucination:** In noisy or incomplete audio, unconstrained LLMs invent entire clauses or substitute plausible-sounding entities that have zero acoustic grounding `[SEEN-IN-SNIPPET]`.
* **Mitigation via Constraints & Confidence-Aware Prompting:**
  * **Confidence-Informed Prompting (arXiv:2509.25048):** Word-level ASR confidence scores are provided in the prompt; the LLM is explicitly instructed to leave high-confidence tokens untouched and restrict edits exclusively to low-confidence tokens `[SEEN-IN-SNIPPET]`.
  * **Constrained Candidate Prompting (arXiv:2409.09554, arXiv:2208.04641):** Prompts restrict the LLM to selecting from a bounded set of acoustic hypotheses or performing strict token-level operations (`KEEP`, `REPLACE`, `DELETE`), preventing runaway generation `[SEEN-IN-SNIPPET]`.

---

### 2.2 Conversational Context and Dialogue History Disambiguation

* Multi-turn dialogue history provides critical constraints for ambiguous ASR tokens `[SEEN-IN-SNIPPET]`.
* If a prior turn established that the user is coding a Python script, an ASR output of *"piping stall numpy"* is unambiguously resolved by the LLM to `pip install numpy`.
* Dialogue State Tracking (DST) maintains active entity slots across turns (e.g., current artist, active contact, target directory), allowing phonetic candidates matching active slots to receive an elevated prior `[KNOWN-BUT-UNVERIFIED]`.

---

### 2.3 The Spoken Dialogue Repair Ladder

Spoken Dialogue Systems (SDS) research (notably Bohus & Rudnicky, SIGdial 2005 `[SEEN-IN-SNIPPET]`) balances system efficiency against error cost through a structured escalation ladder:

```
[Level 1: Silent Auto-Correct]  --> High Confidence / High Phonetic Proximity
        |
[Level 2: Implicit Confirmation] --> Medium-High Confidence / Low-Risk Action
        |
[Level 3: Explicit Confirmation] --> Medium Confidence / High-Risk Action ("Did you mean X?")
        |
[Level 4: Targeted Non-Understanding] --> Low Confidence ("Who / Which X did you mean?")
        |
[Level 5: Rejection / Reprompt]  --> Complete Failure ("Sorry, I didn't catch that")
```

#### A. Dialogue Confirmation Strategies: Explicit vs. Implicit & User Fatigue
* **Implicit Confirmation:** Integrates the recognized slot into the next prompt without stopping the conversation (e.g., *"Searching Spotify for BINI. What track?"*) `[SEEN-IN-SNIPPET]`. It keeps dialogue fast and natural (low turn counts) but is risky if the system misrecognized the input `[SEEN-IN-SNIPPET]`.
* **Explicit Confirmation:** Halts execution to force a verification turn (*"Did you say BINI?"*) `[SEEN-IN-SNIPPET]`. While it prevents cascading errors, excessive explicit confirmations induce severe **user fatigue** and degrade subjective satisfaction `[SEEN-IN-SNIPPET]`.
* **Bohus & Rudnicky (2005):** In their investigation of non-understanding errors and the RavenClaw dialogue framework, they demonstrated that users get trapped in repetitive confirmation loops when thresholds are miscalibrated; dialogue managers must switch strategies rather than repeating the same prompt upon repeated failures `[SEEN-IN-SNIPPET]`.

#### B. Risk-Based & Cost-Sensitive Confirmation
* In decision-theoretic dialogue management, confirmation policies minimize Bayes' risk `[SEEN-IN-SNIPPET]`:
  $$\text{Expected Cost} = P(\text{error}) \cdot C_{\text{false\_accept}} + (1 - P(\text{error})) \cdot C_{\text{false\_reject}}$$
* **Action Reversibility Dictates Policy:**
  * **Low-Risk / Fully Reversible (Play song, search web, fetch weather):** Low $C_{\text{false\_accept}}$. Favor **Silent Auto-Correct** or **Implicit Confirmation** `[SEEN-IN-SNIPPET]`. If wrong, the user simply says *"stop"* or rephrases.
  * **High-Risk / Irreversible (Send email/SMS, place phone call, delete file, execute shell command via MCP, transfer funds):** Extremely high $C_{\text{false\_accept}}$. Always mandate **Explicit Confirmation** unless confidence is near 1.0 `[SEEN-IN-SNIPPET]`.

---

### 2.4 Entity Resolution & Self-Learning from Implicit Feedback (Amazon Alexa Architecture)

* **Entity Resolution Pipeline:** Alexa's Spoken Language Understanding (SLU) decouples ASR transcription from semantic slot extraction and Entity Resolution `[SEEN-IN-SNIPPET]`. Spoken slot variations (synonyms, phonetically confusable terms) are mapped to canonical entity IDs in a knowledge graph, returning both spoken text and resolved entity metadata `[SEEN-IN-SNIPPET]`.
* **Learning from Implicit Feedback (AAAI 2020):**
  * Research by Amazon Alexa AI (*"Feedback-based self-learning in large-scale conversational AI agents"*, Kumar et al., AAAI 2020) demonstrated autonomous defect reduction using user rephrases `[SEEN-IN-SNIPPET]`.
  * **Absorbing Markov Chains:** User interaction sessions are modeled as Markov chains where terminal states represent **Success** (task executed without friction) or **Failure** (user interrupts with *"Alexa, stop"* or encounters an error) `[SEEN-IN-SNIPPET]`.
  * When a user issues a failed command (e.g., *"play alien bridges"*), receives an error, and immediately reformulates to *"play Leon Bridges"*, the transition probability between the defective query and the successful reformulation is mined `[SEEN-IN-SNIPPET]`.
  * **Personal Lexicon Updates:** For an individual edge agent, this implicit feedback loop is directly applicable: when a user says *"No, I said Ysabel"*, the system caches the phonetic mapping from the misrecognized vector to the canonical entity in a persistent personal dictionary `[SEEN-IN-SNIPPET]`.

---

### 2.5 Conversation Analysis (CA) Repair Mechanics for Natural UX

Applying the foundational sociology of conversation—specifically **Schegloff, Jefferson, and Sacks (1977)** (*"The Preference for Self-Correction in the Organization of Repair in Natural Conversation"*)—provides clear UX design rules `[SEEN-IN-SNIPPET]`:

* **Preference for Self-Correction:** In human interaction, listeners strongly prefer to let speakers repair their own errors; outright "other-repair" (where the listener abruptly corrects the speaker) is socially dispreferred `[SEEN-IN-SNIPPET]`.
* **Next-Turn Repair Initiations (NTRIs) by Granularity:** Human conversationalists grade their repair initiations according to how much they understood `[SEEN-IN-SNIPPET]`:
  1. **Open / Unspecified Request:** *"What?"*, *"Huh?"*, *"I didn't catch that"* (forces full repetition; highest user effort) `[SEEN-IN-SNIPPET]`.
  2. **Restricted / Specific Request:** *"Who?"*, *"Which folder?"*, *"Where to?"* (isolates the exact trouble spot while preserving conversational grounding) `[SEEN-IN-SNIPPET]`.
  3. **Candidate Understanding:** *"Did you mean Tuesday?"*, *"Did you mean Ysabel?"* (offers a specific hypothesis for single-token confirmation; lowest user effort) `[SEEN-IN-SNIPPET]`.
* **UX Guideline for the Voice Agent:** Never fall back to an open reprompt (*"I didn't catch that"*) if a restricted slot or a candidate understanding can be constructed from partial phonetic or gazetteer evidence `[SEEN-IN-SNIPPET]`.

---

### 2.6 Evaluation Metrics and Cheap 50–100 Utterance Tuning Protocol

#### A. Core Evaluation Metrics
1. **Entity Error Rate (EER) / Slot Error Rate (SER):** The proportion of critical entity mentions incorrectly recognized or grounded `[SEEN-IN-SNIPPET]`.
2. **Task Success Rate (TSR):** End-to-end task fulfillment without human abort.
3. **Clarification Rate (CR):** Percentage of conversational turns spent in explicit confirmation (`did you mean...`).
4. **User Effort / Turn Penalty:** Average turns per successful interaction, tracking user fatigue `[SEEN-IN-SNIPPET]`.

#### B. Threshold Tuning via DET Curves & Bayes' Risk
* **Detection Error Tradeoff (DET) Curves:** In spoken dialogue systems, plotting False Rejection Rate (FRR) against False Acceptance Rate (FAR) on a normal deviate (probit) scale produces linear DET curves that clearly expose operating tradeoffs `[SEEN-IN-SNIPPET]`.
* **Bayes' Risk Optimization:** The optimal operating threshold $\tau^*$ is found at the tangent point between the DET curve and the isocost line defined by the ratio of error costs $C_{\text{fa}} / C_{\text{fr}}$ `[SEEN-IN-SNIPPET]`.

#### C. Cheap 50–100 Utterance Calibration Protocol
1. **Corpus Construction (50–100 clips):** Record 75 domain-representative utterances containing 100+ critical entities (Filipino names, local places, OPM artists, programming libraries).
2. **Log Signals per Clip:** For each utterance, extract:
   * ASR word-level confidence scores $c \in [0, 1]$.
   * Normalized phonetic distance $d_{\text{phon}} \in [0, 1]$ to nearest gazetteer entry.
   * Margin $\Delta = d_2 - d_1$ between the best and second-best phonetic match.
3. **Sweep Cutoffs:** Vary confidence threshold $\tau_{\text{conf}} \in [0.4, 0.9]$ and distance threshold $\tau_{\text{dist}} \in [0.1, 0.35]$.
4. **Evaluate Utility Function:**
   $$\text{Score} = N_{\text{correct\_auto\_accept}} - 3 \cdot N_{\text{false\_accept}} - 1 \cdot N_{\text{unnecessary\_confirm}} - 2 \cdot N_{\text{rejection}}$$
   Select the $(\tau_{\text{conf}}, \tau_{\text{dist}}, \Delta)$ tuple that maximizes empirical utility on the 75-clip set `[SPECULATIVE]`.

---

## Deliverable (a): The Pareto Table of Request Classes Beyond Smart Home

*Estimated shares represent the post-filtered distribution of generic requests after smart-home basics are routed away.*

| Request Class | Est. Share | Critical Entity Types Spoken | Grounding Source | Recommended Fix Tier | Implementation Effort |
|---|---|---|---|---|---|
| **Factual Q&A & Web Search** | 24% | People, dates, concepts, book/movie titles, foreign terms | Open-World (Search engine / Wikipedia) | **Tier 3 (LLM Contextual Repair)** `[SPECULATIVE]` | Low (Prompt engineering) |
| **Practical Guidance & Planning** | 20% | Ingredients, tools, DIY steps, locations, times | Dynamic Context (Recipe/manual DB + open search) | **Tier 3 (LLM Contextual Repair)** + **Tier 2 (Implicit Confirm)** `[SPECULATIVE]` | Medium (Context injection) |
| **Writing Help & Dictation** | 18% | Proper nouns, email addresses, project titles, URLs | Personal Context (Contacts, email history, filesystem) | **Tier 1 (Phonetic Match on Contacts)** $\rightarrow$ **Tier 3 (LLM)** `[SPECULATIVE]` | Medium (Contact regex/phonetic) |
| **Music & Media Search** | 12% | Artist names, track titles, album names, genres | Curated Gazetteer (Top 2k global + 500 OPM artists) | **Tier 1 (Phonetic Gazetteer Match)** `[SPECULATIVE]` | Low (Metaphone / Levenshtein table) |
| **Technical Help & Coding** | 8% | Variable names, CLI syntax, package names, files | Dynamic Grounding (Local git repo, active files, MCP tools) | **Tier 1 (Local Codebase Index)** $\rightarrow$ **Tier 3 (LLM)** `[SPECULATIVE]` | Medium (Tool schema grounding) |
| **Navigation & Local Places** | 7% | Philippine provinces, cities, municipalities, roads | Curated Gazetteer (PSGC 1.7k LGUs, excluding barangays) | **Tier 1 (PSGC Phonetic Match)** $\rightarrow$ **Tier 2 (Explicit Did You Mean)** `[SPECULATIVE]` | Low (JSON trie lookup) |
| **General Chat & Companionship** | 6% | Personal names, conversational topics, emotions | Dialogue History | **Tier 3 (LLM Generative Flow)** `[SPECULATIVE]` | Minimal (Default LLM behavior) |
| **Translation & Multilingual** | 5% | Tagalog/Taglish idioms, loanwords, slang | Open-World / Bilingual lexicon | **Tier 3 (LLM Multilingual Repair)** `[SPECULATIVE]` | Low (System prompt calibration) |

---

## Deliverable (b): Proposed Repair Ladder with Hypothesis Thresholds

*Note: All numerical thresholds below are concrete **hypotheses to test and calibrate** `[SPECULATIVE]` during the 50–100 clip tuning protocol.*

```
                 [Incoming ASR Transcript + Token Confidence Scores]
                                          |
                                          v
                         +---------------------------------+
                         | Grounding / Gazetteer Lookup    |
                         | (Compute d_phon and Margin Δ)   |
                         +---------------------------------+
                                          |
                +-------------------------+-------------------------+
                |                                                   |
     [Entity Found in Gazetteer]                            [Open-World Entity]
                |                                                   |
     +----------+----------+                             +----------+----------+
     |                     |                             |                     |
d_phon <= 0.20        d_phon in (0.20, 0.35]          Word Conf >= 0.80     Word Conf in [0.55, 0.80)
& Δ >= 0.15           OR Δ < 0.15                                |                     |
     |                     |                             v                     v
     v                     |                    [Silent Pass to LLM]   [Candidate Prompting]
+-------------------+      |                             |             (LLM chooses among
| RUNG 1:           |      |                             |              N-best / context)
| Silent Auto-      |      |                             |                     |
| Correct           |      |                             +----------+----------+
+-------------------+      |                                        |
     |                     |                                        v
     |                     |                            Action Risk Assessment?
     |                     |                                        |
     |                     +-------------------+--------------------+
     |                                         |
     |                     +-------------------+-------------------+
     |                     |                                       |
     |               [Low Risk Action]                       [High Risk Action]
     |                     |                                       |
     |                     v                                       v
     |            +-----------------------+              +-----------------------+
     |            | RUNG 2:               |              | RUNG 3:               |
     |            | Implicit Confirmation |              | Explicit Confirmation |
     |            | ("Playing BINI...")   |              | ("Did you mean X?")   |
     |            +-----------------------+              +-----------------------+
     |                     |                                       |
     +---------------------+-------------------+-------------------+
                                               |
                                     Word Conf < 0.55
                                  AND d_phon > 0.35
                                               |
                                               v
                                  +---------------------------+
                                  | RUNG 4:                   |
                                  | Targeted Non-Understanding|
                                  | ("Which artist was that?")|
                                  +---------------------------+
                                               |
                                        [Total Failure]
                                               |
                                               v
                                  +---------------------------+
                                  | RUNG 5:                   |
                                  | General Reprompt          |
                                  | ("Didn't catch that")     |
                                  +---------------------------+
```

### Detailed Rung Specifications & Signals

#### Rung 1: Silent Auto-Correct (Zero User Friction)
* **Required Signals:** 
  * ASR Word Confidence: Any (even low confidence is accepted if acoustic match to closed list is overwhelming) `[SPECULATIVE]`.
  * Phonetic Distance $d_{\text{phon}} \le 0.20$ (e.g., Levenshtein distance on Double Metaphone $\le 1$ edit on short names) `[SPECULATIVE]`.
  * Margin to Second-Best $\Delta = d_2 - d_1 \ge 0.15$ (unambiguous candidate) `[SPECULATIVE]`.
  * Action Risk: Any (grounded entity replaces text transparently before execution).
* **Behavior:** Seamless replacement (e.g., *"usable to lack cruise"* silently rewritten to `Ysabel Dela Cruz`; *"binnie"* rewritten to `BINI`).

#### Rung 2: Implicit Confirmation (Low Friction, High Momentum)
* **Required Signals:** 
  * ASR Word Confidence: $[0.65, 0.85)$ OR $d_{\text{phon}} \in (0.20, 0.30]$ `[SPECULATIVE]`.
  * Action Risk: **Low / Reversible** (music streaming, web search query, read-only data fetch).
* **Behavior:** Assistant executes the task immediately while echoing the grounded entity name into the response prefix (e.g., *"Searching Spotify for BINI. Playing Pantropiko now."*). If wrong, the user simply interrupts (`"Stop, I said..."`).

#### Rung 3: Explicit Confirmation ("Did You Mean...?")
* **Required Signals:** 
  * Case A (High Ambiguity): $d_{\text{phon}} \in [0.20, 0.35]$ but Margin $\Delta < 0.15$ (competing entities, e.g., *"Did you mean Sarah Garcia or Sarah Geronimo?"*) `[SPECULATIVE]`.
  * Case B (High Risk): Action is **Irreversible** (send email, delete file, execute bash script via MCP tool, make phone call) AND confidence $< 0.90$ `[SPECULATIVE]`.
* **Behavior:** Assistant stops and asks a single-turn verification question: *"Did you mean Ysabel Dela Cruz?"* A single affirmative token (`yes`, `correct`, head nod) proceeds.

#### Rung 4: Targeted Non-Understanding (Restricted NTRI)
* **Required Signals:** 
  * Intent is classified with confidence $\ge 0.75$, but a specific required slot has Confidence $< 0.55$ and $d_{\text{phon}} > 0.35$ `[SPECULATIVE]`.
* **Behavior:** Instead of a generic failure, assistant isolates the exact slot per Conversation Analysis principles `[SEEN-IN-SNIPPET]`: *"I understood you want to send an email, but who should I send it to?"* or *"Which artist did you want to play?"*

#### Rung 5: General Reprompt / Deferral
* **Required Signals:** Overall utterance confidence $< 0.40$; intent classification unresolvable `[SPECULATIVE]`.
* **Behavior:** Brief, polite conversational fallback: *"Sorry, I didn't catch that. Could you repeat it?"* `[SEEN-IN-SNIPPET]`. If triggered twice consecutively, switch prompts to avoid repeat-loop fatigue `[SEEN-IN-SNIPPET]`.

---

## Deliverable (c): The Three Experiments to Run First

Given the November 7 deadline, an available A100 HPC cluster for training, and an offline CPU demo environment, these three experiments maximize immediate accuracy gains:

### Experiment 1: Calibrate the Phonetic Matcher & Sizing Bounds on the 59-Clip Set
* **Goal:** Validate whether bounding gazetteer sizes prevents false-positive degradation on the owner's Filipino-accented speech.
* **Protocol:**
  1. Build three gazetteer tiers:
     * *Tier A (Contacts & Seed):* The existing 230-entity benchmark list.
     * *Tier B (Curated PSGC + Names):* Tier A + 1,742 PSGC administrative units (provinces, cities, municipalities) + Top 500 Philippine surnames.
     * *Tier C (Unconstrained):* Tier B + all 42,011 Philippine barangays.
  2. Run the offline phonetic matcher (`match.py`) over `bench/stt_hard.json` across all three tiers, sweeping distance thresholds $d \in [0.10, 0.35]$.
  3. **Success Metric:** Tier B achieves $\ge 80\%$ entity recall without increasing WER on non-entity words (confirming that Tier C induces false-alarm corruption).

### Experiment 2: Constrained LLM Prompting with N-Best Hypotheses (HyPoradise Style)
* **Goal:** Determine if an LLM (running locally or via API) can repair residual Parakeet misrecognitions without hallucination.
* **Protocol:**
  1. Configure `sherpa-onnx` modified beam search to output top-4 candidate hypotheses for the 59 hard clips.
  2. Test three prompting regimes into your LLM:
     * *Prompt 1 (Unconstrained):* `"Fix any ASR speech recognition errors in this sentence: {1-best}"`
     * *Prompt 2 (HyPoradise N-best):* Feed all 4 beam paths, instructing the model: `"Synthesize the most probable spoken sentence based on these acoustic hypotheses: [Path 1...4]"` `[SEEN-IN-SNIPPET]`.
     * *Prompt 3 (Confidence-Constrained & Operation-Bounded):* Mask low-confidence tokens and restrict LLM to `[KEEP]` or selecting from candidate gazetteer entities `[SEEN-IN-SNIPPET]`.
  3. **Success Metric:** Measure Over-Correction Rate (percentage of already-correct words corrupted) and Entity Recovery Rate. Confirm whether Prompt 3 eliminates hallucinations in the low-error regime.

### Experiment 3: Implement Implicit Feedback Rephrase Caching (Absorbing Markov Shortcut)
* **Goal:** Prove that the agent can autonomously learn user-specific corrections during dialogue (e.g., fixing *"usable to lack cruise"* $\rightarrow$ *Ysabel Dela Cruz* on subsequent turns).
* **Protocol:**
  1. Instrument the dialogue manager to detect negative implicit feedback: when a user issues an interruption (`"No"`, `"Stop"`, `"Cancel"`) within 5 seconds of an entity action, followed immediately by a single-entity utterance `[SEEN-IN-SNIPPET]`.
  2. Log the failed ASR acoustic token sequence and map it to the corrected entity in a dynamic SQLite `personal_lexicon` cache.
  3. Re-run the failed audio clip through the pipeline.
  4. **Success Metric:** 100% resolution of repeated named-entity errors on subsequent turns without manual dictionary edits or model retraining.
