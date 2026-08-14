
import importlib

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


class TestCreatorId:
    def test_is_creator_uses_env_creator_id(self, monkeypatch):
        import utils

        monkeypatch.setattr(utils, 'CREATOR_ID', 987654321)

        assert utils.is_creator(987654321) is True
        assert utils.is_creator('987654321') is True
        assert utils.is_creator(123456789) is False

    def test_default_creator_id_is_not_real_user_id(self, monkeypatch):
        import utils

        monkeypatch.delenv('CREATOR_ID', raising=False)
        reloaded = importlib.reload(utils)

        try:
            assert reloaded.CREATOR_ID == 0
        finally:
            monkeypatch.setenv('CREATOR_ID', '123456789')
            importlib.reload(reloaded)


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


class TestBaitConfig:
    def test_parse_bait_delay_accepts_minutes_hours_days(self):
        from utils import parse_bait_delay

        assert parse_bait_delay('30m') == 1800
        assert parse_bait_delay('12h') == 43200
        assert parse_bait_delay('1d') == 86400

    def test_parse_bait_delay_rejects_invalid_or_out_of_range_values(self):
        from utils import parse_bait_delay

        with pytest.raises(ValueError):
            parse_bait_delay('30s')
        with pytest.raises(ValueError):
            parse_bait_delay('0m')
        with pytest.raises(ValueError):
            parse_bait_delay('29d')

    def test_set_get_and_clear_bait_settings(self):
        from utils import clear_bait_settings, get_bait_settings, set_bait_settings

        set_bait_settings('guild1', channel_id='111', delay_seconds=43200, action='jail')

        settings = get_bait_settings('guild1')
        assert settings['enabled'] is True
        assert settings['channel_id'] == '111'
        assert settings['delay_seconds'] == 43200
        assert settings['action'] == 'jail'

        clear_bait_settings('guild1')
        assert get_bait_settings('guild1')['enabled'] is False

    def test_set_bait_settings_rejects_invalid_action(self):
        from utils import set_bait_settings

        with pytest.raises(ValueError):
            set_bait_settings('guild1', channel_id='111', delay_seconds=43200, action='both')

    def test_bait_exempt_roles_round_trip(self):
        from utils import add_bait_exempt_role, get_bait_settings, remove_bait_exempt_role

        add_bait_exempt_role('guild1', 'role1')
        add_bait_exempt_role('guild1', 'role2')
        add_bait_exempt_role('guild1', 'role1')

        assert get_bait_settings('guild1')['exempt_role_ids'] == ['role1', 'role2']
        assert remove_bait_exempt_role('guild1', 'role1') is True
        assert remove_bait_exempt_role('guild1', 'missing') is False
        assert get_bait_settings('guild1')['exempt_role_ids'] == ['role2']

    def test_pending_bait_ban_can_move_to_banned_and_be_forgiven(self):
        from utils import (
            add_pending_bait_ban,
            forgive_bait_user,
            get_bait_banned,
            get_pending_bait_bans,
            mark_bait_banned,
        )

        add_pending_bait_ban(
            'guild1',
            user_id='user1',
            channel_id='111',
            message_id='msg1',
            action='jail',
            triggered_at='2026-06-30T00:00:00+00:00',
            ban_at='2026-06-30T12:00:00+00:00',
        )

        pending = get_pending_bait_bans('guild1')
        assert len(pending) == 1
        assert pending[0]['user_id'] == 'user1'

        assert mark_bait_banned('guild1', 'user1', banned_at='2026-06-30T12:00:01+00:00') is not None
        assert get_pending_bait_bans('guild1') == []
        assert get_bait_banned('guild1')[0]['user_id'] == 'user1'

        forgiven = forgive_bait_user('guild1', 'user1')
        assert forgiven['status'] == 'banned'
        assert get_bait_banned('guild1') == []

    def test_pending_bait_ban_stores_applied_role_id(self):
        from utils import add_pending_bait_ban, get_pending_bait_bans

        add_pending_bait_ban(
            'guild1',
            user_id='user1',
            channel_id='111',
            message_id='msg1',
            action='jail',
            triggered_at='2026-06-30T00:00:00+00:00',
            ban_at='2026-06-30T12:00:00+00:00',
            applied_role_id='role-a',
        )

        assert get_pending_bait_bans('guild1')[0]['applied_role_id'] == 'role-a'

    def test_pending_bait_ban_can_move_to_failed_and_be_forgiven(self):
        from utils import add_pending_bait_ban, forgive_bait_user, get_bait_failed, mark_bait_failed

        add_pending_bait_ban(
            'guild1',
            user_id='user1',
            channel_id='111',
            message_id='msg1',
            action='jail',
            triggered_at='2026-06-30T00:00:00+00:00',
            ban_at='2026-06-30T12:00:00+00:00',
            applied_role_id='role-a',
        )

        failed = mark_bait_failed(
            'guild1',
            'user1',
            failed_at='2026-06-30T12:00:01+00:00',
            reason='missing ban permissions',
        )

        assert failed['failed_reason'] == 'missing ban permissions'
        assert get_bait_failed('guild1')[0]['user_id'] == 'user1'
        forgiven = forgive_bait_user('guild1', 'user1')
        assert forgiven['status'] == 'failed'
        assert get_bait_failed('guild1') == []


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

    def test_help_meta_perm_tiers(self):
        from utils import get_help_meta, help_meta

        @help_meta(usage='.botpfp', desc='Set bot avatar', perm_tier='creator')
        async def creator_cmd(ctx):
            pass

        @help_meta(usage='.setcolor', desc='Set server theme color', perm_tier='guild_owner')
        async def owner_cmd(ctx):
            pass

        @help_meta(usage='.warn', desc='Warn user', perm_tier='admin', discord_perms=['moderate_members'])
        async def warn_cmd(ctx):
            pass

        meta_creator = get_help_meta(creator_cmd)
        assert meta_creator['perm_tier'] == 'creator'
        assert meta_creator['owner'] is True

        meta_owner = get_help_meta(owner_cmd)
        assert meta_owner['perm_tier'] == 'guild_owner'

        meta_warn = get_help_meta(warn_cmd)
        assert meta_warn['perm_tier'] == 'admin'
        assert meta_warn['discord_perms'] == ['moderate_members']
        assert meta_warn['admin'] is True
