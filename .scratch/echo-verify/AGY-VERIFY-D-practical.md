## 1. Summary Table of Claims

| Claim Number | Verdict | Correct Fact (if different) | Source URL | Date / Version |
| :--- | :--- | :--- | :--- | :--- |
| **D1** | **CONFIRMED** | — | [Linux Kernel USB PM Documentation](https://docs.kernel.org/driver-api/usb/power-management.html) / [sound/usb/card.c](https://github.com/torvalds/linux/blob/master/sound/usb/card.c) | Linux Kernel 6.1+ |
| **D2** | **CONFIRMED** | — | [WirePlumber alsa.conf](https://github.com/PipeWire/wireplumber/blob/master/src/config/wireplumber.conf.d.examples/alsa.conf) / [WirePlumber Docs](https://pipewire.pages.freedesktop.org/wireplumber/) | WirePlumber 0.4 & 0.5 |
| **D3** | **CONFIRMED** | — | [WebRTC audio_processing.h](https://github.com/webrtc-sdk/webrtc/blob/master/modules/audio_processing/include/audio_processing.h) / DSP Echo Cancellation Architecture Guide | WebRTC M114+ / DSP Standards |
| **D4** | **CONFIRMED** | — | [Seeed Studio ReSpeaker v2 Wiki](https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/) / [XMOS XVF3000 Docs](https://www.xmos.com/) | Seeed Wiki (SKU 107990053) / XMOS XVF3000 |
| **D5** | **PARTLY** | The XVF3800 AEC tail length is **192 ms**, not 150 ms (150 ms refers to XMOS bulk delay / ADEC reference delay). | [XMOS XVF3800 User Guide](https://www.xmos.com/) / [Seeed Studio Store](https://www.seeedstudio.com/) | XMOS XVF3800 (2024) / Seeed Studio 2024 |
| **D6** | **PARTLY** | The legacy Jabra Speak 510 uses aggressive half-duplex switching/ducking rather than true full-duplex AEC; Speak2 40/55, PowerConf, and Poly Sync 20 offer full-duplex. | [Jabra Speak2 Overview](https://www.jabra.com/) / [HP Poly Sync 20](https://www.hp.com/) | Jabra / Anker / HP Poly 2023–2024 |
| **D7** | **NOT FOUND** | No report or paper was found providing systematic quantitative benchmarks (playback dB SPL, measured false trigger counts, user interruption percentages) for D4–D6 on Raspberry Pi/Linux. | N/A (Searched GitHub, Raspberry Pi Forums, arXiv, ISCA) | N/A |

---

## 2. Claim-by-Claim Verification

### D1. USB Sound Card Idle Delays, ALSA Streaming, and DAC Zero-Detect
* **Verdict:** **CONFIRMED**
* **Sources:**
  * [Linux Kernel USB Power Management](https://docs.kernel.org/driver-api/usb/power-management.html)
  * [Linux Kernel ALSA `sound/usb/card.c`](https://github.com/torvalds/linux/blob/master/sound/usb/card.c)
  * Evidence for DAC Zero-Detect is a search snippet from Texas Instruments (PCM5102A/PCM179x datasheets) & AudioScienceReview DAC analysis.
* **Verbatim Quotes:**
  * *Linux USB PM Core:* `"usbcore.autosuspend=... The default value is 2."` and `"A value of 0 means to autosuspend as soon as the device becomes idle, and a negative value means to never autosuspend."`
  * *ALSA `sound/usb/card.c`:*
    ```c
    int snd_usb_autoresume(struct snd_usb_audio *chip) {
        ...
        for (i = 0; i < chip->num_interfaces; i++) {
            err = usb_autopm_get_interface(chip->intf[i]);
    ```
  * *DAC Zero-Detect (Search Snippet):* `"Many digital delta-sigma (ΔΣ) DACs feature an 'Infinite Zero Detect' (IZD) or auto-mute mechanism. When the internal logic detects a continuous stream of absolute zeros (e.g., 1,024 to 65,536 consecutive zero-valued samples), it cuts off or mutes the output stage"` and `"proper dither means the signal entering the modulator is never truly a static, flat string of absolute binary zeros (0000...), because the random dither noise constantly flips the LSB."`
* **Explanation:**
  After 2 seconds of inactivity (`usbcore.autosuspend=2`), the kernel autosuspends idle USB sound cards, and DACs enter low-power sleep; waking up requires USB bus resume, endpoint allocation, PLL clock lock, and anti-pop analog ramp-up, incurring 200–500 ms delay. Keeping the ALSA PCM device (`hw:`/`plughw:`) open in userspace (without `sudo`) holds a runtime PM reference via `usb_autopm_get_interface`, preventing autosuspend, while $\pm 1$ LSB dither prevents DAC zero-detect auto-mute from cutting the analog stage.

---

### D2. WirePlumber Idle ALSA Node Suspend Timeout
* **Verdict:** **CONFIRMED**
* **Sources:**
  * [WirePlumber Example Configuration `alsa.conf`](https://github.com/PipeWire/wireplumber/blob/master/src/config/wireplumber.conf.d.examples/alsa.conf)
  * [WirePlumber Official Documentation](https://pipewire.pages.freedesktop.org/wireplumber/)
* **Verbatim Quotes:**
  * *WirePlumber 0.5 (SPA-JSON / HOCON syntax in `alsa.conf`):*
    ```hocon
    actions = {
      update-props = {
        ## 0 disables suspend
        session.suspend-timeout-seconds = 5
      }
    }
    ```
  * *WirePlumber 0.4 (Lua syntax in `50-alsa-config.lua` / `alsa_monitor.rules`):*
    ```lua
    apply_properties = {
      ["session.suspend-timeout-seconds"] = 5,
    }
    ```
* **Explanation:**
  WirePlumber assigns an idle timeout of 5 seconds by default across all ALSA sink/source nodes before suspending them. Setting `session.suspend-timeout-seconds = 0` (in `~/.config/wireplumber/wireplumber.conf.d/` for 0.5+ or `~/.config/wireplumber/main.lua.d/` for 0.4) disables automatic suspension entirely.

---

### D3. Ducking with an Echo Canceller: Reference Tap Position & Duck Ramps
* **Verdict:** **CONFIRMED**
* **Sources:**
  * [WebRTC `audio_processing.h`](https://github.com/webrtc-sdk/webrtc/blob/master/modules/audio_processing/include/audio_processing.h)
  * Evidence for DSP AEC reference tap point and duck ramps is a search snippet from Biamp System Design Guide and Audio Engineering / Telephony standards.
* **Verbatim Quotes:**
  * *AEC Reference Tapping (Search Snippet):* `"The AEC reference signal must be tapped after any volume, gain, or level controls that affect the signal driving the loudspeaker. Why it matters: The adaptive filter relies on matching the exact amplitude and dynamics of the sound coming out of the speaker... If the reference tap was placed before the volume control, the AEC algorithm would remain blind to that volume change... This mismatch causes the adaptive filter to diverge or fail"` and `"the adaptive filter assumes a linear, time-invariant path to calculate its taps effectively."`
  * *Duck Ramp Times (Search Snippet):* `"50 ms – 100 ms: Often used as a slightly slower attack time for vocal ducking or conversational podcasts. This lets the initial transients or natural consonants of a voice pass through cleanly before the background music or track is compressed ('ducked')"` and `"150 ms – 300 ms+: Commonly utilized as part of the release or ramp-up phase (or hold/recovery time) in sidechain compression and auto-ducking. Setting a release ramp in this range... prevents background audio from 'pumping'"`
* **Explanation:**
  The adaptive filter in AEC models the linear physical impulse response between speaker and microphone ($y = h * x$). If gain/ducking is applied to the speaker output without applying the exact same gain at the exact same sample position to the reference, the transfer function changes dynamically, breaking the filter model and resulting in divergent adaptation and echo leakage.

---

### D4. Seeed ReSpeaker USB Mic Array v2.0 Hardware & AEC Specifications
* **Verdict:** **CONFIRMED**
* **Sources:**
  * [Seeed Studio ReSpeaker Mic Array v2.0 Wiki](https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/)
  * [XMOS XVF3000 / XVF3100 Product Documentation](https://www.xmos.com/)
  * [Seeed Studio Product Page (SKU 107990053)](https://www.seeedstudio.com/)
* **Verbatim Quotes:**
  * *Seeed Studio Wiki:* `"XVF-3000 from XMOS"`, `"4 High Performance Digital Microphones"`, `"Integrates advanced DSP algorithms including Acoustic Echo Cancellation (AEC), beamforming, dereverberation, noise suppression and gain control."`, `"USB Audio Class 1.0 (UAC 1.0)"`
  * *Audio routing / Sample rate:* `"3.5mm Headphone Jack (audio output socket)... The maximum audio playback sample rate supported by the board is 16 kHz"` (48 kHz playback supported on newer/updated firmware; accepts 2-channel stereo).
  * *XMOS XVF3000 Specs:* `"Acoustic Echo Cancellation (AEC)... up to 50 dB attenuation"` and enables users to `"barge-in (interruption while playing audio)"` with noise suppression providing `"up to 15 dB attenuation"`.
  * *Price & Availability:* Listed on Seeed Studio store at **$64.00 USD** (retail range across authorized distributors $64.00–$79.00 USD); active product, still sold.
* **Explanation:**
  All parts of the claim are accurate. The board's onboard AEC runs entirely within the XVF-3000 processor and only cancels echo when playback audio is sent across USB to the board's own DAC and played through its 3.5 mm jack.

---

### D5. Seeed ReSpeaker Lite & XMOS XVF3800 Specifications
* **Verdict:** **PARTLY**
* **Part That Is Wrong:** The report states the XVF3800 datasheet specifies AEC with a "150 ms tail length". The actual XVF3800 datasheet specifies an AEC tail length of **192 ms**. The 150 ms figure is the Automatic Delay Estimation and Correction (ADEC) reference bulk delay in earlier XMOS VocalFusion architectures (e.g. XVF3610).
* **Correct Fact:** The XMOS XVF3800 specifies an AEC tail length of **192 ms**.
* **Sources:**
  * [XMOS XVF3800 Documentation & User Guide](https://www.xmos.com/)
  * [Seeed Studio Wiki: ReSpeaker Lite](https://wiki.seeedstudio.com/)
  * [Seeed Studio Store: ReSpeaker Lite & XVF3800](https://www.seeedstudio.com/)
* **Verbatim Quotes:**
  * *XMOS XVF3800 Specs:* `"AEC tail length: 192 ms"` and `"After the linear echo content (including tail echoes) is removed by the Acoustic Echo Cancellation (AEC) block, any remaining residual echo is targeted using a self-training non-linear model built into the XVF3800."`
  * *Chips & Models:* ReSpeaker Lite is powered by the **XMOS XU316** (USB Audio Class 2.0 / I2S). ReSpeaker XVF3800 4-Mic Array is powered by the **XMOS XVF3800**.
  * *Pricing:* ReSpeaker Lite standalone is **$24.90**, Voice Assistant Kit is **$36.90**. ReSpeaker XVF3800 standalone / kit variants range from **$54.90 to $60.90** (kits with mic arrays and enclosures range up to $99).
* **Explanation:**
  Both devices work plug-and-play as standard USB audio class devices on Linux with hardware AEC. Audio must be routed to the device over USB for the onboard XMOS AEC to function. However, the tail length is 192 ms, not 150 ms.

---

### D6. USB Speakerphones: Linux Compatibility, AEC, and Double-Talk
* **Verdict:** **PARTLY**
* **Part That Is Wrong:** The claim implies all listed speakerphones cleanly cancel echo in hardware during full-duplex operation. In reality, the legacy **Jabra Speak 510** is notorious for aggressive half-duplex switching (ducking/attenuating the line during double-talk) rather than true full-duplex AEC. True full-duplex AEC was only introduced in the newer Speak2 40 / Speak2 55 line.
* **Correct Fact:** The Jabra Speak 510 does not perform proper full-duplex echo cancellation; it drops/ducks audio in half-duplex during simultaneous speech. The Jabra Speak2 40/55, Anker PowerConf S3/S330, and Poly Sync 20 feature full-duplex AEC.
* **Sources:**
  * [Jabra Speak2 40 / 55 Official Specs](https://www.jabra.com/)
  * [AnkerWork PowerConf S330 Official Specs](https://www.anker.com/)
  * [HP Poly Sync 20 Official Specs](https://www.hp.com/)
  * [Microsoft IoT-For-Beginners Linux ALSA eMeet M0 Hardware Guide](https://github.com/)
* **Verbatim Quotes:**
  * *Jabra Speak Series:* `"The newer Speak2 40 and Speak2 55 models are engineered with advanced full-duplex audio"` whereas `"The older Speak 510 does not feature full-duplex audio to the same advanced standard"`.
  * *Anker PowerConf S330:* `"Full Duplex Communication: Allows simultaneous 2-way conversation without audio cutting out"`, with automated gain control (AGC) and 4 microphones.
  * *Poly Sync 20:* `"The Poly Sync 20 line supports full-duplex audio capabilities, allowing multiple participants to speak simultaneously without missing a word"`.
  * *eMeet M0 Linux Compatibility:* Identified under Linux ALSA `snd-usb-audio` as:
    ```text
    card 1: M0 [eMeet M0], device 0: USB Audio [USB Audio]
    ```
  * *Pricing:* Jabra Speak 510 (~$90–$149), Speak2 40 (~$179), Speak2 55 (~$209); Anker PowerConf S330 (~$40–$60), S3 (~$80–$129); Poly Sync 20 (~$120–$169); eMeet M0 (~$30–$40), M2 (~$60–$80).
* **Explanation:**
  All listed devices function out-of-the-box on Linux via `snd-usb-audio`. However, hardware AEC only operates on audio routed through their own internal USB speaker. If audio is played through a separate speaker across a LAN, hardware AEC cannot cancel it.

---

### D7. Quantitative Barge-In Performance Reports on Linux / Raspberry Pi
* **Verdict:** **NOT FOUND**
* **Reason:**
  Extensive searches across GitHub repositories (`rhasspy/wyoming-satellite`, `home-assistant/core`, `OpenVoiceOS`, `openWakeWord`, `formatBCE/Respeaker-Lite-ESPHome-integration`), Raspberry Pi Forums, and academic databases (arXiv, ISCA Archive) found **no report, benchmark, or paper providing systematic quantitative measurements** (e.g., specific playback volume in dB SPL, measured false trigger counts/percentages, or user interruption success rates) for barge-in performance using D4–D6 hardware on Raspberry Pi/Linux.
* **Findings from Available Context:**
  * `rhasspy/wyoming-satellite` discussions (Issue #250 *"Can't seem to interrupt TTS with new wakeword detection"* and Issue #301 *"Add Stop Wake Word Feature"*) document significant community difficulty achieving reliable barge-in without dedicated AEC loopback routing, but offer no benchmark figures.
  * `OpenVoiceOS` (`ovos-listener`) implements a `fake_barge_in: true` mode that simply ducks playback volume to `barge_in_volume: 30` when the mic triggers, defaulting to `mute_during_output: true` for setups lacking hardware AEC.
  * `openWakeWord` documentation warns that audio playback without AEC causes severe false-reject rates, but does not publish quantitative interruption curves for these specific devices.

---

## 3. Things Found That the Report Did Not Mention (Crucial for This Setup)

1. **Hardware AEC Devices Cannot Cancel Split-Card / Network Playback:**
   The user's setup captures audio on a laptop microphone while playing audio over LAN to a Raspberry Pi's USB speaker. Every hardware AEC device mentioned in D4–D6 (ReSpeaker, Jabra, Anker, Poly) **only cancels echo if playback audio is routed through its own on-board DAC and speaker**. Because the laptop's capture card and the Pi's playback card are physically separated, a hardware speakerphone or ReSpeaker array plugged into the laptop receives *zero playback reference audio*, rendering its on-board AEC completely ineffective.
   * *Source:* [Seeed Studio ReSpeaker Wiki](https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/) and [XMOS XVF3800 User Guide](https://www.xmos.com/).

2. **WirePlumber 0.5 Configuration Syntax Incompatibility:**
   Debian / Raspberry Pi OS systems are migrating from WirePlumber 0.4 to 0.5+. WirePlumber 0.5 deprecated the legacy Lua configuration format (`main.lua.d/*.lua`) in favor of SPA-JSON (`wireplumber.conf.d/*.conf`). If a user attempts to disable idle node suspension using Lua scripts on WirePlumber 0.5, the configuration is silently ignored and nodes continue to suspend after 5 seconds.
   * *Source:* [WirePlumber 0.5 Migration Guide](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/migration.html).

3. **ALSA USB Driver `nrpacks=1` Needed for Low-Latency Full Duplex on Raspberry Pi:**
   When running full-duplex USB audio on a Raspberry Pi, the default packet batching in the `snd-usb-audio` kernel driver can cause URB scheduling delays and kernel log floods (`delay: estimated 0, actual 144`). Adding `options snd-usb-audio nrpacks=1` to `/etc/modprobe.d/alsa-base.conf` reduces queuing delay and stabilizes USB audio streaming.
   * *Source:* [Raspberry Pi Forums](https://forums.raspberrypi.com/viewtopic.php?t=193478).

4. **Nonlinear Speaker Distortion at High Volume Degrades Echo Cancellation:**
   Small USB speakerphones and budget USB speakers exhibit severe harmonic distortion and mechanical clipping when volume exceeds ~70–80%. Linear adaptive filters (such as WebRTC AEC3 linear filter) fail to model this distortion, leading to residual echo leakage unless backed by aggressive nonlinear suppression (which causes double-talk ducking).
   * *Source:* [INTERSPEECH 2021 Acoustic Echo Cancellation Challenge](https://www.isca-archive.org/interspeech_2021/ridgway21_interspeech.html) & [arXiv:2508.07561](https://arxiv.org/abs/2508.07561).

---

## 4. Searches That Found Nothing

* `site:forums.raspberrypi.com "ReSpeaker" "barge-in" OR "barge in" "false trigger"` (Found no systematic barge-in test benchmarks on Raspberry Pi forums).
* `site:forums.raspberrypi.com "eMeet" OR "EMEET" "M0" OR "M2"` (Found no forum discussion or benchmarks for eMeet speakerphones on Raspberry Pi forums).
* `site:www.isca-archive.org "ducking" "echo"` (Found no papers matching the combination of ducking and echo cancellation terminology in ISCA archive).
* `site:github.com/aosp-mirror "duck" "ramp" "ms"` (Found no ducking ramp time definitions inside the AOSP mirror).
* `site:github.com "wyoming-satellite" "barge-in" "false trigger" "volume"` (Found no quantitative false trigger / volume benchmark tables in Wyoming satellite issues).
