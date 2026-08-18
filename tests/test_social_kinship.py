import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from cogs.social import Social, KINSHIP_JOKES, LOGIC_BLOCKS


@pytest.fixture
def social_cog():
    bot = MagicMock()
    cog = Social(bot)
    cog._kinship_cache = {}
    cog._active_proposals = {}
    cog.is_opted_out = AsyncMock(return_value=False)
    cog._is_locked = MagicMock(return_value=False)
    return cog


@pytest.mark.asyncio
async def test_resolve_relationship_step_family(social_cog):
    # Setup family tree:
    # 101 (Treloco) is parent of 100 (User)
    # 101 (Treloco) is married to 102 (Zul) -> Zul is Step-Parent of User, User is Step-Child of Zul
    # 102 (Zul) is parent of 103 (ZulJr) -> ZulJr and User are Step-Siblings
    parents_map = {
        100: [101],
        103: [102],
    }
    children_map = {
        101: [100],
        102: [103],
    }
    marriages_map = {
        101: (102, "2026-01-01", 1),
        102: (101, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    # User -> Zul: Step-Parent
    assert await social_cog.resolve_relationship(100, 102, mode="combined") == "Step-Parent"
    # Zul -> User: Step-Child
    assert await social_cog.resolve_relationship(102, 100, mode="combined") == "Step-Child"

    # Step-Sibling in both directions
    assert await social_cog.resolve_relationship(100, 103, mode="combined") == "Step-Sibling"
    assert await social_cog.resolve_relationship(103, 100, mode="combined") == "Step-Sibling"


@pytest.mark.asyncio
async def test_resolve_relationship_in_laws(social_cog):
    parents_map = {
        201: [202],
        204: [202],
    }
    children_map = {
        202: [201, 204],
    }
    marriages_map = {
        200: (201, "2026-01-01", 1),
        201: (200, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    # Alice -> Bob's Dad: Parent-in-law
    assert await social_cog.resolve_relationship(200, 202, mode="combined") == "Parent-in-law"
    # Alice -> Bob's Brother: Sibling-in-law
    assert await social_cog.resolve_relationship(200, 204, mode="combined") == "Sibling-in-law"


@pytest.mark.asyncio
async def test_resolve_relationship_ancestor_descendant_in_both_modes(social_cog):
    # 300 (Great-Grandparent) -> 301 (Grandparent) -> 302 (Parent) -> 303 (Great-Grandchild)
    parents_map = {
        303: [302],
        302: [301],
        301: [300],
    }
    children_map = {
        300: [301],
        301: [302],
        302: [303],
    }
    marriages_map = {}

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    # Blood mode
    assert await social_cog.resolve_relationship(303, 300, mode="blood") == "Ancestor"
    assert await social_cog.resolve_relationship(300, 303, mode="blood") == "Descendant"

    # Combined mode regression test: MUST also resolve Ancestor / Descendant
    social_cog._invalidate_kinship_cache()
    assert await social_cog.resolve_relationship(303, 300, mode="combined") == "Ancestor"
    assert await social_cog.resolve_relationship(300, 303, mode="combined") == "Descendant"


@pytest.mark.asyncio
async def test_marry_flow_step_parent_fires_kinship_joke_even_when_married(social_cog):
    # User (400), Step-father Zul (402), Father Treloco (401)
    # Zul is married to Treloco
    parents_map = {400: [401]}
    children_map = {401: [400]}
    marriages_map = {
        401: (402, "2026-01-01", 1),
        402: (401, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.author = SimpleNamespace(id=400, display_name="User", mention="<@400>")
    ctx.send = AsyncMock()

    target = SimpleNamespace(id=402, bot=False, display_name="Zul", mention="<@402>")

    await social_cog.marry.callback(social_cog, ctx, target)

    # Must send a joke from MARRY_STEP_PARENT
    ctx.send.assert_called_once()
    sent_msg = ctx.send.call_args[0][0]
    assert sent_msg in KINSHIP_JOKES["MARRY_STEP_PARENT"]
    # Must NOT be ALREADY_MARRIED
    assert "already married" not in sent_msg


@pytest.mark.asyncio
async def test_marry_flow_unrelated_married_target(social_cog):
    parents_map = {}
    children_map = {}
    marriages_map = {
        502: (503, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.author = SimpleNamespace(id=500, display_name="User", mention="<@500>")
    ctx.send = AsyncMock()

    target = SimpleNamespace(id=502, bot=False, display_name="Stranger", mention="<@502>")

    await social_cog.marry.callback(social_cog, ctx, target)

    ctx.send.assert_called_once()
    sent_msg = ctx.send.call_args[0][0]
    assert sent_msg == LOGIC_BLOCKS["ALREADY_MARRIED"][0].format(target="Stranger")


@pytest.mark.asyncio
async def test_marry_flow_author_already_married(social_cog):
    parents_map = {}
    children_map = {}
    marriages_map = {
        600: (601, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.author = SimpleNamespace(id=600, display_name="User", mention="<@600>")
    ctx.send = AsyncMock()

    target = SimpleNamespace(id=602, bot=False, display_name="Stranger", mention="<@602>")

    await social_cog.marry.callback(social_cog, ctx, target)

    ctx.send.assert_called_once()
    sent_msg = ctx.send.call_args[0][0]
    assert sent_msg == LOGIC_BLOCKS["USER_ALREADY_MARRIED"][0].format(spouse_id=601)


@pytest.mark.asyncio
async def test_marry_flow_own_spouse(social_cog):
    parents_map = {}
    children_map = {}
    marriages_map = {
        700: (701, "2026-01-01", 1),
        701: (700, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.author = SimpleNamespace(id=700, display_name="User", mention="<@700>")
    ctx.send = AsyncMock()

    target = SimpleNamespace(id=701, bot=False, display_name="Spouse", mention="<@701>")

    await social_cog.marry.callback(social_cog, ctx, target)

    ctx.send.assert_called_once()
    sent_msg = ctx.send.call_args[0][0]
    assert sent_msg == LOGIC_BLOCKS["ALREADY_YOUR_SPOUSE"][0].format(target="Spouse")


@pytest.mark.asyncio
async def test_tree_step_family_and_step_siblings(social_cog, monkeypatch):
    # Tempest family setup:
    # 7671 (TreLoco, Parent) married to 1133 (zul, Step-Parent)
    # TreLoco children: 8873 (tempest, Focus), 1137 (yama, Sibling)
    # yama married to 1274 (C., In-law) -> C. child 1294 (Tsuki, Niece/Nephew)
    # zul children (Step-siblings of tempest):
    #   7409 (Step-Sibling) married to 1280 (In-law)
    #   1421 (Step-Sibling)
    #   1484 (Step-Sibling)
    parents_map = {
        8873: [7671],
        1137: [7671],
        1294: [1274],
        7409: [1133],
        1421: [1133],
        1484: [1133],
    }
    children_map = {
        7671: [8873, 1137],
        1133: [7409, 1421, 1484],
        1274: [1294],
    }
    marriages_map = {
        7671: (1133, "2026-01-01", 1),
        1133: (7671, "2026-01-01", 1),
        1137: (1274, "2026-01-01", 1),
        1274: (1137, "2026-01-01", 1),
        7409: (1280, "2026-01-01", 1),
        1280: (7409, "2026-01-01", 1),
    }

    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))
    social_cog.get_family_name = AsyncMock(return_value="DYNASTY")
    social_cog._get_user_display = AsyncMock(side_effect=lambda uid, guild: (f"User_{uid}", f"User_{uid}#0001"))
    social_cog._fetch_avatar = AsyncMock(return_value=None)

    rendered_args = {}
    def mock_render(focus, parents, gps, sib_clusters, fam_name):
        rendered_args["focus"] = focus
        rendered_args["parents"] = parents
        rendered_args["grandparents"] = gps
        rendered_args["sib_clusters"] = sib_clusters
        rendered_args["fam_name"] = fam_name
        import io
        return io.BytesIO(b"fake_png")

    import cogs.social as social_mod
    monkeypatch.setattr(social_mod, "_render_tree_card", mock_render)

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.author = SimpleNamespace(id=8873, display_name="tempest", mention="<@8873>")
    ctx.send = AsyncMock()

    await social_cog.tree.callback(social_cog, ctx, ctx.author)

    assert "sib_clusters" in rendered_args
    # Sibling clusters must include:
    # 1. Focus (8873)
    # 2. Sibling yama (1137)
    # 3. Step-Sibling 7409 (with spouse 1280)
    # 4. Step-Sibling 1421
    # 5. Step-Sibling 1484
    sib_uids = [sc.uid for sc in rendered_args["sib_clusters"]]
    assert 8873 in sib_uids
    assert 1137 in sib_uids
    assert 7409 in sib_uids
    assert 1421 in sib_uids
    assert 1484 in sib_uids

    # Parents must have TreLoco (Parent) and zul (Step-Parent)
    parent_roles = {p.uid: p.role for p in rendered_args["parents"]}
    assert parent_roles[7671] == "Parent"
    assert parent_roles[1133] == "Step-Parent"

