from types import SimpleNamespace

import pytest


def test_youtube_clients_failed_error_is_retryable():
    from cogs.music import _is_retryable_playback_error

    error = """
    dev.lavalink.youtube.AllClientsFailedException: All clients failed to load the item.
    Client [TVHTML5_SIMPLY] failed: Sign in to confirm you're not a bot
    Client [TVHTML5] failed: Read timed out
    Client [ANDROID_VR] failed: This video requires login.
    """

    assert _is_retryable_playback_error(error)


def test_unrelated_playback_error_is_not_retryable():
    from cogs.music import _is_retryable_playback_error

    assert not _is_retryable_playback_error("track does not exist")


def test_load_failed_track_end_reason_is_failed_end():
    from cogs.music import _is_failed_track_end_reason

    assert _is_failed_track_end_reason("loadFailed")


def test_finished_track_end_reason_is_not_failed_end():
    from cogs.music import _is_failed_track_end_reason

    assert not _is_failed_track_end_reason("finished")


@pytest.mark.asyncio
async def test_retryable_track_exception_is_marked_for_retry_without_skip_message():
    from cogs.music import Music, _track_retry_key

    sent_messages = []

    class Home:
        async def send(self, *args, **kwargs):
            sent_messages.append((args, kwargs))

    track = SimpleNamespace(
        title="EVA (lonely, lonely, I guess I'm lonely)",
        author="Vintage",
        uri="https://www.youtube.com/watch?v=test",
        identifier="test",
    )
    player = SimpleNamespace(guild=SimpleNamespace(id=123), home=Home())
    payload = SimpleNamespace(
        player=player,
        track=track,
        exception={"causeStackTrace": "AllClientsFailedException: Read timed out"},
    )

    cog = Music(SimpleNamespace())

    await cog.on_wavelink_track_exception(payload)

    assert cog._pending_playback_retries[123] == _track_retry_key(track)
    assert sent_messages == []
