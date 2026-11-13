# Voice Design prompts for a JARVIS-style voice (needs ElevenLabs Starter or above)

Use `POST /v1/text-to-voice/design` with `model_id: eleven_ttv_v3`, `loudness: 0.3`, a fixed preview text, 3-4 seeds per prompt;
save the best preview with `POST /v1/text-to-voice`. Agent settings: eleven_v4_turbo, stability 0.60-0.70, similarity 0.75-0.80.
Cloning Paul Bettany's audio is against ElevenLabs's use policy (section 5) and is a ban risk; don't.

Preview text: "Good evening, sir. I've reviewed the schedule. There are three items that require your attention, none of them urgent. The forecast is clear, traffic is light, and your two o'clock has moved to three. Shall I begin with the messages, or would you prefer the briefing first?"

## Prompt A (period-accurate butler AI)
British English, Received Pronunciation, male, late 30s to mid 40s. Studio quality recording. Persona: an impeccably polite artificial-intelligence butler. Emotion: calm, dry, quietly amused. Smooth light baritone with crisp consonants and precise diction. Measured, unhurried pacing with brief pauses between clauses. Low pitch variation, never excited, faint warmth under a formal reserve. Sounds composed and slightly synthetic, as if every word was chosen in advance.

## Prompt B (modern RP, warmer)
Native British English speaker from southern England, modern RP, male, around 40, broadcast quality audio. Persona: discreet personal assistant to a wealthy inventor. Emotion: unflappable, courteous, subtly wry. Mid-to-low pitch, clean and resonant, no breathiness. Even tempo on the slower side, clear sentence endings that fall rather than rise. Understated delivery with a hint of dry wit, as though gently teasing while remaining entirely professional.

## Prompt C (slightly synthetic, computer-like)
British English, RP, male, ageless adult voice, excellent studio quality. Persona: the calm voice of a household computer system. Emotion: serene, attentive, faintly ironic. Flat affect with minimal pitch movement, perfectly articulated consonants, soft but present vowels. Deliberate, steady rhythm with short pauses before key information. Polished and refined, with just enough warmth to sound caring rather than cold.
