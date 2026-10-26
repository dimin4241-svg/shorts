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
