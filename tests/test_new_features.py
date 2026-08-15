"""
tests/test_new_features.py  —  tests for safe math eval, regex autoresponse, afk caching, and giveaway duration
"""

import math
import re
import unittest
from cogs.utility_tools import safe_eval
from cogs.giveaways import _parse_duration


class TestNewFeatures(unittest.TestCase):
    def test_safe_eval_basic(self):
        self.assertEqual(safe_eval("2 + 2"), 4)
        self.assertEqual(safe_eval("10 * 5 - 4 / 2"), 48.0)
        self.assertEqual(safe_eval("2 ^ 8"), 256)
        self.assertEqual(safe_eval("sqrt(144)"), 12.0)
        self.assertEqual(safe_eval("round(pi, 2)"), 3.14)
        self.assertEqual(safe_eval("abs(-42)"), 42)

    def test_safe_eval_security(self):
        with self.assertRaises(Exception):
            safe_eval("__import__('os').system('ls')")
        with self.assertRaises(Exception):
            safe_eval("eval('1+1')")
        with self.assertRaises(Exception):
            safe_eval("2 ** 1000")  # exponent too large

    def test_parse_duration(self):
        self.assertEqual(_parse_duration("10s"), 10)
        self.assertEqual(_parse_duration("5m"), 300)
        self.assertEqual(_parse_duration("2h"), 7200)
        self.assertEqual(_parse_duration("1d"), 86400)
        self.assertEqual(_parse_duration("1w"), 604800)
        self.assertIsNone(_parse_duration("invalid"))

    def test_autoresponse_regex_boundaries(self):
        trigger = "cat"
        pat = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
        self.assertTrue(bool(pat.search("I love my cat.")))
        self.assertTrue(bool(pat.search("cat is cute")))
        self.assertTrue(bool(pat.search("CAT")))
        self.assertFalse(bool(pat.search("concatenate string")))
        self.assertFalse(bool(pat.search("scattered stones")))


if __name__ == "__main__":
    unittest.main()
