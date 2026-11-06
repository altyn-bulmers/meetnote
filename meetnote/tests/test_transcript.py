from meetnote.transcript import as_text, mark_echo


def line(src, w0, w1, text):
    return {"type": "line", "src": src, "w0": w0, "w1": w1, "text": text}


def test_speaker_echo_on_the_mic_is_flagged():
    items = mark_echo([
        line("others", 10.0, 14.0, "Let's move the release to Friday."),
        line("me", 10.2, 14.1, "let's move the release to friday"),  # the mic heard the speakers
        line("me", 15.0, 17.0, "Fine by me."),
    ])
    assert [r["echo"] for r in items] == [False, True, False]


def test_same_words_at_another_time_are_not_echo():
    items = mark_echo([
        line("others", 10.0, 12.0, "Sounds good."),
        line("me", 40.0, 42.0, "Sounds good."),
    ])
    assert items[1]["echo"] is False


def test_text_for_claude_leaves_out_echo_and_old_lines():
    items = mark_echo([
        line("others", 100.0, 103.0, "First point."),
        line("others", 200.0, 204.0, "Second point is the budget."),
        line("me", 200.1, 204.0, "Second point is the budget."),
        line("me", 205.0, 207.0, "Agreed."),
    ])
    text = as_text(items, since=150.0)
    assert "First point" not in text
    assert text.count("Second point") == 1
    assert text.splitlines()[-1].endswith("Me: Agreed.")
