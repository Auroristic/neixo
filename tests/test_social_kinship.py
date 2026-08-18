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
