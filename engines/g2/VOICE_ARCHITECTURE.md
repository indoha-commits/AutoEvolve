# Voice architecture

## Provider boundary

EdgeVoice, KokoroVoice, and SystemVoice return the same VoiceResult:

- waveform path;
- measured duration;
- provider and voice identity;
- delivery parameters;
- optional provider timing sidecar;
- timing offset introduced by pacing.

The renderer does not silently switch providers. A failed requested provider fails
the render, preserving provenance and preventing unnoticed quality regression.

## Pacing

The useful lesson retained from the NeuTTS experiment is explicit delivery state.
The NeuTTS runtime and its distorted waveform are not used. G2 represents delivery
as deterministic rate, pitch, leading pause, and trailing pause parameters attached
to each story purpose.

## Captions

Generated Edge narration uses its VTT word boundaries. Local engines use deterministic
word allocation across the measured scene waveform. Faster Whisper is reserved for
speech whose original transcript or timing is unknown.

## Quality policy

- normalize speech to approximately -16 LUFS with a -1.5 dB true-peak target;
- encode intermediate scene audio as 48 kHz mono PCM;
- encode final audio as 48 kHz AAC at 160 kbps;
- no voice cloning or reference audio;
- no accent selection from company geography;
- no provider fallback without an explicit new render request.
