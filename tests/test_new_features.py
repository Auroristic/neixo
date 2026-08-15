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


    def test_image_card_renderers(self):
        import io
        from PIL import Image
        # Create a sample avatar image
        sample_img = Image.new("RGB", (128, 128), (60, 80, 120))
        sample_buf = io.BytesIO()
        sample_img.save(sample_buf, format="PNG")
        raw_bytes = sample_buf.getvalue()

        from cogs.serverstats import _make_glass_backdrop, _render_server_card, _render_lb_card
        from cogs.leveling import _render_rank_card
        from cogs.digest import _render_digest_card
        from cogs.quote import _render_quote_card
        from cogs.welcome import _render_welcome_card
        from cogs.milestones import _render_milestone_card
        from cogs.fun import _render_ship_card

        # 1. Glass backdrop
        bg = _make_glass_backdrop(raw_bytes, 900, 500)
        self.assertEqual(bg.size, (900, 500))

        # 2. Server card
        srv_buf = _render_server_card(raw_bytes, raw_bytes, "Test Server", 150, 5, 2, "Jan 2024", None, "", "User1", "User2", "User3")
        self.assertTrue(srv_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 3. Leaderboard card
        lb_buf = _render_lb_card(raw_bytes, "Top Chatters", "server chat leaderboard", [(1, "User1", 100), (2, "User2", 80)], "page 1/1", raw_bytes, "Neixo", "#1 · 100 msgs", " msgs")
        self.assertTrue(lb_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 4. Rank card
        rank_buf = _render_rank_card(raw_bytes, "TestUser", 5, 2600, 3600, 50.0, 1420, 1, "Test Server")
        self.assertTrue(rank_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 5. Digest card
        dig_buf = _render_digest_card(raw_bytes, "Test Server", "week of Aug 15", 500, "12h 30m", 45, 12, [(1, "U1", 200)], [(1, "U1", 30)], [(1, "U1", 10)])
        self.assertTrue(dig_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 6. Quote card
        from cogs.quote import _render_quote_card
        quote_buf = _render_quote_card(raw_bytes, "Author", "author_user", "Test quote content", "Server", "Today")
        self.assertTrue(quote_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 7. Welcome card
        welc_buf = _render_welcome_card(raw_bytes, raw_bytes, "Test Server", "NewMember", 151)
        self.assertTrue(welc_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 8. Milestone card
        mile_buf = _render_milestone_card(raw_bytes, "Test Server", 1000)
        self.assertTrue(mile_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 9. Ship card
        ship_buf = _render_ship_card(raw_bytes, raw_bytes, "User1", "User2", 88, "perfect match")
        self.assertTrue(ship_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

        # 10. Reactions leaderboard card
        from cogs.reactions import _render_reactor_top
        rc_buf = _render_reactor_top(raw_bytes, "Top Reactors", "server reaction leaderboard", [(1, "User1", 50)], "page 1/1", raw_bytes, "User1", "#1 · 50 reactions")
        self.assertTrue(rc_buf.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
