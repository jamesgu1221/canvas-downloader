from canvas_dl.traversal import sanitize_name


def test_sanitize_windows_reserved_names():
    assert sanitize_name("CON.txt") == "CON_.txt"
    assert sanitize_name("NUL") == "NUL_"


def test_sanitize_illegal_characters():
    assert sanitize_name('a:b?.txt') == "a_b_.txt"
