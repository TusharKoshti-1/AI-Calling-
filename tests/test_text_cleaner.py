"""Exercises the clean_reply pipeline — tag extraction, markdown scrub,
and the fixed word-boundary end-phrase detection."""
from __future__ import annotations

from app.services.text_cleaner import clean_reply


class TestCleanReply:
    def test_strips_hot_lead_and_end_call_tags(self):
        r = clean_reply("[HOT_LEAD] [END_CALL] Great, we'll call you back!")
        assert r.hot_lead is True
        assert r.end_call is True
        assert "[HOT_LEAD]" not in r.text
        assert "[END_CALL]" not in r.text

    def test_hot_lead_forces_end_call(self):
        r = clean_reply("[HOT_LEAD] Visiting is wonderful.")
        assert r.hot_lead is True
        assert r.end_call is True

    def test_plain_reply_is_not_end_call(self):
        r = clean_reply("What is your budget range?")
        assert not r.end_call
        assert not r.hot_lead
        assert r.text == "What is your budget range?"

    def test_strips_markdown_and_emoji(self):
        r = clean_reply("**Hello** *world* 🎉 # heading\n\ntest")
        assert "*" not in r.text
        assert "#" not in r.text
        assert "🎉" not in r.text
        assert "Hello" in r.text and "world" in r.text and "test" in r.text

    def test_strips_think_block(self):
        r = clean_reply("<think>inner monologue</think>Thanks for your time!")
        assert "think" not in r.text.lower()

    def test_detects_end_phrase_when_tag_missing(self):
        r = clean_reply("Thanks, have a great day!")
        assert r.end_call is True

    def test_word_boundary_prevents_substring_false_positive(self):
        """Regression: legacy code matched 'take care' inside 'take careful'."""
        # 'careful' contains 'care' — with substring matching, 'take care'
        # would wrongly match. With word-boundaries it must not.
        r = clean_reply(
            "Please be careful when evaluating your options — "
            "what budget are you thinking?"
        )
        assert r.end_call is False, (
            "Substring 'take care' should NOT match inside 'be careful'."
        )

    def test_genuine_take_care_still_triggers(self):
        r = clean_reply("Take care, speak soon.")
        assert r.end_call is True

    def test_ampersand_replaced(self):
        r = clean_reply("Rock & roll.")
        assert "&" not in r.text
        assert "and" in r.text

    def test_empty_input_returns_safe_default(self):
        r = clean_reply("")
        assert r.text
        assert isinstance(r.end_call, bool)
