"""live_monitor_core / live_monitor_notify 中纯函数的单测"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_monitor_core import sanitize_filename, resolve_output_path
from live_monitor_notify import _duration_str


class TestSanitizeFilename:
    def test_normal_name(self):
        assert sanitize_filename("小明直播间") == "小明直播间"

    def test_special_chars_stripped(self):
        assert sanitize_filename('a<b>c:d"e/f\\g') == "abcdefg"

    def test_spaces_to_underscore(self):
        assert sanitize_filename("hello world") == "hello_world"

    def test_empty_falls_back_to_unknown(self):
        assert sanitize_filename("") == "unknown"

    def test_all_invalid_chars_falls_back_to_unknown(self):
        assert sanitize_filename("///") == "unknown"

    def test_strips_leading_trailing_dots_underscores(self):
        assert sanitize_filename("_.name.__") == "name"

    def test_control_chars_stripped(self):
        assert sanitize_filename("a\nb\tc\rd") == "abcd"


class TestResolveOutputPath:
    def test_multi_room_always_treated_as_dir(self, tmp_path):
        result = resolve_output_path(str(tmp_path), 123, multi_room=True)
        assert result == os.path.join(str(tmp_path), "live_123.csv")

    def test_trailing_slash_treated_as_dir(self, tmp_path):
        result = resolve_output_path(str(tmp_path) + os.sep, 456, multi_room=False)
        assert result.endswith("live_456.csv")

    def test_existing_dir_path_treated_as_dir(self, tmp_path):
        result = resolve_output_path(str(tmp_path), 999, multi_room=False)
        assert result == os.path.join(str(tmp_path), "live_999.csv")

    def test_explicit_file_path_kept_as_is(self):
        result = resolve_output_path("output.csv", 789, multi_room=False)
        assert result == "output.csv"

    def test_creates_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "not_yet_created"
        result = resolve_output_path(str(new_dir), 1, multi_room=True)
        assert os.path.isdir(str(new_dir))
        assert result == os.path.join(str(new_dir), "live_1.csv")


class TestDurationStr:
    @pytest.mark.parametrize("seconds,expected", [
        (30, "0分30秒"),
        (90, "1分30秒"),
        (3661, "1时1分1秒"),
        (0, "0分0秒"),
    ])
    def test_formats(self, seconds, expected):
        assert _duration_str(seconds) == expected
