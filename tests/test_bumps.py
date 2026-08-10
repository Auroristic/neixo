from cogs.bumps import _extract_bumper


class _FakeUser:
    def __init__(self, uid, bot=False):
        self.id = uid
        self.bot = bot


def test_extract_bumper_skips_bots_in_mentions():
    mentions = [_FakeUser(111, bot=True), _FakeUser(222), _FakeUser(333)]
    assert _extract_bumper("Bump done! :tada:", mentions) == 222


def test_extract_bumper_none_when_only_bots():
    assert _extract_bumper("Bump done! :tada:", [_FakeUser(111, bot=True)]) is None


def test_extract_bumper_empty_mentions():
    assert _extract_bumper("Bump done! :tada:", []) is None


def test_extract_bumper_from_raw_content():
    assert (
        _extract_bumper("thank you <@887382911924441139>!", [])
        == 887382911924441139
    )
    assert (
        _extract_bumper("thank you <@!887382911924441139>!", [])
        == 887382911924441139
    )


def test_extract_bumper_ignores_role_mentions():
    assert _extract_bumper("hey <@&123456789>", []) is None


def test_extract_bumper_mentions_beat_content():
    mentions = [_FakeUser(999)]
    assert _extract_bumper("thank you <@887382911924441139>!", mentions) == 999
