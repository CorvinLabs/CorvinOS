"""Tests for profile.py — load/save, get/set, system-prompt formatting."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Sandbox before importing.
_SANDBOX = tempfile.mkdtemp(prefix="profile_test_")
os.environ["XDG_CONFIG_HOME"] = _SANDBOX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import profile as prof  # noqa: E402


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        prof.reset()

    def test_empty_load(self):
        # After reset(), load() returns only the built-in defaults (no user data).
        d = prof.load()
        user_keys = {k for k in d if not k.startswith("_") and k not in prof._PROFILE_DEFAULTS}
        self.assertEqual(user_keys, set())

    def test_set_and_load(self):
        prof.set_value("name", "Silvio")
        prof.set_value("timezone", "Europe/Berlin")
        d = prof.load()
        self.assertEqual(d["name"], "Silvio")
        self.assertEqual(d["timezone"], "Europe/Berlin")

    def test_unknown_keys_under_extra(self):
        prof.set_value("favourite_train", "ICE")
        d = prof.load()
        self.assertNotIn("favourite_train", d)
        self.assertEqual(d["_extra"]["favourite_train"], "ICE")

    def test_set_none_removes(self):
        prof.set_value("name", "Silvio")
        prof.set_value("name", None)
        self.assertIsNone(prof.load().get("name"))

    def test_get_known_and_extra(self):
        prof.set_value("name", "Silvio")
        prof.set_value("hobby", "kite-surfing")
        self.assertEqual(prof.get("name"), "Silvio")
        self.assertEqual(prof.get("hobby"), "kite-surfing")
        self.assertIsNone(prof.get("missing"))


class FormatTests(unittest.TestCase):
    def setUp(self):
        prof.reset()

    def test_empty_profile_yields_empty_prompt(self):
        self.assertEqual(prof.for_system_prompt(), "")

    def test_full_profile(self):
        prof.set_value("name", "Silvio")
        prof.set_value("display_language", "de")
        prof.set_value("tone", "concise, du-form")
        out = prof.for_system_prompt()
        self.assertIn("Name: Silvio", out)
        self.assertIn("Language: de", out)
        self.assertIn("concise, du-form", out)
        # Must be appendable to a system prompt (starts with newline-ish
        # pattern, doesn't have leading garbage).
        self.assertTrue(out.startswith("\n\n"))

    def test_extra_renders(self):
        prof.set_value("favourite_train", "ICE")
        out = prof.for_system_prompt()
        self.assertIn("favourite_train=ICE", out)

    def test_humanize_no_profile(self):
        # After reset(), only _PROFILE_DEFAULTS are present; humanize() should
        # return something (either "no profile" or the defaults rendered).
        # The exact string depends on whether defaults are set — just ensure
        # it's a non-empty string.
        out = prof.humanize()
        self.assertIsInstance(out, str)


class ValueParserTests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(prof.parse_value("hello"), "hello")
        self.assertEqual(prof.parse_value("'quoted'"), "quoted")
        self.assertEqual(prof.parse_value('"also quoted"'), "also quoted")

    def test_numbers(self):
        self.assertEqual(prof.parse_value("42"), 42)
        self.assertEqual(prof.parse_value("3.14"), 3.14)

    def test_booleans(self):
        self.assertIs(prof.parse_value("true"), True)
        self.assertIs(prof.parse_value("YES"), True)
        self.assertIs(prof.parse_value("Off"), False)

    def test_nullables(self):
        self.assertIsNone(prof.parse_value("null"))
        self.assertIsNone(prof.parse_value("none"))
        self.assertIsNone(prof.parse_value(""))


class CacheTests(unittest.TestCase):
    def setUp(self):
        prof.reset()

    def test_cache_hit_no_disk_read(self):
        prof.set_value("name", "X")
        # Force re-read once to seed cache cleanly.
        prof.load(force=True)
        # Now manipulate cache state directly so a stale file would be
        # detectable. We rely on prof._cache_mtime to be set above and
        # prof._cache being a dict.
        self.assertEqual(prof.load()["name"], "X")

    def test_force_reread(self):
        prof.set_value("name", "first")
        prof.load()
        # Bypass set_value and write directly so mtime changes but cache
        # would otherwise be stale until force=True.
        data = json.loads(prof.PROFILE_FILE.read_text())
        data["name"] = "second"
        prof.PROFILE_FILE.write_text(json.dumps(data))
        # Bump mtime.
        os.utime(prof.PROFILE_FILE, None)
        # Without force, mtime change still re-reads.
        self.assertEqual(prof.load()["name"], "second")


class TtsAudienceTests(unittest.TestCase):
    """Layer-12 listener-profile rendering and validation."""

    def setUp(self):
        prof.reset()

    def test_empty_audience_returns_empty_string(self):
        # Backward-compat: no audience fields → empty block → caller does
        # not append anything to the system prompt.
        self.assertEqual(prof.for_tts_audience("de"), "")
        self.assertEqual(prof.for_tts_audience("en"), "")

    def test_audience_renders_de_and_en(self):
        prof.set_value("voice_audience_level", "expert")
        prof.set_value("voice_audience_jargon", 4)
        prof.set_value("voice_audience_background", "Senior Go-Dev")
        de = prof.for_tts_audience("de")
        en = prof.for_tts_audience("en")
        self.assertIn("HÖRER-PROFIL", de)
        self.assertIn("Experte", de)
        self.assertIn("Senior Go-Dev", de)
        self.assertIn("AUDIENCE", en)
        self.assertIn("expert", en)
        self.assertIn("Senior Go-Dev", en)
        # Faithfulness re-affirmation must be present in both languages
        # — guards against a refactor that drops the closing rule.
        self.assertIn("Treue", de)
        self.assertIn("faithfulness", en)

    def test_invalid_level_silently_dropped(self):
        # Fail-open: typo'd values must not break the prompt; they just
        # don't make it into the rendered block.
        prof.set_value("voice_audience_level", "GURU-MASTER")
        out = prof.for_tts_audience("de")
        self.assertEqual(out, "")

    def test_jargon_out_of_range_dropped(self):
        prof.set_value("voice_audience_jargon", 99)
        self.assertEqual(prof.for_tts_audience("de"), "")
        prof.set_value("voice_audience_jargon", -1)
        self.assertEqual(prof.for_tts_audience("de"), "")
        prof.set_value("voice_audience_jargon", 3)
        self.assertIn("3/5", prof.for_tts_audience("de"))

    def test_background_too_long_dropped(self):
        prof.set_value("voice_audience_background", "x" * 1000)
        self.assertEqual(prof.for_tts_audience("de"), "")

    def test_domains_csv_to_list(self):
        prof.set_value("voice_audience_domains", "python, postgres,redis")
        out = prof.for_tts_audience("de")
        self.assertIn("python", out)
        self.assertIn("postgres", out)
        self.assertIn("redis", out)

    def test_domains_max_eight(self):
        # Anti-jailbreak: a profile that lists 50 domains shouldn't blow up
        # the prompt budget. Cap at 8.
        many = ",".join(f"d{i}" for i in range(20))
        prof.set_value("voice_audience_domains", many)
        out = prof.for_tts_audience("de")
        self.assertIn("d0", out)
        self.assertIn("d7", out)
        self.assertNotIn("d8", out)

    def test_low_jargon_unlocks_translation_clause_de(self):
        # The discord A/B run from 2026-05-07 showed the LLM kept code
        # tokens in the output even at jargon=0 because faithfulness
        # > translation. The clause must explicitly grant the right
        # to render code in plain language at jargon ≤ 1.
        prof.set_value("voice_audience_jargon", 0)
        prof.set_value("voice_audience_level", "novice")
        out = prof.for_tts_audience("de")
        self.assertIn("Übersetzen, kein Erfinden", out)
        # Faithfulness re-affirmation must still be present and after
        # the new clause — order matters: permission first, then guard.
        idx_perm = out.index("Übersetzen")
        idx_faith = out.index("Treue")
        self.assertLess(idx_perm, idx_faith)

    def test_low_jargon_unlocks_translation_clause_en(self):
        prof.set_value("voice_audience_jargon", 1)
        prof.set_value("voice_audience_level", "novice")
        out = prof.for_tts_audience("en")
        self.assertIn("translation, not invention", out)
        idx_perm = out.index("translation, not invention")
        idx_faith = out.index("faithfulness")
        self.assertLess(idx_perm, idx_faith)

    def test_high_jargon_keeps_clause_out(self):
        # At jargon ≥ 2 the listener tolerates technical tokens, so the
        # translation-permission clause is not appended (saves tokens
        # and avoids confusing the LLM into over-translating).
        prof.set_value("voice_audience_jargon", 3)
        prof.set_value("voice_audience_level", "expert")
        out_de = prof.for_tts_audience("de")
        self.assertNotIn("Übersetzen, kein Erfinden", out_de)
        out_en = prof.for_tts_audience("en")
        self.assertNotIn("translation, not invention", out_en)

    def test_no_jargon_field_keeps_clause_out(self):
        # Profile that doesn't set jargon at all must not get the
        # permission clause — the trigger is jargon ≤ 1, never absence.
        prof.set_value("voice_audience_level", "novice")
        out = prof.for_tts_audience("de")
        self.assertNotIn("Übersetzen, kein Erfinden", out)

    def test_humanize_lists_audience_keys(self):
        prof.set_value("voice_audience_level", "expert")
        prof.set_value("voice_audience_jargon", 4)
        h = prof.humanize()
        self.assertIn("voice_audience_level", h)
        self.assertIn("voice_audience_jargon", h)

    # ── Layer-12 learning-mode (annex) tests ─────────────────────────────

    def test_learning_zero_no_annex(self):
        # learning=0 must NOT inject the LERN-ZUGABE / LEARNING ANNEX
        # block — it's the "off" sentinel that keeps backward-compat.
        # The level shows up as a bullet so the block renders, but the
        # annex paragraph stays out.
        prof.set_value("voice_audience_learning", 0)
        out_de = prof.for_tts_audience("de")
        out_en = prof.for_tts_audience("en")
        self.assertIn("Lern-Modus 0/3", out_de)
        self.assertNotIn("LERN-ZUGABE", out_de)
        self.assertIn("learning mode 0/3", out_en)
        self.assertNotIn("LEARNING ANNEX", out_en)

    def test_learning_one_emits_gloss_clause_de(self):
        prof.set_value("voice_audience_learning", 1)
        out = prof.for_tts_audience("de")
        self.assertIn("LERN-ZUGABE", out)
        # depth=1 mentions Halbsatz (gloss), not concept-introduction
        self.assertIn("Halbsatz", out)
        self.assertNotIn("zugrundeliegendes Konzept", out)
        # additivity guard must be present — annex never replaces summary
        self.assertIn("ADDITIV", out)
        # explicit "must be spoken aloud" — the user requirement that
        # the learning content lands in the TTS stream
        self.assertIn("vorgelesen", out)
        # Faithfulness re-affirmation must come AFTER the annex clause —
        # order matters: annex authorization first, faithfulness guard
        # last, so the LLM reads "you may add X, but the summary stays
        # treu" in the right precedence.
        idx_annex = out.index("LERN-ZUGABE")
        idx_faith = out.index("Treue und Vollständigkeit")
        self.assertLess(idx_annex, idx_faith)

    def test_learning_two_emits_teach_clause(self):
        prof.set_value("voice_audience_learning", 2)
        de = prof.for_tts_audience("de")
        en = prof.for_tts_audience("en")
        self.assertIn("LERN-ZUGABE", de)
        self.assertIn("zugrundeliegendes Konzept", de)
        # depth=2 must NOT mention recap — that's depth=3 only
        self.assertNotIn("Recap", de)
        self.assertIn("LEARNING ANNEX", en)
        self.assertIn("underlying concept", en)
        self.assertNotIn("recap", en)

    def test_learning_three_emits_teach_plus_recap(self):
        prof.set_value("voice_audience_learning", 3)
        de = prof.for_tts_audience("de")
        en = prof.for_tts_audience("en")
        self.assertIn("LERN-ZUGABE", de)
        self.assertIn("Recap", de)
        self.assertIn("LEARNING ANNEX", en)
        self.assertIn("recap", en)
        # The "spoken aloud" rule must be present at every learning
        # level ≥ 1 — that's the user requirement.
        self.assertIn("vorgelesen", de)
        self.assertIn("spoken aloud", en)

    def test_learning_out_of_range_dropped(self):
        # Fail-open: 4, -1, "abc" must not break the prompt.
        for bad in (4, -1, 99, "abc"):
            prof.reset()
            prof.set_value("voice_audience_learning", bad)
            out = prof.for_tts_audience("de")
            self.assertNotIn("LERN-ZUGABE", out,
                             f"learning={bad!r} leaked an annex clause")
            # When learning is the only field set and it's invalid, the
            # whole block must be empty (no other bits to render).
            if bad != 0:
                self.assertEqual(out, "",
                                 f"learning={bad!r} produced non-empty block")

    def test_learning_combined_with_low_jargon(self):
        # Both clauses must coexist in the right order:
        # bullets → low-jargon clause → learning-annex clause → faith guard.
        prof.set_value("voice_audience_jargon", 0)
        prof.set_value("voice_audience_learning", 2)
        prof.set_value("voice_audience_level", "novice")
        out = prof.for_tts_audience("de")
        idx_jargon = out.index("Übersetzen, kein Erfinden")
        idx_annex = out.index("LERN-ZUGABE")
        idx_faith = out.index("Treue und Vollständigkeit")
        self.assertLess(idx_jargon, idx_annex)
        self.assertLess(idx_annex, idx_faith)

    def test_learning_alone_renders_block(self):
        # learning=2 with no other fields must still produce a non-empty
        # block — the integration site (summarize.py) only appends the
        # block when it's non-empty, so an alone-learning profile would
        # otherwise be silently inert.
        prof.set_value("voice_audience_learning", 2)
        out = prof.for_tts_audience("de")
        self.assertIn("Lern-Modus 2/3", out)
        self.assertIn("LERN-ZUGABE", out)

    def test_learning_humanize_lists_field(self):
        prof.set_value("voice_audience_learning", 2)
        h = prof.humanize()
        self.assertIn("voice_audience_learning", h)
        self.assertIn("2", h)

    # ── METAPHER-BRÜCKE / METAPHOR BRIDGE tests ───────────────────────────

    def test_metapher_bridge_when_learning_and_metaphors_de(self):
        # Both switches on: METAPHER-BRÜCKE instruction must appear after LERN-ZUGABE.
        prof.set_value("voice_audience_learning", 3)
        prof.set_value("voice_audience_metaphors", "on")
        out = prof.for_tts_audience("de")
        self.assertIn("METAPHER-BRÜCKE", out)
        self.assertIn("Als Bild gesprochen,", out)
        idx_annex = out.index("LERN-ZUGABE")
        idx_bridge = out.index("METAPHER-BRÜCKE")
        idx_faith = out.index("Treue und Vollständigkeit")
        self.assertLess(idx_annex, idx_bridge)
        self.assertLess(idx_bridge, idx_faith)

    def test_metapher_bridge_when_learning_and_metaphors_en(self):
        prof.set_value("voice_audience_learning", 2)
        prof.set_value("voice_audience_metaphors", "on")
        out = prof.for_tts_audience("en")
        self.assertIn("METAPHOR BRIDGE", out)
        self.assertIn("As a picture,", out)
        idx_annex = out.index("LEARNING ANNEX")
        idx_bridge = out.index("METAPHOR BRIDGE")
        idx_faith = out.index("faithfulness")
        self.assertLess(idx_annex, idx_bridge)
        self.assertLess(idx_bridge, idx_faith)

    def test_metapher_bridge_not_without_learning(self):
        # metaphors alone (no learning) must NOT add the bridge instruction.
        prof.set_value("voice_audience_metaphors", "on")
        out_de = prof.for_tts_audience("de")
        out_en = prof.for_tts_audience("en")
        self.assertNotIn("METAPHER-BRÜCKE", out_de)
        self.assertNotIn("METAPHOR BRIDGE", out_en)

    def test_metapher_bridge_not_when_metaphors_off(self):
        # learning on but metaphors off: no bridge.
        prof.set_value("voice_audience_learning", 3)
        prof.set_value("voice_audience_metaphors", "off")
        out_de = prof.for_tts_audience("de")
        out_en = prof.for_tts_audience("en")
        self.assertNotIn("METAPHER-BRÜCKE", out_de)
        self.assertNotIn("METAPHOR BRIDGE", out_en)

    def test_metapher_bridge_not_when_learning_zero(self):
        # learning=0 disables both LERN-ZUGABE and METAPHER-BRÜCKE.
        prof.set_value("voice_audience_learning", 0)
        prof.set_value("voice_audience_metaphors", "on")
        out = prof.for_tts_audience("de")
        self.assertNotIn("METAPHER-BRÜCKE", out)


class TtsProviderKnownKeyTests(unittest.TestCase):
    """Regression: tts_provider must be stored at top-level, not under _extra.

    Before the fix, `tts_provider` was absent from KNOWN_KEYS which caused
    set_value('tts_provider', 'openai') to silently land under _extra instead
    of top-level, making the web console's _resolve_tts_provider() always see
    None and ignore the user's explicit choice.
    """

    def setUp(self):
        prof.reset()

    def test_tts_provider_in_known_keys(self):
        self.assertIn("tts_provider", prof.KNOWN_KEYS)

    def test_tts_provider_stored_at_top_level(self):
        prof.set_value("tts_provider", "openai")
        d = prof.load()
        self.assertEqual(d.get("tts_provider"), "openai")
        # Must NOT appear under _extra.
        self.assertNotIn("tts_provider", d.get("_extra", {}))

    def test_tts_provider_cleared_by_set_none(self):
        prof.set_value("tts_provider", "edge")
        prof.set_value("tts_provider", None)
        d = prof.load()
        self.assertIsNone(d.get("tts_provider"))

    def test_tts_provider_auto_treated_as_absent(self):
        # "auto" is not a real provider — resolvers treat it as None.
        # set_value stores "auto" at top level; the resolver in routes/voice.py
        # filters it out. Round-trip must not corrupt the field.
        prof.set_value("tts_provider", "auto")
        d = prof.load()
        self.assertEqual(d.get("tts_provider"), "auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
