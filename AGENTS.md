# Codex / Claude Code agent instructions

Goal: improve an original, safe and repeatable Shorts/Reels video production system for Russian-language creators.

- Do not copy or vendor ashishjamarkattel/Animator code; upstream fork currently has no declared license. This repository is an independent implementation.
- `python -m pytest -q` is required before commits. Run a local offline --demo to check that MP4 exists (low resolution for speed).
- No unreviewed public publishing, scraping protected content, credential handling, artificial engagement or manufactured metrics.
- Never commit `.env`, generated runs, API keys or billed media. API test executions must be opt-in.
- Claude/Anthropic can produce text and vector SVG code; it is not a native photo generator. For raster illustrations use OpenAI Images API.
- Make new providers optional and deterministic through mocked tests. Handle API timeouts, rates and malformed responses.
- Preserve 9:16 video shape, subtitles, and output metadata. Use proper Russian typography and avoid unsupported sensational factual claims.
- Suggested roadmap: animated vectors, voice transcription/timecoded karaoke captions, cost ledger, YouTube Studio CSV analytics, hook A/B testing, optional manual upload assistant, GUI.

## Russian speech QA (required for new narration features)

- Prefer Yandex SpeechKit (`--voice-provider yandex --voice ermil`) for the first native Russian baseline. Audition ElevenLabs multilingual v2 with a verified Russian voice ID before treating it as superior.
- Provider keys come exclusively from local .env. Never send keys in chat or record them in metadata.
- The pronunciation glossary must be explicit; do not automatically alter every 'е' to 'ё' or guess homograph stress. Never add Yandex + stress symbols to visible captions or subtitle text.
- If editing synthesis, add mocked tests checking the exact API method, speed, normalization and prior/next speech context.
- Prioritize short idiomatic spoken Russian with natural grammar; no word-by-word English translation.
- In a real video, audition the hook, test stress for ambiguous words and listen for clipped beginnings/endings. Make it optional to render preview audio without image API calls.
- Audio target: speech clarity and stable -16 LUFS with -1.5 dBTP ceiling. Don't automatically add unlicensed music or synthetic sound effects.
- Existing OpenAI /v1/audio/speech models are scheduled for retirement Jan 6 2027. Plan a separate documented migration; don't silently switch APIs.
