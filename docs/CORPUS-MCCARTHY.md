# The Cormac McCarthy corpus

`corpus/cormac-mccarthy/` is a 33-file, 25,600-word reference corpus on the life and works of the novelist Cormac McCarthy (1933-2023), the backbone for a larger doc-chat test than `corpus/harry-potter/` (9 files, 2,300 words). It gives 562 retrieval chunks of 500 characters against 39.

```bash
uv run --group laptop python -W ignore -m doc_chat --mic --docs corpus/cormac-mccarthy ...   # see docs/PI-REMOTE-SPEAKER.md
```

## How it was made

1. Four search-only research prompts (`.scratch/speech-hosting/prompt-mccarthy-{A,B,C,D}.txt`: life, early Southern novels and plays, Western works, late works and reference files) were run through `agy -p` in parallel, with no shell, no file reads and without `--dangerously-skip-permissions`. The prompts ask for plain prose in the agy assistant's own words (no passages from the books), a paragraph that names its subject in full (each is retrieved on its own), dates and numbers only where the search results support them, and sources and unverified claims kept outside the files.
2. `scripts/build_corpus.py` split the reports into files and set aside the source lists and unverified claims in `.scratch/speech-hosting/cormac-mccarthy-{SOURCES,UNVERIFIED}.md` (every `.md` and `.txt` in a corpus folder is indexed, so sources must not be in it). Report C repeated blocks 16-21: the second, longer copy of each was kept.
3. The facts were spot-checked by hand against what the reviewer knew (see below). `scripts/make_entities.py` then produced `config/entities-mccarthy.json` (197 names, with a stoplist for generic words such as "Road", "White", "American") for the speech server's name repair: `python -m lan_speech --entities config/entities-mccarthy.json`.

## What was checked, and what was not

The checks were by one reader (an AI assistant) from memory plus the lists agy gave; **the web pages agy cites were not opened**, and 25,000 words were not read line by line. Read in full and compared: the awards, marriages, timeline, adaptations and FAQ files; the rest was skimmed only through retrieval tests. Errors found and fixed in the files: the first wife's name (Lee Holleman, not "Holmberg"), "ninety-year life" (he died at 89), an invented year for the first divorce and a guessed age, the MacArthur stipend figure ($23,000 a year, which is not right), three prizes that could not be confirmed (Believer Book Award, Maltese Falcon Award, Premio Ignotus), the year of publication of *The Stonemason* (1994, not 1995) and the birth year of his younger son (1998, not 1999). The reviewer also believes he had a daughter, Wendy, from his second marriage, which the files now mention as "some accounts name". The Guggenheim year (1969) and "senior fellow" at the Santa Fe Institute were left as written and are not confirmed.

agy itself flagged four details as unverified: the exact date of the third marriage; his father's years of birth and death; whether his Chicago job was a warehouse or a repair job; and the date of the first staging of *The Stonemason* (the file says sources differ). Treat the corpus as **research-grade for testing a voice agent, not as a citable source**; expect a few more errors.

Retrieval smoke test (BM25 over the chunks, top 2): 10 of 10 questions ("When was Blood Meridian published?", "Who directed The Counselor?", "Where did he die?", "Why no quotation marks?" and six others) returned a chunk that contains the answer. One run, ten questions chosen by the reviewer.

## Using it with a real model (2026-10-23)

`python -m doc_chat --docs corpus/cormac-mccarthy --llm gemini --stream --max-words 90 --chunk-size 1000 --top-k 3 --system-prompt <file>` (the built-in prompt limits answers to two short sentences). Gemini's answers were grounded in the corpus and mostly right; two of twelve open-ended questions got "I do not know" because of retrieval bugs that are now fixed (a short question was searched together with the previous one; title lines outranked paragraphs). See `docs/PI-REMOTE-SPEAKER.md`.
