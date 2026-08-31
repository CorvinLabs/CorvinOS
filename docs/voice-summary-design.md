# Voice Summary Redesign — Smart Context & Narration

## Problem

Current voice summaries:
- ❌ Blindly read the text (user can already read)
- ❌ No context about WHY decisions were made
- ❌ Miss key insights and trade-offs
- ❌ Sound robotic and unhelpful

Example (current):
> "Fixed bug in image loading. Updated the handler to check for null pointers. Added error logging."

Example (desired):
> "Quick win: we fixed an image loading bug that was silently crashing on null pointers. The fix was straightforward—just added a guard check, plus error logging so we can debug if it happens again. This unblocks the rendering pipeline we've been working on all week."

---

## New Voice Summary Architecture

### Three Layers

#### 1. **Analysis Layer** — Extract Meaning
Parse the assistant's response to understand:
- **Type**: Bug fix, feature, refactor, docs, etc.
- **Impact**: Who does this affect? Why does it matter?
- **Complexity**: Trivial? Risky? Well-tested?
- **Context**: Builds on prior work? Resolves a blocker?
- **Trade-offs**: What was considered? Why this approach?

#### 2. **Narration Layer** — Add Voice & Style
Transform insights into spoken English:
- **Opening hook**: Why this matters (user's context)
- **Body**: Key decisions + rationale (not just WHAT, but WHY)
- **Closing**: Impact or next step
- **Tone**: Match the user's profile (warm, concise, technical, etc.)

#### 3. **Delivery Layer** — Polish for Speech
Optimize for audio:
- Remove code blocks / replace with pronunciation hints
- Shorten complex technical terms (or spell them out)
- Add pauses for emphasis
- Cap at ~60 seconds for voice note (user can read code themselves)

---

## Implementation: Smart Summary Pipeline

### Step 1: Extract Metadata

```python
def analyze_response(text: str, prior_context: str = "") -> ResponseAnalysis:
    """Understand what the assistant did."""
    return {
        "type": classify_change_type(text),        # "feature" | "fix" | "refactor" | "docs"
        "scope": identify_scope(text),             # "local" | "component" | "system"
        "risk_level": assess_risk(text),           # "trivial" | "moderate" | "high"
        "blockers_resolved": find_blockers(text, prior_context),
        "testing_claim": extract_testing_claim(text),
        "trade_offs": extract_decisions(text),     # What was considered?
        "key_files": extract_changed_files(text),
        "user_benefit": distill_benefit(text),     # Why does user care?
    }
```

### Step 2: Generate Narrative

```python
def generate_voice_summary(analysis: ResponseAnalysis, user_profile: Profile) -> str:
    """Convert analysis into a natural spoken narrative."""
    
    # Opening: Why it matters (to the user, in their context)
    if analysis.blockers_resolved:
        opening = f"Good news: we unblocked {analysis.blockers_resolved[0]}."
    else:
        opening = f"We just {action_verb(analysis.type)} {analysis.key_files[0]}."
    
    # Body: The reasoning
    body_parts = [
        explain_what(analysis),      # What was done
        explain_why(analysis),       # Why this approach
        explain_trade_offs(analysis) # What was considered
    ]
    body = ". ".join(p for p in body_parts if p)
    
    # Closing: Impact or next step
    if analysis.risk_level == "high":
        closing = f"Worth watching — {suggest_verification(analysis)}."
    else:
        closing = suggest_next_step(analysis)
    
    # Assemble + polish for audio
    full = f"{opening} {body} {closing}"
    return polish_for_audio(full, user_profile)
```

### Step 3: Polish for Speech

```python
def polish_for_audio(text: str, profile: Profile) -> str:
    """Optimize for voice playback."""
    
    # Remove markdown, code blocks
    text = re.sub(r'```[\s\S]*?```', '[code block skipped]', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)  # Remove backticks, keep content
    
    # Expand acronyms based on user's profile
    if profile.voice_audience_jargon <= 2:
        text = expand_acronyms(text)  # "API" → "application programming interface"
    
    # Shorten if too long (>80 seconds @ 150wpm = ~200 words)
    if len(text.split()) > 200:
        text = summarize_technical_parts(text)
    
    # Add pauses for emphasis
    text = text.replace(". ", ". [pause] ")
    
    return text
```

---

## Examples

### Example 1: Bug Fix

**Raw response** (what the assistant wrote):
> "Fixed the image loading null pointer crash. Added a guard check in `handleImageLoad()` and improved error logging so we can see what's happening when this breaks again. The fix is 4 lines of code in `src/renderer/image.ts`."

**Analysis extracted**:
```json
{
  "type": "fix",
  "scope": "component",
  "risk_level": "trivial",
  "blockers_resolved": ["image rendering pipeline"],
  "key_files": ["src/renderer/image.ts"],
  "user_benefit": "Rendering is stable again",
  "trade_offs": "Could've added image preloading validation too, but that's future-proof"
}
```

**Voice summary**:
> "We fixed the image rendering crash — it was silently failing on null pointers. Quick guard check, plus better error logging so we can debug if it happens again. Four-line fix in the image renderer. This unblocks the rendering pipeline we've been working on, so that's a win."

**Why this is better**:
- ✅ Explains WHY it was crashing (null pointers)
- ✅ Explains WHY we added logging (debugging future issues)
- ✅ Adds context (unblocks pipeline)
- ✅ Casual tone (not robotic)
- ✅ ~60 seconds to read aloud

---

### Example 2: Complex Refactor

**Raw response**:
> "Refactored the profile migration system to use a factory pattern. Moved all version-specific logic into discrete handlers (`MigrationV1`, `MigrationV2`, etc.) so we can test each version independently. Added 8 unit tests covering all upgrade paths. The old monolithic function is gone."

**Analysis**:
```json
{
  "type": "refactor",
  "scope": "system",
  "risk_level": "moderate",
  "testing_claim": "8 unit tests covering all paths",
  "trade_offs": "More files/classes, but much more testable and maintainable",
  "key_files": ["src/migrations/"],
  "user_benefit": "Easier to add new migrations, fewer bugs"
}
```

**Voice summary**:
> "We tackled technical debt in the migration system. The old code had all version logic crammed into one giant function — hard to test, hard to extend. New approach: each migration version gets its own handler, so they're testable in isolation. We verified it works with eight unit tests covering every upgrade path. Trade-off: more files, but way more maintainable. Future migrations will be much faster to write."

**Why**:
- ✅ Explains the PROBLEM (monolithic, hard to test)
- ✅ Explains the SOLUTION (factory pattern, isolated handlers)
- ✅ Explains the VERIFICATION (8 tests)
- ✅ Explains the TRADE-OFF (more files, but worth it)
- ✅ Forward-looking ("future migrations will be faster")

---

## Integration Points

### 1. Voice Route (current: `voice.py`)
```python
# BEFORE
summary = strip_for_tts(full_response)  # ← just truncate

# AFTER
analysis = analyze_response(full_response, prior_context=session_context)
summary = generate_voice_summary(analysis, user_profile=session.profile)
summary = polish_for_audio(summary, user_profile=session.profile)
await tts.synthesize(summary)
```

### 2. Chat Response Handler
```python
# When assistant finishes writing, BEFORE sending to TTS:
if should_send_voice_summary:
    analysis = analyze_response(response.text, context)
    voice_text = generate_voice_summary(analysis, user.profile)
    # Cache for debugging
    response.voice_summary_generated = voice_text
    response.voice_summary_analysis = analysis
```

### 3. Profile Integration
Voice summary generation respects user's Identity settings:
```python
def generate_voice_summary(..., user_profile: Profile):
    if user_profile.voice_audience_jargon <= 1:
        # Explain technical terms
    if user_profile.voice_note_max_sentences:
        # Hard cap on length
    if user_profile.display_language == "de":
        # German prose (not translated — natively written)
```

---

## Testing Strategy

### 1. Unit Tests — Analysis Accuracy
```python
def test_analyze_response_identifies_bug_fix():
    text = "Fixed null pointer crash by adding guard check..."
    analysis = analyze_response(text)
    assert analysis.type == "fix"
    assert "null pointer" in analysis.key_issues

def test_extract_trade_offs():
    text = "Could've done X, but chose Y because Z..."
    analysis = analyze_response(text)
    assert len(analysis.trade_offs) > 0
```

### 2. Integration Tests — Full Pipeline
```python
def test_voice_summary_generation_end_to_end():
    response = "Fixed image loading bug. Added guard check..."
    user = User(profile=Profile(voice_audience_jargon=2))
    
    summary = generate_voice_summary(response, user)
    
    assert "why" in summary.lower() or "reason" in summary.lower()
    assert len(summary) < 300  # Reasonable for 60-second voice note
    assert "null" in summary or "crash" in summary  # Context preserved
```

### 3. Listening Tests (Manual)
- Record voice summaries for 10+ real responses
- Have 3 users rate:
  - ✓ Does this make sense without reading the chat?
  - ✓ Do I understand WHY this was done?
  - ✓ Is it helpful for someone just listening (e.g., in car)?
  - ✓ Does it match my tone/language preference?

---

## Rollout Plan

### Phase 1: Analysis Engine (v0.10.59)
- Implement `ResponseAnalysis` extractor
- Add unit tests
- NO changes to actual voice output yet

### Phase 2: Narration Engine (v0.10.60)
- Implement `generate_voice_summary()`
- A/B test: 50% old summaries, 50% smart summaries
- Gather feedback

### Phase 3: Full Rollout (v0.10.61)
- Default to smart summaries for all users
- Option to revert to simple mode in Settings → Voice
- Log which summaries are re-listened (signal of quality)

---

## Success Metrics

✅ **Voice note re-listen rate increases** (people replay because it's helpful)  
✅ **Average voice note length increases slightly** (richer content)  
✅ **Time to understand response decreases** (listening time <60s, not 2+ minutes of silent reading)  
✅ **User satisfaction** (profile feedback: "Voice summary was actually useful")  

---

## Open Questions

1. **Tone variation**: Should summary tone match user profile (warm vs. concise)? Or always match response formality?
   - **Decision**: Always derive from response formality + combine with profile warmth

2. **Context injection**: How much prior-chat context should we pull in?
   - **Decision**: Only if analysis flagged it (e.g., "blockers_resolved")

3. **Multilingual**: How to handle German/non-English users?
   - **Decision**: Native narration, not translation. German users get smart German summaries.

4. **Code examples**: Should we ever SAY code snippets aloud?
   - **Decision**: No. Replace code with functional description ("added a guard check") — users can read code themselves.
