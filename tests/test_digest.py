def _fake_conn(rows):
    class FakeConn:
        def execute(self, sql, params=()):
            return self
        def fetchall(self):
            return rows
    return FakeConn()


def _baselines_conf():
    return {
        "channel_id": "5",
        "baselines": {"10": {"msgs": 100, "vc": 0, "bumps": 0}},
        "member_base": 20,
        "last_run_iso": "",
    }


def test_baselines_update_false_does_not_mutate(monkeypatch):
    from cogs.digest import Digest

    monkeypatch.setattr('cogs.serverstats._get_conn', lambda: _fake_conn([(10, 150)]))
    monkeypatch.setattr('cogs.bumps._get_conn', lambda: _fake_conn([(10, 3)]))

    cog = Digest(None)
    conf = _baselines_conf()
    delta_msgs, delta_vc, delta_bumps = cog._baselines('111', conf, update=False)

    assert delta_msgs == {10: 50}
    assert delta_bumps == {10: 3}
    # baselines must NOT have been advanced to current totals
    assert conf['baselines']['10']['msgs'] == 100
    assert conf['baselines']['10']['bumps'] == 0


def test_baselines_update_true_advances(monkeypatch):
    from cogs.digest import Digest

    monkeypatch.setattr('cogs.serverstats._get_conn', lambda: _fake_conn([(10, 150)]))
    monkeypatch.setattr('cogs.bumps._get_conn', lambda: _fake_conn([]))

    cog = Digest(None)
    conf = _baselines_conf()
    cog._baselines('111', conf, update=True)

    assert conf['baselines']['10']['msgs'] == 150


def test_digest_now_registered():
    import asyncio

    import discord
    from discord.ext import commands

    import cogs.digest

    async def _main():
        import inspect
        bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
        cog = cogs.digest.Digest(bot)
        res = bot.add_cog(cog)
        if inspect.isawaitable(res):
            await res
        assert bot.get_command('digest now') is not None
        if hasattr(cog, "task") and cog.task:
            cog.task.cancel()

    asyncio.run(_main())
