import pytest
import io
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from cogs.social import Social, _render_tree_card, TreeNode, KINSHIP_JOKES, LOGIC_BLOCKS


@pytest.fixture
def mega_social():
    bot = MagicMock()
    cog = Social(bot)
    cog._kinship_cache = {}
    cog._active_proposals = {}
    cog.is_opted_out = AsyncMock(return_value=False)
    cog._is_locked = MagicMock(return_value=False)
    cog.get_family_name = AsyncMock(return_value="THE_EMPIRE")
    cog._get_user_display = AsyncMock(side_effect=lambda uid, guild: (f"User_{uid}", f"User_{uid}#0001"))
    cog._fetch_avatar = AsyncMock(return_value=None)
    return cog


def create_mega_family():
    """
    Creates a large, complex, 5-generation interconnected family dynasty.

    Generations:
    - Gen -2 (Great-Grandparents): 1 (GG_Pat), 2 (GG_Mat)
    - Gen -1 (Grandparents):
        10 (GP_Pat_Dad, child of 1&2), 11 (GP_Pat_Mom)
        20 (GP_Mat_Dad), 21 (GP_Mat_Mom)
    - Gen 0 (Parents, Step-Parents, Aunts, Uncles):
        100 (Dad, child of 10&11) married to 200 (Mom, child of 20&21)
        101 (Step-Mom, married to 100)
        110 (Aunt, child of 10&11) married to 120 (Uncle)
    - Gen 1 (Focus, Siblings, Step-Siblings, Cousins, Spouses):
        1000 (Focus, child of 100&200) married to 1001 (Spouse)
        1002 (Sibling1, child of 100&200) married to 1003 (Sib1_Spouse)
        1004 (Sibling2, child of 100&200, single)
        1010 (Step_Sibling1, child of 101) married to 1011 (StepSib_Spouse)
        1012 (Step_Sibling2, child of 101, single)
        1020 (Half_Sibling, child of 100&101)
        1100 (Cousin, child of 110&120)
    - Gen 2 (Children, Step-Children, Nieces, Nephews):
        2000 (Focus_Child1, child of 1000&1001) married to 2001 (Child1_Spouse)
        2002 (Focus_Child2, child of 1000&1001)
        2010 (Niece1, child of 1002&1003)
        2020 (Nephew2, child of 1004)
        2030 (Step_Niece, child of 1010&1011)
    - Gen 3 (Grandchildren):
        3000 (Grandchild1, child of 2000&2001)
        3001 (Grandchild2, child of 2002)
    """
    parents_map = {
        # Gen -1
        10: [1, 2],
        # Gen 0
        100: [10, 11],
        110: [10, 11],
        200: [20, 21],
        # Gen 1
        1000: [100, 200],
        1002: [100, 200],
        1004: [100, 200],
        1010: [101],
        1012: [101],
        1020: [100, 101],
        1100: [110, 120],
        # Gen 2
        2000: [1000, 1001],
        2002: [1000, 1001],
        2010: [1002, 1003],
        2020: [1004],
        2030: [1010, 1011],
        # Gen 3
        3000: [2000, 2001],
        3001: [2002],
    }

    # Inverse mapping for children
    children_map = {}
    for child, plist in parents_map.items():
        for p in plist:
            children_map.setdefault(p, []).append(child)

    marriages_map = {
        # Gen -2
        1: (2, "2026-01-01", 1),
        2: (1, "2026-01-01", 1),
        # Gen -1
        10: (11, "2026-01-01", 1),
        11: (10, "2026-01-01", 1),
        20: (21, "2026-01-01", 1),
        21: (20, "2026-01-01", 1),
        # Gen 0
        100: (101, "2026-01-01", 1),  # Remarried to 101 (Step-Mom)
        101: (100, "2026-01-01", 1),
        110: (120, "2026-01-01", 1),
        120: (110, "2026-01-01", 1),
        # Gen 1
        1000: (1001, "2026-01-01", 1),
        1001: (1000, "2026-01-01", 1),
        1002: (1003, "2026-01-01", 1),
        1003: (1002, "2026-01-01", 1),
        1010: (1011, "2026-01-01", 1),
        1011: (1010, "2026-01-01", 1),
        # Gen 2
        2000: (2001, "2026-01-01", 1),
        2001: (2000, "2026-01-01", 1),
    }

    return parents_map, children_map, marriages_map


def setup_mega_cog(social_cog):
    parents_map, children_map, marriages_map = create_mega_family()
    social_cog.get_parents = AsyncMock(side_effect=lambda uid: parents_map.get(uid, []))
    social_cog.get_children = AsyncMock(side_effect=lambda uid: children_map.get(uid, []))
    social_cog.get_marriage = AsyncMock(side_effect=lambda uid: marriages_map.get(uid))
    return parents_map, children_map, marriages_map


@pytest.mark.asyncio
async def test_mega_tree_relationships_all_directions(mega_social):
    setup_mega_cog(mega_social)

    # 1. Direct Parent / Child
    assert await mega_social.resolve_relationship(1000, 100, mode="blood") == "Parent"
    assert await mega_social.resolve_relationship(100, 1000, mode="blood") == "Child"

    # 2. Siblings & Half-Siblings
    assert await mega_social.resolve_relationship(1000, 1002, mode="blood") == "Sibling"
    assert await mega_social.resolve_relationship(1002, 1000, mode="blood") == "Sibling"
    assert await mega_social.resolve_relationship(1000, 1020, mode="blood") == "Sibling"  # Shared dad (100)

    # 3. Grandparent / Grandchild
    assert await mega_social.resolve_relationship(1000, 10, mode="blood") == "Grandparent"
    assert await mega_social.resolve_relationship(10, 1000, mode="blood") == "Grandchild"
    assert await mega_social.resolve_relationship(1000, 21, mode="blood") == "Grandparent"
    assert await mega_social.resolve_relationship(21, 1000, mode="blood") == "Grandchild"

    # 4. Great-Grandparent (Ancestor / Descendant)
    assert await mega_social.resolve_relationship(1000, 1, mode="combined") == "Ancestor"
    assert await mega_social.resolve_relationship(1, 1000, mode="combined") == "Descendant"
    assert await mega_social.resolve_relationship(3000, 100, mode="combined") == "Ancestor"
    assert await mega_social.resolve_relationship(100, 3000, mode="combined") == "Descendant"

    # 5. Aunt/Uncle vs Niece/Nephew
    assert await mega_social.resolve_relationship(1000, 110, mode="blood") == "Aunt/Uncle"
    assert await mega_social.resolve_relationship(110, 1000, mode="blood") == "Niece/Nephew"
    assert await mega_social.resolve_relationship(1000, 2010, mode="blood") == "Niece/Nephew"  # Sibling's child
    assert await mega_social.resolve_relationship(2010, 1000, mode="blood") == "Aunt/Uncle"

    # 6. First Cousins
    assert await mega_social.resolve_relationship(1000, 1100, mode="blood") == "Cousin"
    assert await mega_social.resolve_relationship(1100, 1000, mode="blood") == "Cousin"

    # 7. Step-Parent / Step-Child
    assert await mega_social.resolve_relationship(1000, 101, mode="combined") == "Step-Parent"
    assert await mega_social.resolve_relationship(101, 1000, mode="combined") == "Step-Child"

    # 8. Step-Siblings
    assert await mega_social.resolve_relationship(1000, 1010, mode="combined") == "Step-Sibling"
    assert await mega_social.resolve_relationship(1010, 1000, mode="combined") == "Step-Sibling"
    assert await mega_social.resolve_relationship(1000, 1012, mode="combined") == "Step-Sibling"

    # 9. In-Laws
    assert await mega_social.resolve_relationship(1000, 1003, mode="combined") == "Sibling-in-law"
    assert await mega_social.resolve_relationship(1003, 1000, mode="combined") == "Sibling-in-law"
    assert await mega_social.resolve_relationship(1001, 100, mode="combined") == "Parent-in-law"
    assert await mega_social.resolve_relationship(100, 1001, mode="combined") == "Child-in-law"


@pytest.mark.asyncio
async def test_tree_rendering_from_multiple_perspectives(mega_social, monkeypatch):
    setup_mega_cog(mega_social)

    captured_renders = []

    def mock_render(focus, parents, gps, sib_clusters, fam_name):
        # Also run real _render_tree_card to make sure PIL doesn't crash on canvas layout!
        real_card = _render_tree_card(focus, parents, gps, sib_clusters, fam_name)
        assert isinstance(real_card, io.BytesIO)
        assert real_card.getbuffer().nbytes > 0

        info = {
            "focus_id": focus.uid,
            "parent_ids": [p.uid for p in parents],
            "gp_ids": [g.uid for g in gps],
            "sibling_cluster_ids": [s.uid for s in sib_clusters],
        }
        captured_renders.append(info)
        return real_card

    import cogs.social as social_mod
    monkeypatch.setattr(social_mod, "_render_tree_card", mock_render)

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()

    # Perspective 1: Focus User (1000)
    ctx.author = SimpleNamespace(id=1000, display_name="Focus1000", mention="<@1000>")
    await mega_social.tree.callback(mega_social, ctx, ctx.author)

    p1 = captured_renders[-1]
    assert p1["focus_id"] == 1000
    # Must have both direct dad (100) and step-mom (101) or mom (200)
    assert 100 in p1["parent_ids"]
    # Must have grandparents
    assert len(p1["gp_ids"]) >= 2
    # Sibling clusters must have Focus, Sibling1, Sibling2, Half-Sibling, Step-Siblings
    assert 1000 in p1["sibling_cluster_ids"]
    assert 1002 in p1["sibling_cluster_ids"]
    assert 1004 in p1["sibling_cluster_ids"]
    assert 1010 in p1["sibling_cluster_ids"]
    assert 1012 in p1["sibling_cluster_ids"]
    assert 1020 in p1["sibling_cluster_ids"]

    # Perspective 2: Sibling1 (1002)
    ctx.author = SimpleNamespace(id=1002, display_name="Sib1002", mention="<@1002>")
    await mega_social.tree.callback(mega_social, ctx, ctx.author)

    p2 = captured_renders[-1]
    assert p2["focus_id"] == 1002
    assert 1000 in p2["sibling_cluster_ids"]
    assert 1010 in p2["sibling_cluster_ids"]

    # Perspective 3: Step-Sibling1 (1010)
    ctx.author = SimpleNamespace(id=1010, display_name="StepSib1010", mention="<@1010>")
    await mega_social.tree.callback(mega_social, ctx, ctx.author)

    p3 = captured_renders[-1]
    assert p3["focus_id"] == 1010
    assert 101 in p3["parent_ids"]  # Step-mom is direct mom
    assert 100 in p3["parent_ids"]  # Dad is step-parent
    # Focus and direct siblings must be in step-siblings row
    assert 1000 in p3["sibling_cluster_ids"]
    assert 1012 in p3["sibling_cluster_ids"]

    # Perspective 4: Child1 (2000)
    ctx.author = SimpleNamespace(id=2000, display_name="Child2000", mention="<@2000>")
    await mega_social.tree.callback(mega_social, ctx, ctx.author)

    p4 = captured_renders[-1]
    assert p4["focus_id"] == 2000
    assert 1000 in p4["parent_ids"]  # Focus is parent
    assert 2002 in p4["sibling_cluster_ids"]  # Sibling


@pytest.mark.asyncio
async def test_text_tree_pages_generation(mega_social):
    setup_mega_cog(mega_social)
    pages = await mega_social._build_tree_pages(1000, None)
    full_text = "\n".join(pages)

    # Must contain ancestors, focus, siblings, step-siblings, and children
    assert "Ancestors ✦" in full_text
    assert "User_1000" in full_text
    assert "Siblings ✦" in full_text
    assert "Step-Siblings ✦" in full_text
    assert "Children ✦" in full_text
    assert "User_1010" in full_text
    assert "User_2000" in full_text
    assert "User_3000" in full_text
