# assets/

Small binary files that the code needs and that are not downloaded.

## silero_vad.onnx (2.3 MB)

The Silero voice-activity-detection model, **the copy bundled with Pipecat** (`pipecat/audio/vad/data/silero_vad.onnx`), which is what main's design (tickets 11/12) runs on the Raspberry Pi.

- SHA-256: `597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004`
- Source: https://github.com/snakers4/silero-vad (MIT licence, (c) Silero Team)
- Interface (checked): inputs `input` [1, 64 + 512] float32, `state` [2, 1, 128], `sr` int64; outputs the speech probability and the new state. `doc_chat.bargein.SileroOnset` mirrors Pipecat's `SileroOnnxModel`.
- **Do not replace it with the file from the silero-vad GitHub `master` branch**: that file has the same size but a different hash (a newer release) and has not been tested here.
- `models/silero-vad/silero_vad.onnx` on a machine that has run main's downloads is a different (older, 644 KB) file; ignore it.
