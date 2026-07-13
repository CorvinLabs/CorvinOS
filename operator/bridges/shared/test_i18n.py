#!/usr/bin/env python3
"""Per-subtask E2E for the i18n module.

Covers:
  * BCP-47 normalisation (alias folding, casing, region tags, malformed)
  * Native-name lookup for the registry + unknown codes
  * Resolution chain through multiple candidate tiers
  * `language_directive()` shape — voice vs general audience flavour
  * UI-string bundles (`t()`) — exact locale, base-locale fallback,
    English fallback, missing-key behaviour, format substitution
  * Round-trip via the `lang_cli.py` CLI: set + show + clear + list
  * Cross-module: profile.display_language → summarize.py prompt shape

The suite is deliberately self-contained — it sandboxes the user
profile via `XDG_CONFIG_HOME` so it can run in parallel with other
test files without contaminating the user's real `~/.config/corvin-voice/`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


HERE = Path(__file__).resolve().parent
SHARED = HERE
SCRIPTS = HERE.parent.parent / "voice" / "scripts"
LANG_CLI = SCRIPTS / "lang_cli.py"

sys.path.insert(0, str(SHARED))
import i18n  # noqa: E402


class NormaliseTests(unittest.TestCase):
    def test_canonical_unchanged(self):
        for code in ("de", "en", "fr", "ja", "ko", "ar"):
            self.assertEqual(i18n.normalise(code), code)

    def test_script_subtag_titlecase(self):
        self.assertEqual(i18n.normalise("zh-hans"), "zh-Hans")
        self.assertEqual(i18n.normalise("zh-HANS"), "zh-Hans")
        self.assertEqual(i18n.normalise("zh-Hant"), "zh-Hant")

    def test_region_subtag_uppercase(self):
        self.assertEqual(i18n.normalise("pt-br"), "pt-BR")
        self.assertEqual(i18n.normalise("en_us"), "en-US")
        # zh-tw normalises mechanically (no alias collision since the
        # alias `tw` only matches the bare two-letter form). The LLM
        # understands `zh-TW` as Traditional Chinese either way.
        self.assertEqual(i18n.normalise("zh-tw"), "zh-TW")

    def test_alias_folding(self):
        self.assertEqual(i18n.normalise("German"),     "de")
        self.assertEqual(i18n.normalise("deutsch"),    "de")
        self.assertEqual(i18n.normalise("chinese"),    "zh-Hans")
        self.assertEqual(i18n.normalise("Mandarin"),   "zh-Hans")
        self.assertEqual(i18n.normalise("japanese"),   "ja")
        self.assertEqual(i18n.normalise("Französisch"), "fr")
        self.assertEqual(i18n.normalise("ZH"),          "zh-Hans")

    def test_unknown_well_formed_passes_through(self):
        # Made-up but BCP-47 shaped: 'xx-YY' parses fine.
        self.assertEqual(i18n.normalise("xx-yy"), "xx-YY")

    def test_malformed_returns_empty(self):
        for bad in (None, "", "  ", "12", "x", "this is not a code",
                    "xyzzy", "$$$"):
            self.assertEqual(i18n.normalise(bad), "")

    def test_strip_whitespace(self):
        self.assertEqual(i18n.normalise("  zh-Hans  "), "zh-Hans")

    def test_base_lang(self):
        self.assertEqual(i18n.base_lang("zh-Hans"), "zh")
        self.assertEqual(i18n.base_lang("pt-BR"), "pt")
        self.assertEqual(i18n.base_lang("de"), "de")
        self.assertEqual(i18n.base_lang(""), "")


class NativeNameTests(unittest.TestCase):
    def test_known_codes_have_native_name(self):
        # Every code in known_codes() must produce a non-empty,
        # non-fallback native name.
        for code in i18n.known_codes():
            name = i18n.native_name(code)
            self.assertTrue(name)
            self.assertNotIn("language tag", name,
                             f"{code} fell into fallback name path")

    def test_unknown_well_formed_gets_descriptive_fallback(self):
        # A made-up but well-formed code should produce a clear synthetic
        # name, not silently default to English.
        out = i18n.native_name("xx-YY")
        self.assertIn("xx-YY", out)

    def test_zh_variants_distinguishable(self):
        hans = i18n.native_name("zh-Hans")
        hant = i18n.native_name("zh-Hant")
        self.assertNotEqual(hans, hant)
        self.assertIn("Simplified", hans)
        self.assertIn("Traditional", hant)

    def test_base_fallback_for_unknown_region(self):
        # de-AT isn't in the registry but `de` is — should mention German.
        out = i18n.native_name("de-AT")
        self.assertIn("German", out)


class ResolveTests(unittest.TestCase):
    def test_first_match_wins(self):
        self.assertEqual(i18n.resolve("zh-Hans", "de", "en"), "zh-Hans")

    def test_skip_empty_and_invalid(self):
        self.assertEqual(i18n.resolve(None, "", "xyzzy", "ja", "en"), "ja")

    def test_default_used_when_nothing_resolves(self):
        self.assertEqual(i18n.resolve(None, "", "xyzzy", default="en"), "en")
        self.assertEqual(i18n.resolve(None, default="ja"), "ja")

    def test_alias_in_chain(self):
        self.assertEqual(i18n.resolve("German"), "de")


class DirectiveTests(unittest.TestCase):
    def test_general_directive_shape(self):
        d = i18n.language_directive("zh-Hans")
        self.assertIn("OUTPUT LANGUAGE OVERRIDE", d)
        self.assertIn("Simplified Chinese", d)
        # Native-name registry encodes the endonym in the same string,
        # so either the BCP-47 tag or the CJK characters must show up.
        self.assertTrue("zh-Hans" in d or "简体中文" in d,
                        f"native discriminator missing: {d!r}")
        self.assertIn("final pin", d.lower())
        # Must explicitly defuse the user-global "always reply in X" rule.
        self.assertIn("CLAUDE.md", d)
        self.assertIn("user-global", d)

    def test_voice_audience_adds_token_clause(self):
        general = i18n.language_directive("zh-Hans")
        voice = i18n.language_directive("zh-Hans", audience="voice")
        self.assertGreater(len(voice), len(general))
        self.assertIn("Code identifiers", voice)
        self.assertIn("CLI flags", voice)
        self.assertNotIn("Code identifiers", general)

    def test_unknown_locale_still_emits_directive(self):
        # We never want a silent missing-language path. An unknown code
        # falls through to the synthetic name, but the directive itself
        # must still be coherent.
        d = i18n.language_directive("xx-YY")
        self.assertIn("OUTPUT LANGUAGE", d)
        self.assertIn("xx-YY", d)

    def test_alias_normalised_in_directive(self):
        d = i18n.language_directive("Chinese", audience="voice")
        self.assertIn("Simplified Chinese", d)


class BundleTests(unittest.TestCase):
    def setUp(self):
        i18n._BUNDLE_CACHE.clear()

    def test_de_bundle_has_lang_keys(self):
        b = i18n._load_bundle("de")
        self.assertIn("lang", b)
        self.assertIn("set_ok", b["lang"])

    def test_t_de_returns_german(self):
        out = i18n.t("lang.set_ok", "de", name="X", code="zh")
        self.assertIn("Sprache", out)

    def test_t_en_returns_english(self):
        out = i18n.t("lang.set_ok", "en", name="X", code="zh")
        self.assertIn("Language", out)
        self.assertIn("X", out)

    def test_unknown_lang_falls_back_to_english(self):
        out = i18n.t("lang.set_ok", "fr", name="Y", code="zh")
        self.assertIn("Language", out)
        self.assertIn("Y", out)

    def test_base_locale_fallback(self):
        # pt-BR has no bundle; should fall through pt → en. We don't
        # ship a pt bundle today, so it must end at English.
        out = i18n.t("lang.set_ok", "pt-BR", name="Z", code="ja")
        self.assertIn("Language", out)

    def test_missing_key_returns_literal(self):
        out = i18n.t("does.not.exist", "de")
        self.assertEqual(out, "does.not.exist")

    def test_format_keys_preserved_on_failure(self):
        # A key that exists but takes args we don't supply should still
        # return the unformatted string (graceful degradation).
        out = i18n.t("lang.set_ok", "en")  # no name/code
        self.assertIn("{", out)  # unformatted

    def test_available_bundles_lists_at_least_de_en(self):
        avail = list(i18n.available_bundles())
        self.assertIn("de", avail)
        self.assertIn("en", avail)


class LangCliTests(unittest.TestCase):
    """Round-trip through the slash-command backend."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lang-cli-test-"))
        self.env = os.environ.copy()
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        r = subprocess.run(
            ["python3", str(LANG_CLI), *args],
            capture_output=True, text=True, env=self.env,
        )
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            payload = {}
        return r.returncode, payload, r.stderr

    def test_show_unset(self):
        rc, j, _ = self._run("show")
        self.assertEqual(rc, 0)
        self.assertTrue(j["ok"])
        self.assertFalse(j["set"])
        self.assertEqual(j["code"], "en")

    def test_set_zh_round_trip(self):
        rc, j, _ = self._run("set", "zh-Hans")
        self.assertEqual(rc, 0)
        self.assertTrue(j["ok"])
        self.assertEqual(j["code"], "zh-Hans")

        rc, j, _ = self._run("show")
        self.assertEqual(rc, 0)
        self.assertTrue(j["set"])
        self.assertEqual(j["code"], "zh-Hans")
        self.assertIn("Simplified Chinese", j["name"])

    def test_set_alias_normalised(self):
        rc, j, _ = self._run("set", "japanese")
        self.assertEqual(rc, 0)
        self.assertEqual(j["code"], "ja")

    def test_set_unknown_rejected(self):
        rc, j, _ = self._run("set", "xyzzy")
        self.assertEqual(rc, 1)
        self.assertFalse(j["ok"])
        self.assertEqual(j["reason"], "unknown")

    def test_clear(self):
        self._run("set", "ja")
        rc, j, _ = self._run("clear")
        self.assertEqual(rc, 0)
        self.assertTrue(j["ok"])
        rc, j, _ = self._run("show")
        self.assertFalse(j["set"])

    def test_list_includes_zh_ja_ar(self):
        rc, j, _ = self._run("list")
        self.assertEqual(rc, 0)
        codes = {item["code"] for item in j["codes"]}
        for c in ("de", "en", "zh-Hans", "zh-Hant", "ja", "ar", "fr", "es"):
            self.assertIn(c, codes)


class SummarizePromptShapeTests(unittest.TestCase):
    """summarize.py prompt-shape per locale (no live LLM)."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        # Re-import so the class-level cache picks up the latest module.
        if "summarize" in sys.modules:
            del sys.modules["summarize"]
        import summarize as s
        self.s = s

    def test_legacy_de_no_directive(self):
        p = self.s._system_for("de", 400, has_task=False)
        self.assertNotIn("OUTPUT LANGUAGE", p)

    def test_legacy_en_no_directive(self):
        p = self.s._system_for("en", 400, has_task=False)
        self.assertNotIn("OUTPUT LANGUAGE", p)

    def test_zh_hans_directive_sandwiched(self):
        p = self.s._system_for("en", 400, has_task=False, output_language="zh-Hans")
        # Sandwich: directive both at the FRONT (frames the whole
        # output as a translated TTS turn) and at the BACK (most-recent
        # instruction wins).
        self.assertIn("Simplified Chinese", p)
        first = p.find("OUTPUT LANGUAGE OVERRIDE")
        last = p.rfind("OUTPUT LANGUAGE OVERRIDE")
        self.assertGreater(last, first, "directive must appear twice (sandwich)")
        # Front directive comes before the base prompt (system prompts
        # typically open with FOCUS/AUFBAU markers from the SYSTEM table).
        idx_focus = max(p.find("FOCUS"), p.find("FOKUS"))
        self.assertLess(first, idx_focus)
        # Back directive comes AFTER the SELF-CHECK block.
        idx_self = p.find("SELF-CHECK")
        self.assertLess(idx_self, last)

    def test_japanese_directive(self):
        p = self.s._system_for("en", 400, has_task=False, output_language="ja")
        self.assertIn("Japanese", p)

    def test_arabic_directive(self):
        p = self.s._system_for("en", 400, has_task=False, output_language="ar")
        self.assertIn("Arabic", p)

    def test_de_passthrough_when_output_de(self):
        # Explicit `de` matches base prompt → no extra directive (token-cost optimisation).
        p = self.s._system_for("de", 400, has_task=False, output_language="de")
        self.assertNotIn("OUTPUT LANGUAGE", p)

    def test_cross_lang_de_base_zh_output(self):
        # Base prompt is German but user wants Chinese output — directive
        # must still fire so the LLM honours zh-Hans as the OUTPUT.
        p = self.s._system_for("de", 400, has_task=False, output_language="zh-Hans")
        self.assertIn("OUTPUT LANGUAGE", p)
        self.assertIn("Simplified Chinese", p)

    def test_unknown_locale_silent_no_op(self):
        p = self.s._system_for("en", 400, has_task=False, output_language="xyzzy")
        self.assertNotIn("OUTPUT LANGUAGE", p)

    def test_persona_audience_directive_layer_order(self):
        # Order with directive sandwich:
        #   OUTPUT LANGUAGE → base → persona → audience → SELF-CHECK
        #     → OUTPUT LANGUAGE
        # That is, the inner block (persona / audience / self-check)
        # stays in the same relative order as before; the directive
        # only adds a front+back wrapping.
        p = self.s._system_for("en", 400, has_task=False,
                                persona="coder",
                                audience="AUDIENCE — test stub",
                                output_language="zh-Hans")
        idx_persona  = p.find("Persona style")
        idx_audience = p.find("AUDIENCE — test stub")
        idx_self     = p.find("SELF-CHECK")
        first_lang   = p.find("OUTPUT LANGUAGE OVERRIDE")
        last_lang    = p.rfind("OUTPUT LANGUAGE OVERRIDE")
        self.assertLess(first_lang, idx_persona)
        self.assertLess(idx_persona, idx_audience)
        self.assertLess(idx_audience, idx_self)
        self.assertLess(idx_self, last_lang)
        self.assertNotEqual(first_lang, last_lang)


class ProfileAudienceTests(unittest.TestCase):
    """profile.for_tts_audience BCP-47 dispatch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="profile-i18n-test-"))
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp)
        # Reload profile to re-resolve PROFILE_FILE.
        if "profile" in sys.modules:
            del sys.modules["profile"]
        sys.path.insert(0, str(SHARED))
        import profile as p
        self.p = p
        # Force the file path to honour the new XDG_CONFIG_HOME.
        self.p.PROFILE_FILE = self.p._profile_path()
        self.p._cache = None
        self.p._cache_mtime = 0.0
        self.p.set_value("voice_audience_level", "novice")
        self.p.set_value("voice_audience_jargon", 1)
        self.p.set_value("voice_audience_metaphors", "on")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_de_renders_german(self):
        out = self.p.for_tts_audience("de")
        self.assertIn("HÖRER-PROFIL", out)
        self.assertIn("Anfänger", out)

    def test_en_renders_english(self):
        out = self.p.for_tts_audience("en")
        self.assertIn("AUDIENCE", out)
        self.assertIn("novice", out)

    def test_zh_hans_falls_back_to_english_block(self):
        # The audience block stays in English (LLM-pivot); the
        # OUTPUT-LANGUAGE directive separately steers actual output.
        out = self.p.for_tts_audience("zh-Hans")
        self.assertIn("AUDIENCE", out)

    def test_japanese_falls_back_to_english_block(self):
        out = self.p.for_tts_audience("ja")
        self.assertIn("AUDIENCE", out)

    def test_arabic_falls_back_to_english_block(self):
        out = self.p.for_tts_audience("ar")
        self.assertIn("AUDIENCE", out)

    def test_de_at_dialect_keeps_german_block(self):
        # Regional German dialect → still German block (base-locale rule).
        out = self.p.for_tts_audience("de-AT")
        self.assertIn("HÖRER-PROFIL", out)


class SystemLanguageTests(unittest.TestCase):
    """`system_language()` — the OS-locale defence-in-depth tier that keeps the
    console welcome (was hard 'en') and the bridge TTS (was hard 'de') agreeing
    on the user's ACTUAL language when display_language was never seeded."""

    def test_posix_lang_env(self):
        with unittest.mock.patch.dict(
            os.environ, {"LANG": "de_DE.UTF-8", "LC_ALL": "", "LANGUAGE": ""}, clear=False
        ):
            self.assertEqual(i18n.system_language(), "de-DE")

    def test_lc_all_takes_precedence_over_lang(self):
        with unittest.mock.patch.dict(
            os.environ, {"LC_ALL": "fr_FR.UTF-8", "LANG": "de_DE.UTF-8"}, clear=False
        ):
            self.assertEqual(i18n.system_language(), "fr-FR")

    def test_c_and_posix_locale_ignored(self):
        with unittest.mock.patch.dict(
            os.environ, {"LC_ALL": "C", "LANG": "POSIX", "LANGUAGE": ""}, clear=False
        ):
            if sys.platform != "win32":
                self.assertEqual(i18n.system_language(), "")

    def test_unset_returns_empty_non_windows(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            if sys.platform != "win32":
                self.assertEqual(i18n.system_language(), "")

    def test_resolve_chain_prefers_profile_then_system_then_default(self):
        # explicit profile pin wins over the system-locale tier
        self.assertEqual(i18n.resolve("es", "de-DE", default="en"), "es")
        # unseeded profile → system-locale tier is used
        self.assertEqual(i18n.resolve("", "de-DE", default="en"), "de-DE")
        # nothing known → hard default
        self.assertEqual(i18n.resolve("", "", default="en"), "en")


if __name__ == "__main__":
    unittest.main(verbosity=2)
