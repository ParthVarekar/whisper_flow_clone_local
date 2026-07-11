from whisper_flow.formatting import apply_smart_formatting


def test_replaces_spoken_punctuation_and_newlines():
    text = "hello comma world new paragraph next line thanks period"
    out = apply_smart_formatting(text)
    # Capitalization now correctly capitalizes sentence/paragraph starts
    assert out == "Hello, world\n\nThanks."


def test_press_enter_appends_newline():
    out = apply_smart_formatting("please send this press enter")
    assert out.endswith("\n")
    assert "press enter" not in out.lower()


def test_formal_style_adds_terminal_period():
    out = apply_smart_formatting("this is a note", writing_style="formal")
    assert out == "This is a note."


def test_casual_style_drops_terminal_period():
    out = apply_smart_formatting("This is a note.", writing_style="casual")
    assert out == "This is a note"
