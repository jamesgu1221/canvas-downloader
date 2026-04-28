import pytest

from canvas_dl.config import ConfigError, parse_course_ids


def test_parse_course_ids_rejects_non_numeric():
    with pytest.raises(ConfigError):
        parse_course_ids(["abc"], "--only-course")


def test_parse_course_ids_accepts_numbers():
    assert parse_course_ids(["1", "23"], "--skip-course") == [1, 23]
