
import pytest


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch, tmp_path):
    import utils as u
    u._local = __import__('threading').local()
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr('utils.DATA_DIR', str(data_dir))
    monkeypatch.setattr('utils.DB_FILE', str(data_dir / 'bot.db'))
    for cache_attr in ['_config_cache', '_ignore_cache', '_dm_whitelist_cache', '_aliases_cache']:
        monkeypatch.setattr(f'utils.{cache_attr}', None)
    for time_attr in ['_config_cache_time', '_ignore_cache_time', '_dm_whitelist_cache_time', '_aliases_cache_time']:
        monkeypatch.setattr(f'utils.{time_attr}', 0)
    monkeypatch.setattr('utils._migration_done', False)
    monkeypatch.setattr('utils._db_initialized', False)
    u.init_files()
    u._ensure_db()


class TestLoadSaveJson:
    def test_load_missing_returns_default(self):
        from utils import load_json
        assert load_json('data/nonexistent.json') == {}

    def test_save_and_load_roundtrip(self):
        from utils import load_json, save_json
        data = {'key': 'value', 'num': 42}
        save_json('data/test.json', data)
        assert load_json('data/test.json') == data

    def test_overwrite_existing(self):
        from utils import load_json, save_json
        save_json('data/test.json', {'a': 1})
        save_json('data/test.json', {'b': 2})
        assert load_json('data/test.json') == {'b': 2}

    def test_list_file_default(self):
        from utils import IGNORE_LIST_FILE, load_json
        assert load_json(IGNORE_LIST_FILE) == []


class TestConfigCache:
    def test_get_config_empty(self):
        from utils import get_config
        assert get_config() == {}

    def test_get_config_cached(self, monkeypatch):
        from utils import CONFIG_FILE, get_config, save_json
        save_json(CONFIG_FILE, {'guild_1': {'prefix': '!'}})
        first = get_config()
        assert first == {'guild_1': {'prefix': '!'}}

        save_json(CONFIG_FILE, {'guild_1': {'prefix': '?'}})
        cached = get_config()
        assert cached == {'guild_1': {'prefix': '!'}}

        from utils import invalidate_config
        invalidate_config()
        refreshed = get_config()
        assert refreshed == {'guild_1': {'prefix': '?'}}

    def test_get_embed_color_default(self):
        from utils import get_embed_color
        color = get_embed_color(999999)
        assert isinstance(color, int)


class TestGuildAvatars:
    def test_set_and_get(self):
        from utils import get_guild_avatar, set_guild_avatar
        set_guild_avatar('111', '222', 'https://example.com/av.png')
        assert get_guild_avatar('111', '222') == 'https://example.com/av.png'

    def test_get_missing_returns_none(self):
        from utils import get_guild_avatar
        assert get_guild_avatar('111', '999') is None

    def test_remove(self):
        from utils import get_guild_avatar, remove_guild_avatar, set_guild_avatar
        set_guild_avatar('111', '222', 'https://example.com/av.png')
        assert remove_guild_avatar('111', '222') is True
        assert get_guild_avatar('111', '222') is None
        assert remove_guild_avatar('111', '222') is False


class TestPlaylists:
    def test_save_and_load(self):
        from utils import load_playlist, save_playlist
        tracks = [{'title': 'song1', 'uri': 'https://youtu.be/abc'}]
        save_playlist('user1', 'myplaylist', tracks)
        assert load_playlist('user1', 'myplaylist') == tracks

    def test_load_missing(self):
        from utils import load_playlist
        assert load_playlist('user1', 'nonexistent') is None

    def test_list_playlists(self):
        from utils import list_playlists, save_playlist
        save_playlist('user1', 'a', [])
        save_playlist('user1', 'b', [])
        names = list_playlists('user1')
        assert 'a' in names
        assert 'b' in names

    def test_delete(self):
        from utils import delete_playlist, load_playlist, save_playlist
        save_playlist('user1', 'x', [{'title': 't'}])
        assert delete_playlist('user1', 'x') is True
        assert load_playlist('user1', 'x') is None


class TestLeveling:
    def test_add_xp_new_user(self):
        from utils import add_xp
        result = add_xp('user1', 'guild1', xp_amount=100)
        assert result['xp'] == 100
        assert result['level'] > 0

    def test_add_xp_accumulates(self):
        from utils import add_xp, get_user_xp
        add_xp('user1', 'guild1', xp_amount=50)
        add_xp('user1', 'guild1', xp_amount=50)
        data = get_user_xp('user1', 'guild1')
        assert data['xp'] == 100

    def test_get_user_xp_none(self):
        from utils import get_user_xp
        assert get_user_xp('nonexistent', 'guild1') is None

    def test_leaderboard(self):
        from utils import add_xp, get_leaderboard
        add_xp('user_a', 'guild1', xp_amount=200)
        add_xp('user_b', 'guild1', xp_amount=100)
        lb = get_leaderboard('guild1', limit=10)
        assert len(lb) >= 2
        assert lb[0]['user_id'] == 'user_a'

    def test_add_voice_xp(self):
        from utils import add_voice_xp, get_user_xp
        add_voice_xp('user1', 'guild1', minutes=10)
        data = get_user_xp('user1', 'guild1')
        assert data['voice_minutes'] == 10
        assert data['xp'] == 50


class TestLevelRoles:
    def test_set_and_get(self):
        from utils import get_level_role, set_level_role
        set_level_role('guild1', 5, 'role123')
        assert get_level_role('guild1', 5) == 'role123'

    def test_get_missing(self):
        from utils import get_level_role
        assert get_level_role('guild1', 99) is None

    def test_get_all(self):
        from utils import get_all_level_roles, set_level_role
        set_level_role('guild1', 1, 'r1')
        set_level_role('guild1', 5, 'r5')
        roles = get_all_level_roles('guild1')
        assert roles == {1: 'r1', 5: 'r5'}


class TestCmdChannelRules:
    def test_set_and_get(self):
        from utils import get_cmd_channel_rule, set_cmd_channel_rule
        set_cmd_channel_rule('guild1', 'music', 'allow', ['111', '222'])
        rule = get_cmd_channel_rule('guild1', 'music')
        assert rule['mode'] == 'allow'
        assert '111' in rule['channels']

    def test_clear(self):
        from utils import clear_cmd_channel_rule, get_cmd_channel_rule, set_cmd_channel_rule
        set_cmd_channel_rule('guild1', 'music', 'deny', ['333'])
        clear_cmd_channel_rule('guild1', 'music')
        assert get_cmd_channel_rule('guild1', 'music') is None

    def test_invalid_mode(self):
        from utils import set_cmd_channel_rule
        with pytest.raises(ValueError):
            set_cmd_channel_rule('guild1', 'x', 'invalid', [])


class TestGifCooldown:
    def test_cooldown_first_call_returns_none(self):
        from utils import check_gif_cooldown
        result = check_gif_cooldown(999)
        assert result is None

    def test_cooldown_second_call_returns_number(self):
        from utils import check_gif_cooldown
        check_gif_cooldown(998)
        result = check_gif_cooldown(998)
        assert isinstance(result, int | float) or result == 'silent'


class TestHelpMeta:
    def test_help_meta_decorator(self):
        from utils import get_help_meta, help_meta

        @help_meta(section='Test', usage='.test', desc='A test command')
        async def my_cmd(ctx):
            pass

        meta = get_help_meta(my_cmd)
        assert meta['section'] == 'Test'
        assert meta['usage'] == '.test'
        assert meta['desc'] == 'A test command'
        assert meta['examples'] == []

    def test_help_meta_with_examples(self):
        from utils import get_help_meta, help_meta

        @help_meta(section='Fun', usage='.ping', desc='Pong!', examples=['.ping'])
        async def ping_cmd(ctx):
            pass

        meta = get_help_meta(ping_cmd)
        assert meta['examples'] == ['.ping']
