# Echo, barge-in and self-interruption: a prompt for an outside research agent, and an inventory of what we did

**Written 2026-10-24** from the repository state at `agy/interrupts` (pushed). Two parts:

1. **The prompt** (below, in one block, about 5,700 words): self-contained and zero-shot, written for an agent that cannot read the codebase and knows nothing about the project. No file references, no addresses, nothing secret. It defines every term, describes the product and what "done" means, the hardware, the audio path and its timing, the exact logic of the guard, the echo model, the duck and the canceller (with their parameters), the test method, all the measurements with the tables, the failures with verbatim transcripts, seven hypotheses (labelled untested), what was not tried, the constraints, eleven questions and the answer format. Paste it into any agent that has live web search.
2. **The inventory**: every piece of research done and everything created in this work, with where it lives, so you can see what the other agent is being compared against.

Every number below rests on one speaker, one room and small counts unless it says otherwise; the prompt says so too, on purpose. Counts of transcripts "with echo words in them" were made by eye from the logs; command counts come from the agent server's tool-call log.

---

## 1. The prompt

```text
YOUR TASK
You are a research agent with live web search and no access to my code, my files or my project. Everything you need to know about the project is in this message. I am stuck on an acoustic echo problem in a voice assistant. Find out how this problem is actually solved in practice, with evidence from real sources, and tell me exactly what to try next and in what order. Do not answer from memory when a source exists: search, open the sources, read them and cite them. If something cannot be found, write "not found" instead of guessing. Nothing here has been peer reviewed; some of my own explanations below are hypotheses and are labelled as such.

HOW TO READ THIS MESSAGE
Sections: 1 words I use; 2 the product and what "done" means; 3 the hardware and software; 4 what happens in time during a reply and an interruption; 5 exactly what my current echo and interruption logic does; 6 how I test; 7 all measurements; 8 what I think might be going on (hypotheses); 9 what I have not tried; 10 constraints; 11 questions; 12 how to work and what to output.

====================================================================
1. WORDS I USE (so you can map them to standard terms)
====================================================================
- Reply: what the assistant says. A language model produces text, a text-to-speech (TTS) model turns it into audio sentence by sentence, and the audio is played on a loudspeaker.
- Barge-in: the user talks over the reply to interrupt it ("stop", "wait, turn the lights on instead").
- Turn: one user utterance. A turn ends after 0.6 s of silence; the laptop then asks the speech recogniser (ASR) for the final transcript, which arrives about 0.3 s later.
- Echo: the assistant's own voice, played by the loudspeaker, coming back through the microphone. Far-end/reference signal = the audio that is sent to the loudspeaker. Near-end = the user's voice. Double-talk = both at once. ERLE = echo return loss enhancement.
- dBFS: decibels relative to digital full scale. "Level" below means the RMS of a block of microphone samples scaled to 0..1 (so 0.0316 is -30 dBFS). "Room noise" = the median level of the microphone when nothing is playing and nobody speaks (-44 to -35 dBFS during my sessions; the cause of the variation is unknown).
- Silero VAD: a small neural voice-activity detector. I run it on 32 ms windows (512 samples at 16 kHz); a window is "speech" when its confidence is at least 0.5. It cannot tell the assistant's voice from the user's.
- k-of-n rule: "speech" when at least k of the last n windows (or blocks) were speech, instead of an unbroken run (speech has quiet gaps).
- Played level: the loudness of the audio most recently sent to the loudspeaker (the reference), as an RMS between 0 and 1.
- Coupling: microphone level divided by played level during a reply: how much of the loudspeaker signal arrives at the microphone. It varies a lot within a reply because speech echo is spiky.
- Guard: my logic that decides, 10 times a second, whether the microphone block is "the user talking over the assistant" or "the assistant's own echo".
- Pre-roll: the most recent 1.2 s of microphone audio, kept so the start of an interruption is not lost while the detector makes up its mind.
- Duck: turn the loudspeaker down (here 15 dB) at the first sign of a voice, before the guard has fully decided.
- Cue beep: a measurement trick, section 6.
- Text guard: a check that compares the recognised words with the words the assistant was just saying.

====================================================================
2. THE PRODUCT AND WHAT "DONE" MEANS
====================================================================
A full-duplex, interruptible, English voice assistant for a consumer-style device, a small home assistant. The user speaks normally; the assistant answers aloud; the user must be able to interrupt it at any time by voice, with no wake word and no headset. It also controls a (simulated) home through tools and answers questions from a document set; that part is not the problem here.

I would call the echo problem solved when, with the loudspeaker about 0.5-1.5 m in front of the laptop at 80% volume in an ordinary room:
(a) the assistant never interrupts itself: 0 self-interruptions in at least 30 replies that nobody talks over;
(b) the user's first words survive: at least 90% of scripted interruptions are recognised from their first word ("Stop. Set a timer for ten minutes." must not arrive as "Come on. Set a timer..."), and a bare "Stop." works;
(c) the assistant stops within about 0.5 s of the user's first word (my detector needs at least 224 ms of speech, so that is mostly its floor);
(d) nothing the assistant said ever reaches the language model as if the user had said it, and it never acts on its own speech;
(e) no audible dips in volume on replies nobody talks over;
(f) it works without a dedicated echo-cancelling hardware device if at all possible; if it cannot, I want the evidence for that and the best device to buy;
(g) it runs in real time on an ordinary laptop CPU together with speech recognition and TTS.

====================================================================
3. THE HARDWARE AND SOFTWARE
====================================================================
Rule of the course project: the Raspberry Pi may run no speech model except a small VAD. Everything else may run on a laptop.

Topology (the one I want to ship):
  - A Linux laptop owns the microphone and runs: streaming speech recognition (Parakeet TDT v2 with hotwords), text-to-speech (Supertonic, 44.1 kHz output), the language-model client, the Silero VAD and all the interruption logic below. The laptop microphone is sampled at 16 kHz, mono, 16-bit, delivered in 100 ms blocks. I do not know the exact laptop microphone model.
  - A Raspberry Pi 4 Model B is only the loudspeaker: a small USB speaker (Jieli CD002-AUDIO, a cheap USB sound card with a speaker, opens only at 48 kHz) connected to the laptop over an Ethernet cable (a private link, about 1.5 ms round trip). The Pi runs one small script that plays what the laptop sends. Its USB sound card is addressed directly through ALSA; its own USB microphone (Jieli UACDemoV1.0) is not used because it gave speech only 5-15 dB above its own noise.
  - The loudspeaker sits in front of the laptop (what a consumer would do), at 80% of the Pi's sink volume, in an ordinary room; the distance and angle were not recorded. Earlier tests had the speaker behind the laptop.
  - There is no hardware echo cancellation anywhere. I do not own a USB speakerphone.

Audio path of a reply, in code terms:
  TTS audio (44.1 kHz) -> laptop resamples to 48 kHz (polyphase) -> sent over a WebSocket in 50 ms slices (16-bit mono PCM). The sender paces itself at playing speed and never lets more than 0.25 s of audio get ahead of what the Pi has actually played, so that an "abort" message can silence the Pi quickly. The Pi writes each slice to its USB sound card. On "abort" the Pi drops everything it holds; the device itself still holds about 35 ms.
  The same slice, resampled to 16 kHz, is also handed to my echo canceller as the reference signal the moment it is sent (that is, about 0.25 s before it will be played).
  Microphone path: laptop microphone -> 100 ms blocks -> (if enabled) echo canceller -> the guard, the 1.2 s pre-roll buffer, and the speech recogniser.

Echo canceller (when enabled): the WebRTC audio-processing module through LiveKit's Python SDK (livekit.rtc.AudioProcessingModule) with echo_cancellation=True and noise_suppression, high_pass_filter and auto_gain_control all False. It works on 10 ms frames (160 samples at 16 kHz). For every 10 ms microphone frame I call process_reverse_stream() with the next reference frame (or a frame of silence if none is queued) and then process_stream() with the microphone frame, so the two streams advance in lock step. set_stream_delay_ms(50) is called once. When the reference queue goes from empty to non-empty, 50 ms of extra reference is fed ahead once. (I call this parameter the "lead" L: it sets BOTH the stream-delay hint, L ms, and L ms of extra reference fed ahead once; the shipped value is L = 50.) The reference queue is filled by the player thread, which is up to 0.25 s ahead of the loudspeaker, and consumed at microphone speed; therefore the reference stream leads the echo by roughly 0.25 s + the Pi's device buffer (about 35 ms) + network + acoustic path + microphone capture latency. I have not measured that sum in the live system; I estimate 0.3-0.5 s. I only know the two clocks (Pi USB sound card and laptop microphone) drift, section 7C.

====================================================================
4. WHAT HAPPENS IN TIME
====================================================================
A reply: the user stops speaking -> 0.6 s of silence ends the turn -> ASR final about 0.3 s later -> the language model's first token 1-2 s later (it may call a tool first, a second request) -> TTS first audio about 0.25 s after the first sentence -> the sound comes out of the Pi 0.1-0.3 s later. So the reply starts roughly 2.3-3.3 s after the user's last word. The first reply after the Pi's sound device has been idle for a while arrives about 0.5 s later still (section 7C).

An interruption: while the reply plays, every 100 ms microphone block (after the canceller, if enabled) is fed to the guard together with the played level. When the guard says "user", the laptop (1) tells the Pi to abort, (2) cancels the TTS and the language model stream, (3) takes the last 1.2 s of microphone blocks from the pre-roll buffer as the beginning of a NEW turn and streams them, followed by live audio, to the speech recogniser, which ends the turn after 0.6 s of silence. Its transcript is then processed by text rules (section 5E). The guard needs at least 224 ms of speech (7 Silero windows of 32 ms), so the pre-roll that is handed to the recogniser contains about one second of whatever came BEFORE the user's first word: during a reply that is the assistant's own echo. This is the route by which the assistant's words get into the transcript when nothing cancels the echo.

====================================================================
5. WHAT MY CURRENT LOGIC DOES, EXACTLY
====================================================================
A. The guard (called once per 100 ms microphone block while the assistant is answering). It says "user" only if BOTH stages agree:
  - Silero stage: at least 7 of the last 10 Silero windows (32 ms each, over the last ~0.3 s) were speech at confidence >= 0.5.
  - Level stage: at least 3 of the last 8 blocks had level > bar, where
        bar = max( 4 x room_noise , 0.02 , margin x coupling x played_level )
    margin = 1.2; coupling is the 95th percentile of the last 60 observed ratios (mic level / played level) taken only from blocks that were NOT above the bar and while played level > 0.01 (so the user's voice does not teach it); until 4 samples exist coupling is a prior of 0.8. The model is kept across replies in a session.
  When the assistant is not yet playing (still thinking), any clear speech counts (level > 3 x room noise and Silero).
  History of this design: with a bar built on the median coupling every reply interrupted itself (7 of 7); the 95th percentile with a longer window fixed that. Echo is spiky: coupling median 0.34, 90th percentile 0.62, 99th percentile 0.79, while the user speaking over a reply measured median 0.64 and 90th percentile 1.7 of the played level.
B. The duck (optional): while answering, if the newest block's level > bar AND at least 2 of the last 10 Silero windows are speech AND the guard has not fired, send a gain message of -15 dB; the Pi applies it (a 50 ms linear ramp per slice) to audio it has not played yet, and the laptop scales the reference given to the canceller by the same factor and the played level by it too. If the guard then fires, the reply is aborted as usual. If no voice is seen for 0.6 s after the duck, send gain 0 dB (restore).
C. The echo canceller (optional): section 3. Everything the guard, the pre-roll and the recogniser see has been through it.
D. Text guard: if the recognised text contains a run of at least 3 words that appears, in order, in the words the assistant was saying, and that run is at least 80% of the recognised words, the text is treated as the assistant's own echo and ignored ("[ignored: that was my own voice]").
E. Stop and pivot rules: an utterance made only of stop words ("stop", "wait", "hold on", "stop it", "never mind", with fillers such as "please", "now", "okay") only silences the assistant and it goes back to listening; after an interruption up to eight words ending in stop/wait/pause also count unless a question opener (tell, what, who, when, ...) is present; a leading "wait, stop." in front of a question is stripped ("Wait, stop. Turn on the lights instead." becomes "Turn on the lights instead."). These rules assume the transcript is clean and mostly lack a defence when the assistant's words sit in front of the user's.
F. After each interruption the language model sees the conversation history with the interrupted reply truncated to what was actually spoken.

====================================================================
6. HOW I TEST
====================================================================
Cue-beep sessions: a spoken test where I ask a question ("Tell me about Blood Meridian"), a beep (880 Hz, 0.3 s, mixed into the reply audio so it comes out of the Pi) sounds 2.5 s into every reply, and I say my scripted interruption at the beep. This gives every interruption a known time. A reply that is stopped before the beep was stopped by something other than my voice (a false trigger). A reply that plays to the end after a cue was a miss. The script has 8 numbered questions and 8 interruption lines, always "Stop." followed by a command, or "Stop." alone:
  1 "Tell me about Blood Meridian." -> "Stop."
  2 "Tell me about Suttree." -> "Stop. Turn on the lights."
  3 "Tell me about All the Pretty Horses." -> "Stop. What year was it published?"
  4 "Tell me about No Country for Old Men." -> "Stop. Set a timer for ten minutes."
  5 "Tell me about The Road." -> "Stop."
  6 "Tell me about The Passenger." -> "Stop. Play some music."
  7 "Tell me about his early life." -> "Stop. When did he die?"
  8 "Tell me about Outer Dark." -> "Stop. Pause the music."
Commands go to a home-control tool through a language model, so a mis-heard or echoed word can cause a wrong action, which makes the effects visible.
Every run is one speaker (me), one room, one placement, one session per condition. Counts are small: treat everything as direction, not rates. The recorded sessions include the guard trace (one row per 100 ms block: mic level, bar, played level, Silero confidence, fired) and the audio of every turn.
Measurement helpers I wrote: (1) a probe that plays known band-limited noise bursts (loud, and 12 dB quieter) and a 1 kHz tone through the Pi while the laptop records, and reports delay drift, coherence by band, gain at two levels and tone distortion; (2) an offline script that rebuilds the reference of a recording from the fixed clips, aligns it by phase-transform cross-correlation, runs the WebRTC canceller with a given reference lead, and reports Silero fires and recogniser transcripts before and after.

====================================================================
7. MEASUREMENTS (one speaker, one room, small counts)
====================================================================
A. Detector on the echo (laptop microphone, Pi speaker, user silent, six reply clips): Silero (confidence 0.5 or 0.7, 150 or 250 ms) fired on 6 of 6 clips at every volume from 40% to 90%. The echo above the room noise at the laptop microphone, median / 95th percentile: 90%: 18-26 / 26-32 dB; 80%: 12-19 / 20-26 dB; 60%: 2-8 / 9-14 dB; 40%: -2 to 2 / 4-10 dB. The echo trips Silero even when it is at the noise floor, because the laptop microphone is much more sensitive than the Pi's USB microphone. With the guard of section 5A at 80%, held out: 6 of 6 interruptions detected and 0 of 6 echo-only clips fired; in a live loop 0 of 14 quiet replies were interrupted by echo and 7 of 7 cued interruptions were stopped (beep to stop median 0.64 s and 0.76 s in two runs).
B. Earlier experiment on the Pi with its OWN microphone and speaker (two separate USB devices; the user spoke 5-10 cm from the microphone), with and without PipeWire's WebRTC echo-cancel module (run as a separate process, no root): without cancellation Silero fired on the assistant's voice on 6 of 6 clips at 90% volume (twice) but not at 60-75%; with cancellation the trips vanished at 75% and 90% (5 runs of 6 clips, 20 double-talk trials) but at 60% they did not (4, 4, 3 and 1 of 6 clips in four runs; unexplained). With cancellation the user was detected about 1 s later (median 2.0-2.3 s after the beep against 1.0-1.3 s without; includes the user's reaction time). I suspected two USB clocks feeding one canceller. The Pi's microphone gave speech only 5-15 dB above its own noise.
C. The path from the laptop through the Pi speaker back to the laptop microphone (the probe, 80%, room -46.5 dBFS):
  - Echo level 20.0 dB above the room for the loud burst (a reply's loudness), 8.9 dB for the burst 12 dB quieter.
  - The delay is not constant: it ramps by 2.2 samples per second at 16 kHz (137 ppm; 8.2 ms per minute), the clock difference between the Pi's USB sound card and the laptop microphone. Followed in 0.5 s blocks it was smooth.
  - Linear coherence between the reference and the microphone for the loud burst: against a fixed-delay reference 0.27 at 300-600 Hz and at most 0.02 above 600 Hz (a linear canceller could remove about 0.5 dB); against a drift-corrected reference 0.76 (300-600 Hz), 0.79 (600-1000), 0.71 (1-2 kHz), 0.51 (2-4 kHz).
  - Mild nonlinearity: gain 2.2 dB lower for the loud burst than for the quiet one; at 1 kHz the 2nd harmonic is 36.5 dB below the fundamental and the 3rd and 4th over 55 dB below.
  - The WebRTC canceller (section 3, lock step, reference fed ahead) removed 29.7 dB of the loud burst and 14.2 dB of the quiet burst after 4 s (these include the effect on the room floor and overstate echo-only removal).
  - Separate observation: in recordings of six clips played 1.5 s apart, the first clip's sound reached the microphone about 0.5 s after it was sent while later clips took 0.1-0.3 s: the USB sound card seems to need about 0.5 s to wake after idle. I have not measured whether each reply in a real conversation (seconds apart) pays this.
D. Offline, 12 real clips at 80% (6 echo-only, 6 with me saying "Wait, stop, I have a question." from a beep 2.5 s into the clip), the canceller run offline: the reference was rebuilt from the known clips, aligned to the echo's arrival by cross-correlation, stretched by the measured 137 ppm so it does not drift against the microphone (the live system has no such correction, the canceller must track the drift by itself), then fed 300 ms ahead of its echo with set_stream_delay_ms(300), i.e. a consistent 300 ms render-to-echo delay and a matching hint: Silero (0.5, 7 of 10) fired on 6 of 6 echo-only clips raw and 1 of 6 after (0.4 s into the clip, while converging). What the recogniser heard from the echo-only clips: raw, the whole echoed reply (about 15 words) in 6 of 6; after cancelling: "" , "We're getting", "Where did the crossing of the corner?", "For guide feet.", "Look at the routine.", "A lot of funday". My words after the beep: recognised correctly 5 of 6 times after cancelling against 4 of 6 raw ("Wait, Stark, I have a question." raw once); Silero then fired on me 0.6-0.9 s after the beep instead of on the echo.
E. Sweep on that recording, with the live code path (lock step, the reference queue fed 0.25 s ahead of the microphone position): lead L (0, 50, 100, 200, 300 ms; remember L is both the delay hint and L ms of extra reference fed ahead once, so the real render-to-echo delay in a cell is path + L while the hint is only L) against a simulated path delay between reference and echo (0, 50, 100, 200, 300 ms); each cell is "echo-only clips where Silero still fires, of 6":
              path 0   50   100  200  300 ms
     lead 0       2    1    0    0    2
     lead 50      1    0    1    0    1
     lead 100     0    1    1    1    1
     lead 200     2    4    1    6    5
     lead 300     3    5    4    6    5
  Median echo reduction in those cells was 7-9 dB for leads up to 100 ms and 2-8 dB for 200-300 ms; talk-over clips where Silero fired on me after the beep: 6 of 6 for leads up to 100 ms, 3-6 of 6 above. So a lead of 0-100 ms held for every simulated path delay (so a hint of 0-100 ms against a real delay of up to 400 ms was fine), while a lead of 200 ms or more got worse as the delay grew. I do not understand why: a cell with total delay 400 ms and hint 100 (L = 100, path 300) fires on 1 of 6 clips, but a cell with the same total 400 ms and hint 200 (L = 200, path 200) fires on 6 of 6. The real live delay is probably 0.3-0.5 s (section 3), more after idle, which is inside the range that looked fine offline, yet live the canceller alone still let residual bursts through (7F).
F. Live A/B in cue-beep sessions, speaker in front of the laptop (except the first column, speaker behind), 80% volume, the 8-line script of section 6, one session per condition. "Early" means the reply was cut off before the cue with nobody speaking.
                                  none (speaker  canceller   duck only   canceller
                                  behind)        only                    + duck
  replies cut off early           1 of 17        7 of 20     0 of 14     0 of 15
  interruption transcripts that   about 6 of 8   about 4     4 of 8      0 of 7
   had the assistant's own words                 of 8
   in them (counted by eye)
  spurious "stop the music"       3              1           2 (each     0
   commands (from the tool log)                              with a
                                                             play-music)
  the assistant acted on its      1 (a second    1 (looked   0           0
   own voice                      timer)         up a
                                                 contact)
  junk turns (its own words       about 4        6           3 (2 caught 1 (the word
   heard back and handled)                                   by the text "causal")
                                                             guard)
  interruption openings garbled   0 of 6         2 of 6      1 of 6      2 of 6
   or lost                                                   (+1 lost)
  scripted interruptions that     about 6 of 8   3-4 of 8    about 6     5 of 8
   ended in the right action                                 of 8
   (my reading of the logs)
  beep to stop, median            0.41 s (n=12)  0.36 s (4)  0.39 s (12) 1.34 s (9)
  false ducks on quiet replies    -              -           not counted 3 of 7
  Microphone level during replies fell from -29.3 dBFS (none, speaker behind) to -43.7 dBFS (canceller only; room floor -44), and the median echo/played ratio from 0.33 to 0.07, but with the canceller alone 7 of 20 replies were still cut off in their first 0.4-1.2 s with nobody talking: the guard fired on residual bursts (level well above the bar, Silero confidence 0.7-0.9). With the duck those early fires did not happen, with or without the canceller. The "spurious stop" commands appear when the word "Stop" in my line is not the first word of the transcript (echo words are in front of it), so my rule that strips a leading "Stop" does not remove it and the language model reads it as a command. The beep-to-stop time includes my reaction time, which varied, so the 1.34 s is not necessarily a slower detector.
  Examples of what the recogniser heard from my interruptions. Without cancelling: "Five novels at Bobby." (my "Stop." lost), "First book in the Border Trilogy. Stop! What year was it published?", "2005 border. Stop. Set a timer for ten minutes." (it set the timer, ran a spurious stop-music command, then heard its own confirmation and set a second timer), "Novel published in 2007." (pure echo, answered as a question), "Joseph McCartney Chief Stop, when did he die?", "Second, novel stop pause the music." With the canceller: "Stop!" (clean), "Turn on the lights.", "Set a timer for ten minutes." (clean, no spurious stop), "Pause the music." (clean); but also "Come on. Want you what is published?" for "Stop. What year was it published?", "Please come to sit." for "Stop. Play some music.", nothing at all for one bare "Stop." (the model happened to take 6 s to start, so the reply played on), and a quiet reply stopped by the word "causal" heard from the assistant's own "...natural causes...".
G. Speech recognition in general: about 0.3 s from the end of the turn to the final transcript; the first one or two words of a question are often misrecognised even without echo (4 of about 23 live utterances), so a lost opening word is not always the canceller's fault; I have no baseline for how often it happens without echo in this setup.

====================================================================
8. WHAT I THINK MIGHT BE GOING ON (hypotheses; none tested; tell me which are plausible and how to test them)
====================================================================
H1. The real delay from reference to echo in the live system (probably 0.3-0.5 s, and about 0.5 s more for a reply that starts after the Pi's sound card has gone idle) or its variation from reply to reply is outside what the WebRTC delay estimator tracks well, and the 50 ms hint is far from it. Offline the same mismatch looked harmless up to a total of about 400 ms (7E), but the offline reference had a fixed alignment and the idle wake-up delay was not simulated.
H2. At the start of double-talk the canceller's near-end suppression takes the first words of the user (its filter has converged on echo only), which is when my guard is still deciding and the pre-roll is being cut. The audible result is a garbled opening.
H3. The duck changes the loudspeaker gain by 15 dB in one step. By my reading of my own code, the canceller's reference is scaled at the moment each reference frame is rendered to it, but the Pi applies the new gain to audio that was already sent (up to 0.25 s of it); so for roughly the next 0.25 s the reference the canceller has already been given is 15 dB louder than the echo it corresponds to. A step like that may disturb the adaptive filter and cause the residual bursts and the garbled openings.
H4. The 137 ppm clock drift is tracked slowly by the canceller, so the first seconds of every reply, which is when the guard is most likely to fire on a leak, are the worst.
H5. The speaker's mild nonlinearity at 80% and room resonances leave residual components a linear filter cannot remove; the residue is loud enough and speech-like enough (Silero confidence 0.7-0.9) to fire my guard in the first second of a reply.
H6. The 1.2 s pre-roll is the main route by which echo enters the transcript (section 4); with the canceller the pre-roll is clean, without it each barge-in transcript starts with up to a second of echo.
H7. Speech-like residue or "musical noise" from the canceller's suppressor fools Silero.

====================================================================
9. WHAT I HAVE NOT TRIED
====================================================================
A different canceller (SpeexDSP, a neural canceller such as DeepVQE or DTLN-aec, PipeWire's own module with tuned options); WebRTC AEC3 configuration beyond the defaults and the stream-delay hint (the Python binding I use exposes only four booleans); running the canceller only for the guard while the recogniser gets the raw audio with the pre-roll trimmed; measuring the live render-to-echo delay and its variation between replies; keeping the Pi's sound card awake; a gradual duck; target-speaker or personalised VAD; a USB speakerphone with on-board cancellation; 90% volume with cancellation; a second speaker, room or placement; larger numbers of trials.

====================================================================
10. CONSTRAINTS
====================================================================
- No speech model on the Pi except a VAD (course rule); the Pi has no sudo; the Pi is Debian-like.
- Everything else runs on a Linux laptop in Python; real-time CPU budget shared with the speech recogniser, TTS and a small ONNX VAD; GPU use is not assumed.
- Prefer open-source, maintained, packaged components (pip, apt, or a PipeWire/PulseAudio module). I can change the Pi script and the laptop code freely.
- I cannot change the speaker hardware for the demo unless the evidence says it is necessary; a recommendation to buy a specific device is welcome if it comes with evidence.
- No wake word; the assistant keeps listening while it talks.

====================================================================
11. QUESTIONS (answer each with evidence)
====================================================================
Q1. Delay and drift across two devices. When playback is on a different machine or sound card from the capture (render and capture clocks drifting by 100+ ppm, and a reference-to-echo delay of 0.3-1 s), how do production systems keep a software canceller converged? What does WebRTC AEC3 do about delay (the delay estimator's range and behaviour, "clock drift" handling, what exactly set_stream_delay_ms does and whether a hint that is far from the real delay hurts), what does PipeWire's module-echo-cancel / its WebRTC method do, what does SpeexDSP do, and what do LiveKit, Pipecat and similar frameworks document for playback on another device? Is it normal for the reference to lead the echo by 0.3-0.5 s, or must the reference be delayed to match before it is given to the canceller (and how do the libraries expect that to be done)?
Q2. Double-talk damage. Why does a WebRTC-style canceller damage the first words of a barge-in, and what are the known remedies (double-talk detection, freezing or slowing adaptation, the suppressor's aggressiveness, running the recogniser and the VAD on different taps, a short un-cancelled pre-roll, residual echo suppression, nonlinear processing)? Which of these knobs are exposed by the LiveKit Python APM, by other Python or C bindings of WebRTC audio processing (name them, with versions and whether they are maintained), by SpeexDSP, by PipeWire's module?
Q3. Other products and frameworks. How do Pipecat, LiveKit Agents, Vapi, Retell, OpenAI Realtime, Gemini Live, Alexa, Google Nest and Apple devices prevent self-interruption when playback is acoustic: minimum speech duration, adaptive interruption models, what is committed to the conversation history after an interruption, how the recogniser is fed after an interruption? Is "duck 15 dB at the first sign, stop on confirmation, restore if not confirmed" documented anywhere with thresholds and results?
Q4. Ducking and the canceller. Is it known to hurt a canceller when the loudspeaker gain changes by 15 dB in a step? How do products apply ducking relative to the canceller's reference (scale the reference, duck after the canceller, ramp slowly)? What ramp times are used?
Q5. The recogniser after a barge-in. How should the audio given to the recogniser after an interruption be assembled so the assistant's echo is not in it but the user's first word is: pre-roll length, retroactive cancellation of the pre-roll, trimming to the detected onset minus N ms, a second pass? What do published systems do?
Q6. Neural and residual echo suppression usable on a laptop in real time with a reference signal (DeepVQE, DTLN-aec, NKF, FRCRN-style models, AEC Challenge winners): open code and weights, reported ERLE and double-talk quality, size and speed, ONNX or Python packaging, any use in voice agents.
Q7. Echo-aware VAD. Personalised or target-speaker VAD, device-directed speech detection, using the reference as a feature: is any of it usable without a model on the Pi, and is any open-source?
Q8. Hardware. USB speakerphones or speaker-and-microphone modules with on-board echo cancellation suitable for a Raspberry Pi project (for example Seeed ReSpeaker/XMOS XVF3800, Poly Sync, Jabra Speak, Anker PowerConf, eMeet): price, whether cancellation still works when audio is played through the device as a USB sound card, independently measured or published ERLE or echo return loss, Linux support, any report of barge-in at 80% volume.
Q9. The 0.5 s wake-up of a USB sound card. Standard ways to avoid the delay at the start of a reply on ALSA or PipeWire (keep the sink awake with inaudible audio, disable USB autosuspend, disable the sink's idle suspend), and what they cost.
Q10. Measuring. Which standard measures and public tools (ERLE, double-talk near-end quality, AECMOS, PESQ or DNSMOS on the near-end speech, word error rate of the recogniser on cancelled audio) should I use, and what extra recordings should I make in my setup (for example a simultaneous reference tap, a swept-sine impulse response, a double-talk set with known near-end speech) so that the next experiment separates the hypotheses H1-H7?
Q11. Verdict and plan. Given everything above, which changes are most likely to reach the criteria of section 2 with the speaker in front of the laptop at 80% volume, and in what order should I try them? For each experiment give: the exact change, what to measure, the number that counts as success, and what result would rule it out. Say plainly if the evidence suggests that this arrangement needs echo-cancelling hardware to work reliably.

====================================================================
12. HOW TO WORK AND WHAT TO OUTPUT
====================================================================
- Prefer primary sources: the WebRTC audio_processing source and its documentation, PipeWire module documentation, SpeexDSP documentation, framework documentation and their issue trackers, the AEC Challenge papers and results, vendor datasheets that contain numbers, and independent measurements. Prefer sources from the last two years; give the date of every source; if sources disagree, say so.
- Mark every statement [source: URL, date] or [inference]. Do not present my measurements back to me as findings. Do not invent numbers, product names, parameter names, library names or citations.
- If a question can be answered by a short experiment on my side rather than by reading, say so and describe it.
OUTPUT, in this order:
1. A summary of at most 12 lines: the most likely cause of each failure in sections 7-8 and the single best next step.
2. For each hypothesis H1-H7: plausible / implausible / cannot tell, with the evidence, and the cheapest test.
3. Answers to Q1-Q11 with evidence and sources.
4. The ranked experiment plan (Q11) as a table.
5. A list of every parameter name, library call, setting or package you found that I should look at, each with its source.
6. A list of claims you could not verify, and of anything in my description that looks wrong or inconsistent.
```

---

## 2. Research done

### 2.1 Outside research (search-only agy reports, 2026-10-16 to 2026-10-23; the assistant's own checks noted)

`agy` is the Antigravity CLI run in plan mode (web search only, no shell, no file reads, no `--dangerously-skip-permissions`). The reports are web-search summaries: the claims are tagged by the model and partly unreliable. They live in `.scratch/speech-hosting/`.

| Report | Subject | How far to trust it |
|---|---|---|
| `AGY-ECHO.md` | how commercial devices and voice frameworks handle echo and barge-in; the source of `docs/ECHO-RESEARCH.md` | tagging unreliable, one citation probably wrong (DeepVQE venue and arXiv number), numbers are ranges not data; what held up against our own data is listed in the digest |
| `AGY-PI-NEXT.md`, `AGY-PI-NEXT-v1.md` (brief `PROMPT-PI-NEXT.md`) | one-word interrupts and echo cancellation on the Pi | **unreliable**; see `docs/RESEARCH-HANDOFF-PI.md` for which leads were closed |
| `AGY-RESEARCH.md` | where to host speech models (laptop, Pi, cloud) | summary only |
| `AGY-STT-IMPROVE.md`, `AGY-PARAKEET-IMPROVE.md`, `AGY-PARETO-REPAIR.md` | improving recognition: hotwords, Parakeet settings, a repair step for names | used to choose Parakeet v2 plus hotwords and the name-repair ladder |
| `AGY-SPEC-DECODE.md` | early decode / speculative recognition accuracy | informed the early-send idea (not built) |
| `AGY-NET-PAPERS.md`, `AGY-NET-PRACTICE.md`, `AGY-NET-PRODUCTS.md` | poor-network practice for streaming speech | background |
| `AGY-MCCARTHY-A.md` to `-D.md` | four research prompts producing 33 corpus files on Cormac McCarthy (not citable; errors found and fixed are in `docs/CORPUS-MCCARTHY.md`) | research-grade only |
| `AGY-GEMINI-FAST.md` | Gemini models for a fast voice agent (IDs, price, tool calling, thinking controls) | model IDs and prices mostly held; its first latency ranking did not (checked live, see 2.3) |
| `AGY-CHEAP-LLM.md` | Groq and other cheap hosted LLMs from the Philippines | relies on 2024-era data; its top pick (`gemini-2.0-flash`) is retired; not trusted for rankings |

Other sources read directly by the assistant: Pipecat 1.13.0 source (services, frames, MCP client, worker idle timeout, the Gemini adapter and thought signatures), the LiveKit Python audio-processing API (`AudioProcessingModule`: 10 ms frames, `process_stream`, `process_reverse_stream`, `set_stream_delay_ms`), the OpenRouter model and provider-endpoint catalog (prices, tool support, 30-minute latency percentiles), the Gemini model list through the API, the speech-to-LLM network edge timings (TCP/TLS/first-byte to Groq, Google, Cerebras, DeepInfra, Together, Fireworks, Alibaba, OpenRouter), and the skills in `~/Documents/skills/mine` (`coding-with-simple-models`, `feature-engineering`, `dev-flow` on the branch `feat/role-composable-pipeline`).

### 2.2 Our own measurements: echo, barge-in, end of turn

| Document | What was measured | Headline |
|---|---|---|
| `docs/BARGE-IN.md` | barge-in design and laptop measurements; PipeWire WebRTC echo cancel on the laptop's own speakers | with it 0% of blocks flagged, echo below the room floor after the first play; the first play still leaks (about 11 dB) |
| `docs/INTERRUPTS.md` | model-free interruption bench and a detector ablation (`scripts/interrupt_bench.py`) | every software detector failed with a simulated leak; Silero worst |
| `docs/PI-RESULTS.md` | Pi echo test, split USB mic and speaker, with and without PipeWire AEC; live pilot of the gate; 7-of-10 rule; close-range microphone study | Silero self-triggers at 90% without AEC; AEC fixes 75% and 90% but not 60% (unexplained) and delays detection by about 1 s; the USB mic gives speech only 5-15 dB over its noise; the 0.5 confidence, 7-of-10, duck -15 dB gate works for loud sentences, not one quiet word |
| `docs/pi-results/` | the raw run folders (selftest and doubletalk, conditions C and E, volumes 40-90%) | |
| `docs/SILERO-SPEECH-COMMANDS.md` | does Silero hear a clean one-word "stop"? | yes on clean audio (so the Pi's low confidence is the microphone, not the model); the 250 ms hold costs short words |
| `docs/PI-BRIDGE.md` | Pi microphone and speaker with the laptop's speech models over a cable | stop of speech to first sound 1.99 s; a weak mic fragments long phrases at a 0.6 s hold, so 1.2 s was used |
| `docs/PI-REMOTE-SPEAKER.md` | laptop mic, Pi speaker: the echo, the `BargeInGuard`, the real `doc_chat` loop, Gemini, pivoting, metrics | the numbers in the prompt (measurement 1); 0 of 14 quiet replies interrupted by echo; pivot 5 of 5 |
| `docs/SMART-TURN.md` | Smart Turn v3 (audio), Turnsense (text), confirm-and-stitch simulation | none adopted |
| `docs/STT-HOSTING.md`, `docs/STT-COMPARE.md`, `docs/LAPTOP-SPEECH.md` | recognition accuracy, hotwords, name repair, padding, finalize latency, TTS speed | Parakeet v2 plus hotwords 4.5% word error on 25 live turns; finalize p50 0.32 s; TTS real-time factor p50 0.19 |
| `docs/AEC-REMOTE-SPEAKER.md` | the cross-device echo path and the canceller, offline, on real recordings; the probe | 137 ppm drift, coherence 0.5-0.8 once drift-corrected, offline recogniser results |
| `docs/LIVE-AGENT.md` | the agent in the live loop: quiet session, interruption sessions, the A/B | measurement 5 in the prompt; home commands 3.3 s from the last word to the first sound (two LLM requests); 2 echo false triggers in a quiet session |
| `docs/SMART-HOME-TOOL.md` | one `smart_home` tool; four system prompts; three LLMs; serial latency | native Gemini 3.1 flash-lite with a router prompt 50 of 50; first reply 0.52 s (plain) and 1.31 s (tool turn) from the LLM's input |

### 2.3 Checks and corrections made along the way (so the other agent is not sent down closed paths)

- The first offline cancellation result ("about 5 dB") was a harness fault, not the canceller: a fixed-delay reference cannot follow 137 ppm of drift; fixed by aligning per clip and, in the canceller, by letting WebRTC track the drift.
- A test that handed all the reference to the canceller at once lost most of it (the buffer keeps 3 s); the player feeds it 0.25 s ahead.
- A reference lead of 200 ms looked natural and was worse than 50 ms.
- Total-power reduction in dB is not a usable measure when the echo is near the room floor; counting detector fires and reading transcripts is.
- The "median echo" bar failed live (7 of 7 replies self-interrupted); the earlier harness had hidden it because its room was noisier (-35 against -40 dBFS).
- Gemini's OpenAI-compatible endpoint cannot be used with tools from Pipecat (phantom empty call, then a dropped `thought_signature`); the native service works. Groq `llama-3.1-8b` was fast but spoke its tool calls as text (26-40 of 48 right).
- agy's first latency ranking of Gemini models did not hold when checked against the live API (`gemini-3.5-flash-lite` was not faster than `gemini-3.1-flash-lite`).
- Two numbers in the first A/B write-up were wrong and are corrected here and in `docs/LIVE-AGENT.md`: the duck-only run had 2 spurious stop-music commands (not 1 wrong action), and the no-canceller session had echo words in about 6 of its 8 interruption transcripts (not 5). The corrected figures are in the prompt.
- A Pipecat pipeline worker cancels itself after about 5 minutes idle while the server keeps accepting requests; every request then timed out.

### 2.4 Open questions this brief hands over

Whether laptop microphone plus Pi loudspeaker satisfies the course brief (asked of the professor, unanswered); whether the speaker in front of the laptop works without echo-cancelling hardware; why cancellation costs opening words and why the combined run's stop took 1.34 s (one session, includes reaction time); why cancellation at 60% on the Pi's own devices failed while 75% and 90% worked; behaviour at 90% volume with cancellation; other speakers and rooms; the speaker distance and angle of the "in front" placement (not recorded).

---

## 3. What has been created

All of it is on branch `agy/interrupts` (68 commits ahead of `main`, 378 files, about +50.9k lines; pushed). Recordings under `out/` are git-ignored and stay on the laptop.

### 3.1 Speech server and test client (`src/`)

| Path | What it is |
|---|---|
| `src/lan_speech/` (10 modules) | the laptop speech server: WebSocket `/stt` and `/tts`, Parakeet re-decode with hotwords, the name-repair ladder, early decode, Supertonic TTS with clause chunking, bearer-token auth, pause detector |
| `src/doc_chat/__main__.py` | the voice test client: the turn loop, flags, session records, stop-phrase and pivot rules, cue-beep mode, guard trace, `--llm {none,qwen,gemini,echo,agent}`, `--aec`, `--duck-db` |
| `src/doc_chat/speech.py` | sockets, `listen_turn`, `MicHub` (one mic stream for the session, optional canceller) |
| `src/doc_chat/bargein.py` | `PreRoll`, `EchoModel` (learned speaker-to-mic coupling, quantile bar), `SileroOnset` (k-of-n, `reset`, `recent_speech`), `BargeInGuard` (Silero stage AND level stage), `Ducker` |
| `src/doc_chat/playback.py` | `Player` (local, fade-out on abort, `set_gain`), `RemoteSpeaker` / `RemotePlayer` (play on another machine's speaker; resample, pace, abort, `set_gain`), `mix_beep`, `make_player` |
| `src/doc_chat/aec.py` | `EchoCanceller`: WebRTC via LiveKit's `AudioProcessingModule`, reference = what is sent to the Pi, 10 ms frames, 50 ms lead |
| `src/doc_chat/agent_client.py` | `AgentChatModel`: a chat model that streams from the agent server; hanging up cancels the turn |
| `src/doc_chat/chain.py`, `corpus.py`, `dialogue.py`, `turnend.py`, `names.py`, `echo.py`, `phrases.py`, `gemini.py`, `qwen.py`, `pi_pilot.py`, `pi_check.py` | retrieval (BM25 over a corpus, follow-up detection), history and clarification, text turn-end model, name handling, the model-free echo responder, LLM backends, the Pi pilot gate and its check |
| `src/voice_agent/mcp_servers/docs.py` | a docs MCP server (fastmcp 2.14.7): `search_docs` over the McCarthy corpus; the precedent for tools as MCP servers |
| `pipeline/` (separate uv project; Pipecat 1.13 pins `onnxruntime~=1.24`) | `voice_pipeline.agent` (one Pipecat pipeline around native Gemini 3.1 flash-lite), `agent_tools`, `tools/smart_home.py` (19 intents, open slots, JSON phonebook for calls and messages), `agent_server.py` (ws://127.0.0.1:8781, a log row per turn), `dryrun.py` (72 prompts, 77 turns), config (`system_agent.txt`, four earlier system prompts, `agent_prompts.jsonl`, `phonebook.json`), `README.md`, 16 tests |
| `Makefile` | `check`, `test`, `pipeline-sync`, `pipeline-check`, `pipeline-test`, `agent-dryrun`, `agent-server`, `check-all`, `test-all` |

### 3.2 Scripts (`scripts/`)

- **Pi test kit and Pi roles:** `pi_echo_check.py`, `pi_double_talk_score.py`, `pi_pack_results.sh`, `make_pi_clips.py`, `pi_live_pilot.py`, `pi_gate_variants.py`, `pi_synth_report.py`, `pi_bridge.py` (Pi mic and speaker, laptop models), `pi_speaker.py` (the Pi as loudspeaker; `begin`/audio/`end`/`abort`/`gain`), `pi_record.py`, `aec.sh`, `aec_pi.sh`, `echo_check.py`.
- **Echo and interruption measurement:** `remote_echo_check.py` (the Pi speaker through the laptop mic, with a talk-over cue), `guard_replay.py` (replay the guard offline, sweep settings), `cue_report.py` (score a cue-beep session), `interrupt_bench.py`, `e2e_barge_in.py`, `e2e_ask_to_add.py`, **`echo_path_probe.py`** (known noise and tone through the Pi: delay drift, coherence, nonlinearity), **`aec_offline.py`** (runs the canceller on a recording and reads transcripts; `uv run --with livekit`), `silero_speech_commands.py`.
- **End of turn and STT:** `eot_replay.py`, `smart_turn_eval.py`, `smart_turn_dangling.py`, `text_turn_eval.py`, `text_turn_kroko.py`, `confirm_stitch_sim.py`, `stt_compare.py`, `stt_hotwords_eval.py`, `stt_repair_eval.py`, `stt_latency_bench.py`, `stt_finalize_latency.py`, `stt_early_eval.py`, `stt_provisional_eval.py`, `stt_padding_eval.py`, `score_tries.py`, `analyze_tries.py`, `try_stt.py`, `laptop_speech_bench.py`, `phone_bench.py`, `net_probe.py`.
- **Housekeeping and data:** `session_report.py`, `build_corpus.py`, `make_entities.py`, `make_synth_clips.py`, `fetch_laptop_models.py`, `fetch_turn_model.py`, `power_mode.sh`.

### 3.3 Tests

62 test and fixture files added or changed against `main`; at the tip `uv run --group laptop pytest -q` gives 597 passed, 9 skipped, 2 xfailed and 1 failed (`test_flow.py::test_llm_echo_is_accepted_without_documents`, which needs a speech server on 127.0.0.1:8765 and fails the same way before this work); the Pipecat project passes its own 16 (`make pipeline-check pipeline-test`); ruff and mypy strict are clean on everything touched.

### 3.4 Documents (`docs/`; index in `docs/INDEX.md`)

`HANDOFF.md` (the map; section 0 "Resume here"), `INDEX.md`, `BRANCHES.md`, `PI-TEST-PLAN.md`, `PI-RESULTS.md` and `pi-results/`, `PI-BRIDGE.md`, `PI-REMOTE-SPEAKER.md`, `RESEARCH-HANDOFF-PI.md`, `SILERO-SPEECH-COMMANDS.md`, `ECHO-RESEARCH.md`, `BARGE-IN.md`, `INTERRUPTS.md`, `SMART-TURN.md`, `LAPTOP-SPEECH.md`, `STT-HOSTING.md`, `STT-COMPARE.md`, `DOC-CHAT.md`, `IRL-RECIPE.md`, `CORPUS-MCCARTHY.md`, `COURSE-SUMMARY-BUILDING-AI-VOICE-AGENTS.md`, `LIVE-AGENT.md`, `AEC-REMOTE-SPEAKER.md`, `SMART-HOME-TOOL.md`, `AGY-CODING.md`, `MCP-SERVERS.md` (extended), and this file.

### 3.5 Data, config and assets

`corpus/cormac-mccarthy/` (33 files, about 25,600 words, researched by four search-only agy runs and hand-checked; not citable), `config/entities*.json` (name lists for repair), `config/pipewire-echo-cancel.conf`, `assets/silero_vad.onnx` (the exact Silero model tested), `tests/fixtures/pi_echo/` (six standard reply clips and the double-talk sentences). Local recordings (git-ignored): `out/remote_echo/{pi90,pi80,pi60,pi40b,guard60,talk70far,fresh80,probe-80}`, `out/sessions/` (the agent's quiet, interruption and A/B sessions; discarded runs carry `-idle-bug`, `-buggy`, `-0-aborted` suffixes), `out/pi_bridge/`, `out/pi/`, `pipeline/out/`.

### 3.6 Plans, tickets and agy runs (`.scratch/`)

- `.scratch/project2-voice-agent/`: `DESIGN.md` (decisions D1-D7 and a 2026-10-23 progress note), `tickets/` (01-28: the originals, eleven amended for the new direction, four added: 25 laddered TTS, 26 first-word recognition, 27 end of turn on spontaneous speech, 28 doc_chat as a tool), `RUNNER.md`, `AGY-GUARDRAILS.md`, `handoff/` (the earlier agy waves and the tech-lead review of tickets 01-14).
- `.scratch/smart-home-tool/`, `.scratch/live-agent/`, `.scratch/echo-fix/`: each has `PLAN.md`, level-3 `tickets/` with agy's execution logs and the review entries, `agy_work/` (`fence.sh`, the briefs, agy's final replies), and the generators and edit lists. These five agy waves came back byte-identical to the tickets with no deviations (method in `docs/AGY-CODING.md`).
- `.scratch/speech-hosting/`: the agy research reports and prompts from 2.1.

### 3.7 The order things were built (so the history can be read)

1. Pi test kit and the Pi echo experiments (`c768d76` to `4a9b3ef`); the 7-of-10 rule.
2. Pi bridge (Pi mic and speaker, laptop models): `f4543ef`; laptop mic with the Pi as loudspeaker and the echo guard: `c6986a6`; handoff: `ec221a7`.
3. McCarthy corpus, playback and stop-phrase fixes, cue-beep mode, the guard calibrated in the real loop, pivots and retrieval fixes, metrics: `98819f7` to `f56e73d`.
4. Ticket reassessment and LLM research (`f8db588`); the `smart_home` tool and the separate Pipecat project (`34335ef`); the agent in the live loop with a docs MCP server (`e9adf9d`); the echo-path measurement and offline cancellation (`78e3f0e`); live-agent documentation (`9224cde`).
5. Echo canceller and duck, behind `--aec` and `--duck-db` (`cdfaeb0`); the live test (`4119359`); the A/B (`ca8ca21`); the end-of-day handoff (`cadd31d`).
