import asyncio
import html
import io
import logging
import math
import os
import random
import re
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord.ext import commands
from PIL import Image, ImageDraw

from cogs.serverstats import _load_font, _circle_avatar, _make_glass_backdrop
from utils import CONFIG_FILE, DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json

log = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, "social.db")

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Social, marriage, mini-games, and community interactions.",
}

# ── 8ball responses (minimalist aesthetic) ─────────────────────────
EIGHTBALL_RESPONSES = [
    "yes",
    "most likely",
    "definitely",
    "without a doubt",
    "signs point to yes",
    "ask again later",
    "cannot predict now",
    "don't count on it",
    "my sources say no",
    "very doubtful",
    "no",
    "absolutely not",
]

# ── curated Would You Rather questions ────────────────────────────
WYR_QUESTIONS = [
    ("have the ability to fly", "have the ability to be invisible"),
    ("always be 10 minutes late", "always be 20 minutes early"),
    ("live in a futuristic cyberpunk city", "live in a peaceful secluded forest cottage"),
    ("know the history of every object you touch", "be able to talk to animals"),
    ("never have to sleep again", "never have to eat again without feeling hungry"),
    ("explore deep space", "explore the deepest trenches of the ocean"),
    ("be able to rewind time by 10 seconds", "be able to freeze time for 5 seconds"),
    ("always know when someone is lying", "always get away with any lie"),
    ("lose all your memories from yesterday", "never be able to make new long-term memories"),
    ("have infinite wealth but no internet", "have average wealth with high-speed internet forever"),
    ("be able to speak every human language", "be able to master every musical instrument"),
    ("live one life for 1,000 years", "live 10 different lives for 100 years each"),
]


# ── Marriage Card Renderers & Time Helpers ─────────────────────────
def _format_married_duration(married_dt: datetime) -> tuple[str, str, int, str]:
    now = datetime.now(timezone.utc)
    delta = now - married_dt
    total_seconds = max(0, int(delta.total_seconds()))
    days = delta.days
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if days > 0:
        long_str = f"{days} Day{'s' if days != 1 else ''}, {hours} Hour{'s' if hours != 1 else ''}"
        short_str = f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        long_str = f"{hours} Hour{'s' if hours != 1 else ''}, {minutes} Minute{'s' if minutes != 1 else ''}"
        short_str = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        long_str = f"{minutes} Minute{'s' if minutes != 1 else ''}, {seconds} Second{'s' if seconds != 1 else ''}"
        short_str = f"{minutes}m {seconds}s"
    else:
        long_str = f"{seconds} Second{'s' if seconds != 1 else ''}"
        short_str = f"{seconds}s"

    if days < 1:
        tier_title = "✦  N E W L Y W E D S  ✦"
    elif days < 7:
        tier_title = "✦  S W E E T H E A R T S  ✦"
    elif days < 30:
        tier_title = "✦  D E V O T E D   H E A R T S  ✦"
    elif days < 90:
        tier_title = "✦  S O U L B O U N D  ✦"
    elif days < 365:
        tier_title = "✦  T W I N   F L A M E S  ✦"
    else:
        tier_title = "✦  E T E R N A L   V O W  ✦"

    return long_str, short_str, days, tier_title


def _render_marriage_card(
    av1_bytes: bytes | None,
    av2_bytes: bytes | None,
    name1: str,
    name2: str,
    tag1: str,
    tag2: str,
    duration_str: str,
    date_str: str,
    sent_proposals: int = 0,
    recv_proposals: int = 0,
    header_title: str = "✦  E T E R N A L   V O W  ✦",
) -> io.BytesIO:
    from cogs.serverstats import _load_font, _circle_avatar, _make_glass_backdrop

    W, H = 880, 380
    source_bytes = av1_bytes or av2_bytes
    bg = _make_glass_backdrop(source_bytes, W, H, dark_tint=0.62, blur_radius=24)

    # ── 1. Cosmic Constellation & Sacred Geometry Layer ──────────────
    cosmic = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cosmic)

    # Deterministic celestial sparkle positions
    random.seed(42)
    for _ in range(45):
        sx = random.randint(30, W - 30)
        sy = random.randint(25, H - 25)
        size = random.choice([1, 1, 2, 2, 3])
        alpha = random.randint(25, 75)
        c_draw.ellipse([sx, sy, sx + size, sy + size], fill=(255, 255, 255, alpha))

    # Sacred orbital rings in background center
    cx, cy = W // 2, 160
    for r, a in [(130, 14), (170, 10), (210, 6)]:
        c_draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, a), width=1)
    c_draw.line([(cx - 220, cy), (cx + 220, cy)], fill=(255, 255, 255, 8), width=1)
    c_draw.line([(cx, cy - 140), (cx, cy + 140)], fill=(255, 255, 255, 8), width=1)

    bg = Image.alpha_composite(bg, cosmic)

    # ── 2. Glass Frame & Ornamental Corner Filigree ─────────────────
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 26, 20

    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=26,
        fill=(0, 0, 0, 125),
        outline=(255, 255, 255, 45),
        width=1,
    )
    cd.rounded_rectangle(
        [pad_x + 6, pad_y + 6, W - pad_x - 6, H - pad_y - 6],
        radius=20,
        outline=(255, 255, 255, 18),
        width=1,
    )
    cd.line([(pad_x + 35, pad_y + 1), (W - pad_x - 35, pad_y + 1)], fill=(255, 255, 255, 110), width=1)

    def draw_corner_ornament(x, y, dx, dy):
        cd.line([(x, y), (x + dx * 16, y)], fill=(255, 255, 255, 90), width=1)
        cd.line([(x, y), (x, y + dy * 16)], fill=(255, 255, 255, 90), width=1)
        px, py = x + dx * 8, y + dy * 8
        cd.polygon([(px, py - 2), (px + 2, py), (px, py + 2), (px - 2, py)], fill=(255, 255, 255, 140))

    draw_corner_ornament(pad_x + 12, pad_y + 12, 1, 1)
    draw_corner_ornament(W - pad_x - 12, pad_y + 12, -1, 1)
    draw_corner_ornament(pad_x + 12, H - pad_y - 12, 1, -1)
    draw_corner_ornament(W - pad_x - 12, H - pad_y - 12, -1, -1)

    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(12, bold=True)
    f_huge = _load_font(30, bold=True)
    f_sub = _load_font(15, bold=False)
    f_name = _load_font(21, bold=True)
    f_tag = _load_font(14, bold=False)
    f_badge = _load_font(13, bold=True)
    f_symbols = _load_font(16, bold=False)

    # ── 3. Top Header Badge ──────────────────────────────────────────
    header_text = header_title
    hw = f_title.getlength(header_text)
    pill_w = hw + 36
    pill_h = 28
    pill_x = (W - pill_w) // 2
    pill_y = pad_y + 14
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=14,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 120),
        width=1,
    )
    f_title.draw(draw, ((W - hw) // 2, pill_y + 7), header_text, fill=(12, 14, 18, 255))

    left_line_end = pill_x - 16
    draw.line([(pad_x + 55, pill_y + 14), (left_line_end, pill_y + 14)], fill=(255, 255, 255, 45), width=1)
    draw.ellipse([left_line_end - 4, pill_y + 12, left_line_end, pill_y + 16], fill=(255, 255, 255, 120))

    right_line_start = pill_x + pill_w + 16
    draw.line([(right_line_start, pill_y + 14), (W - pad_x - 55, pill_y + 14)], fill=(255, 255, 255, 45), width=1)
    draw.ellipse([right_line_start, pill_y + 12, right_line_start + 4, pill_y + 16], fill=(255, 255, 255, 120))

    # ── 4. Avatars with Sacred Halos & Ticks ─────────────────────────
    av_size = 120
    av1_x, av1_y = pad_x + 46, pad_y + 54
    if av1_bytes:
        try:
            av1_img = _circle_avatar(av1_bytes, av_size)
            bg.paste(av1_img, (av1_x, av1_y), av1_img)
            draw.ellipse([av1_x, av1_y, av1_x + av_size, av1_y + av_size], outline=(255, 255, 255, 95), width=2)
            draw.ellipse([av1_x - 6, av1_y - 6, av1_x + av_size + 6, av1_y + av_size + 6], outline=(255, 255, 255, 30), width=1)
            draw.ellipse([av1_x - 12, av1_y - 12, av1_x + av_size + 12, av1_y + av_size + 12], outline=(255, 255, 255, 15), width=1)
        except Exception:
            pass

    av2_x, av2_y = W - pad_x - 46 - av_size, pad_y + 54
    if av2_bytes:
        try:
            av2_img = _circle_avatar(av2_bytes, av_size)
            bg.paste(av2_img, (av2_x, av2_y), av2_img)
            draw.ellipse([av2_x, av2_y, av2_x + av_size, av2_y + av_size], outline=(255, 255, 255, 95), width=2)
            draw.ellipse([av2_x - 6, av2_y - 6, av2_x + av_size + 6, av2_y + av_size + 6], outline=(255, 255, 255, 30), width=1)
            draw.ellipse([av2_x - 12, av2_y - 12, av2_x + av_size + 12, av2_y + av_size + 12], outline=(255, 255, 255, 15), width=1)
        except Exception:
            pass

    # User 1 name & tag
    w1 = f_name.getlength(name1[:14])
    f_name.draw(draw, (av1_x + (av_size - w1) // 2, av1_y + av_size + 14), name1[:14], fill=(255, 255, 255, 245))
    t1_w = f_tag.getlength(tag1[:16])
    f_tag.draw(draw, (av1_x + (av_size - t1_w) // 2, av1_y + av_size + 38), tag1[:16], fill=(165, 170, 185, 200))

    # User 2 name & tag
    w2 = f_name.getlength(name2[:14])
    f_name.draw(draw, (av2_x + (av_size - w2) // 2, av2_y + av_size + 14), name2[:14], fill=(255, 255, 255, 245))
    t2_w = f_tag.getlength(tag2[:16])
    f_tag.draw(draw, (av2_x + (av_size - t2_w) // 2, av2_y + av_size + 38), tag2[:16], fill=(165, 170, 185, 200))

    # ── 5. Center Symbolic Interlocking Rings & Starburst ────────────
    mid_y = pad_y + 92

    l_start = av1_x + av_size + 24
    l_end = cx - 55
    draw.line([(l_start, mid_y), (l_end, mid_y)], fill=(255, 255, 255, 55), width=1)
    draw.polygon([(l_start - 3, mid_y), (l_start, mid_y - 3), (l_start + 3, mid_y), (l_start, mid_y + 3)], fill=(255, 255, 255, 160))
    draw.polygon([(l_end - 3, mid_y), (l_end, mid_y - 3), (l_end + 3, mid_y), (l_end, mid_y + 3)], fill=(255, 255, 255, 160))

    r_start = cx + 55
    r_end = av2_x - 24
    draw.line([(r_start, mid_y), (r_end, mid_y)], fill=(255, 255, 255, 55), width=1)
    draw.polygon([(r_start - 3, mid_y), (r_start, mid_y - 3), (r_start + 3, mid_y), (r_start, mid_y + 3)], fill=(255, 255, 255, 160))
    draw.polygon([(r_end - 3, mid_y), (r_end, mid_y - 3), (r_end + 3, mid_y), (r_end, mid_y + 3)], fill=(255, 255, 255, 160))

    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        x1 = cx + math.cos(rad) * 22
        y1 = mid_y + math.sin(rad) * 22
        x2 = cx + math.cos(rad) * 32
        y2 = mid_y + math.sin(rad) * 32
        draw.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 30), width=1)

    ring_sym = "💍"
    f_ring = _load_font(30, bold=False)
    rw = f_ring.getlength(ring_sym)
    f_ring.draw(draw, (cx - rw // 2, mid_y - 19), ring_sym, fill=(255, 255, 255, 255))

    f_symbols.draw(draw, (cx - rw // 2 - 24, mid_y - 10), "✧", fill=(255, 255, 255, 180))
    f_symbols.draw(draw, (cx + rw // 2 + 12, mid_y - 10), "✧", fill=(255, 255, 255, 180))

    # Duration text
    dw = f_huge.getlength(duration_str)
    f_huge.draw(draw, ((W - dw) // 2, mid_y + 30), duration_str, fill=(255, 255, 255, 255))

    # Married since date with star points
    date_text = f"✦  Married on {date_str}  ✦" if not date_str.startswith("✦") else date_str
    date_w = f_sub.getlength(date_text)
    f_sub.draw(draw, ((W - date_w) // 2, mid_y + 70), date_text, fill=(185, 190, 205, 210))

    # ── 6. Bottom Proposal Stats Pill ────────────────────────────────
    stats_text = f"Proposals: {sent_proposals} sent   •   {recv_proposals} received"
    sw = f_badge.getlength(stats_text)
    pill_w2 = sw + 36
    pill_h2 = 32
    pill_x2 = (W - pill_w2) // 2
    pill_y2 = H - pad_y - 50
    draw.rounded_rectangle(
        [pill_x2, pill_y2, pill_x2 + pill_w2, pill_y2 + pill_h2],
        radius=16,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_badge.draw(draw, (pill_x2 + 18, pill_y2 + 8), stats_text, fill=(12, 14, 18, 255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


def _render_single_card(
    av_bytes: bytes | None,
    name: str,
    tag: str,
    sent_proposals: int = 0,
    recv_proposals: int = 0,
) -> io.BytesIO:
    from cogs.serverstats import _load_font, _circle_avatar, _make_glass_backdrop

    W, H = 700, 280
    bg = _make_glass_backdrop(av_bytes, W, H, dark_tint=0.62, blur_radius=22)

    # Cosmic Dust
    cosmic = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cosmic)
    random.seed(33)
    for _ in range(30):
        sx = random.randint(20, W - 20)
        sy = random.randint(20, H - 20)
        size = random.choice([1, 2])
        alpha = random.randint(20, 60)
        c_draw.ellipse([sx, sy, sx + size, sy + size], fill=(255, 255, 255, alpha))
    bg = Image.alpha_composite(bg, cosmic)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 24, 18
    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=24,
        fill=(0, 0, 0, 125),
        outline=(255, 255, 255, 45),
        width=1,
    )
    cd.rounded_rectangle(
        [pad_x + 5, pad_y + 5, W - pad_x - 5, H - pad_y - 5],
        radius=19,
        outline=(255, 255, 255, 18),
        width=1,
    )
    cd.line([(pad_x + 25, pad_y + 1), (W - pad_x - 25, pad_y + 1)], fill=(255, 255, 255, 95), width=1)

    def draw_corner_ornament(x, y, dx, dy):
        cd.line([(x, y), (x + dx * 14, y)], fill=(255, 255, 255, 80), width=1)
        cd.line([(x, y), (x, y + dy * 14)], fill=(255, 255, 255, 80), width=1)
        px, py = x + dx * 7, y + dy * 7
        cd.polygon([(px, py - 2), (px + 2, py), (px, py + 2), (px - 2, py)], fill=(255, 255, 255, 120))

    draw_corner_ornament(pad_x + 10, pad_y + 10, 1, 1)
    draw_corner_ornament(W - pad_x - 10, pad_y + 10, -1, 1)
    draw_corner_ornament(pad_x + 10, H - pad_y - 10, 1, -1)
    draw_corner_ornament(W - pad_x - 10, H - pad_y - 10, -1, -1)

    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(12, bold=True)
    f_huge = _load_font(25, bold=True)
    f_sub = _load_font(15, bold=False)
    f_name = _load_font(21, bold=True)
    f_tag = _load_font(14, bold=False)
    f_badge = _load_font(13, bold=True)

    header_text = "✦  M A R R I A G E   P R O F I L E  ✦"
    hw = f_title.getlength(header_text)
    pill_w1 = hw + 32
    pill_h1 = 26
    pill_x1 = (W - pill_w1) // 2
    pill_y1 = pad_y + 12
    draw.rounded_rectangle(
        [pill_x1, pill_y1, pill_x1 + pill_w1, pill_y1 + pill_h1],
        radius=13,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_title.draw(draw, ((W - hw) // 2, pill_y1 + 6), header_text, fill=(12, 14, 18, 255))

    av_size = 105
    av_x, av_y = pad_x + 40, pad_y + 50
    if av_bytes:
        try:
            av_img = _circle_avatar(av_bytes, av_size)
            bg.paste(av_img, (av_x, av_y), av_img)
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 90), width=2)
            draw.ellipse([av_x - 5, av_y - 5, av_x + av_size + 5, av_y + av_size + 5], outline=(255, 255, 255, 25), width=1)
        except Exception:
            pass

    info_x = av_x + av_size + 35
    w_n = f_name.getlength(name[:16])
    f_name.draw(draw, (info_x, pad_y + 56), name[:16], fill=(255, 255, 255, 245))
    f_tag.draw(draw, (info_x + w_n + 10, pad_y + 61), tag[:16], fill=(160, 165, 180, 190))

    status_text = "Single  •  Not Married"
    f_huge.draw(draw, (info_x, pad_y + 88), status_text, fill=(245, 245, 255, 250))

    f_sub.draw(draw, (info_x, pad_y + 120), "✦ Use `.marry @someone` to propose! ✦", fill=(175, 180, 195, 200))

    # Stats pill
    stats_text = f"Proposals: {sent_proposals} sent   •   {recv_proposals} received"
    sw = f_badge.getlength(stats_text)
    pill_w = sw + 30
    pill_h = 30
    pill_y = H - pad_y - 44
    draw.rounded_rectangle(
        [info_x, pill_y, info_x + pill_w, pill_y + pill_h],
        radius=15,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_badge.draw(draw, (info_x + 15, pill_y + 7), stats_text, fill=(12, 14, 18, 255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


# ── Family Tree Graphic Node & Card Renderer ─────────────────────────
class TreeNode:
    def __init__(self, uid: int, name: str, tag: str, role: str, av_bytes: bytes | None = None, spouse: "TreeNode | None" = None, children: list["TreeNode"] | None = None):
        self.uid = uid
        self.name = name
        self.tag = tag
        self.role = role
        self.av_bytes = av_bytes
        self.spouse = spouse
        self.children = children or []


def _safe_circle_avatar(img_bytes: bytes | None, size: int) -> Image.Image:
    im = None
    if img_bytes:
        try:
            im = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except Exception:
            im = None
    if im is None:
        im = Image.new("RGBA", (size, size), (22, 25, 33, 255))
        d = ImageDraw.Draw(im)
        f_av = _load_font(size // 3, bold=True)
        f_av.draw(d, (size // 3, size // 4), "✦", fill=(120, 125, 145, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _draw_interlocking_rings(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int = 7):
    draw.ellipse([cx - r - 3, cy - r, cx + r - 3, cy + r], outline=(255, 255, 255, 220), width=2)
    draw.ellipse([cx - r + 3, cy - r, cx + r + 3, cy + r], outline=(255, 255, 255, 220), width=2)
    draw.rectangle([cx - 4, cy - r - 2, cx - 2, cy - r + 2], fill=(255, 255, 255, 255))
    draw.rectangle([cx + 2, cy - r - 2, cx + 4, cy - r + 2], fill=(255, 255, 255, 255))


def _render_tree_card(
    focus_user: TreeNode,
    spouse: TreeNode | None,
    parents: list[TreeNode],          # up to 2
    grandparents: list[TreeNode],     # grandparents
    children_trees: list[TreeNode],   # children, each can have their own spouse and grandchildren!
    siblings: list[TreeNode],         # siblings
    family_name: str | None = None,
) -> io.BytesIO:
    has_grandparents = len(grandparents) > 0
    has_parents = len(parents) > 0
    has_children = len(children_trees) > 0
    has_grandchildren = any(len(c.children) > 0 for c in children_trees)

    total_grandkids = sum(len(c.children) for c in children_trees)
    total_child_slots = sum(2 if c.spouse else 1 for c in children_trees)

    max_horizontal_elements = max(
        len(grandparents) or 1,
        (len(parents) * 2) or 1,
        (2 if spouse else 1) + len(siblings),
        total_child_slots or 1,
        total_grandkids or 1,
    )

    base_node_spacing = 130
    calculated_w = max(960, max_horizontal_elements * base_node_spacing + 180)
    W = min(calculated_w, 1400)

    tier_count = 1
    if has_grandparents: tier_count += 1
    if has_parents: tier_count += 1
    if has_children: tier_count += 1
    if has_grandchildren: tier_count += 1

    tier_height = 175
    H = max(600, tier_count * tier_height + 150)

    bg = Image.new("RGBA", (W, H), (7, 8, 11, 255))

    cx = W // 2
    cy = H // 2
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for r in range(min(W, H) // 2, 40, -40):
        alpha = int(7 * (1 - r / (min(W, H) // 2)))
        gdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, alpha))
    bg = Image.alpha_composite(bg, glow)
    draw = ImageDraw.Draw(bg)

    f_star = _load_font(10)
    stars = [
        (50, 70), (120, 140), (W - 60, 90), (W - 120, 200),
        (80, H - 90), (W - 80, H - 80), (130, cy), (W - 130, cy),
        (200, 60), (W - 200, 60), (cx, 45), (cx, H - 45),
    ]
    for sx, sy in stars:
        f_star.draw(draw, (sx - 4, sy - 4), "✦", fill=(255, 255, 255, 40))

    pad_x, pad_y = 24, 20
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card)
    cdraw.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=18,
        fill=(13, 15, 20, 230),
        outline=(255, 255, 255, 32),
        width=1,
    )

    def draw_corner(x, y, dx, dy):
        cdraw.line([(x, y), (x + dx * 20, y)], fill=(255, 255, 255, 100), width=2)
        cdraw.line([(x, y), (x, y + dy * 20)], fill=(255, 255, 255, 100), width=2)
        cdraw.rectangle([x - 2, y - 2, x + 2, y + 2], fill=(255, 255, 255, 180))

    draw_corner(pad_x + 10, pad_y + 10, 1, 1)
    draw_corner(W - pad_x - 10, pad_y + 10, -1, 1)
    draw_corner(pad_x + 10, H - pad_y - 10, 1, -1)
    draw_corner(W - pad_x - 10, H - pad_y - 10, -1, -1)

    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(11, bold=True)
    f_name = _load_font(12, bold=True)
    f_badge = _load_font(9, bold=True)

    title_text = f"✦   HOUSE OF {family_name.upper()}   ✦" if family_name else f"✦   {focus_user.name.upper()}'S FAMILY DYNASTY   ✦"
    tw = f_title.getlength(title_text)
    pill_w = tw + 36
    pill_h = 26
    pill_x = (W - pill_w) // 2
    pill_y = pad_y + 16
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=13,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 120),
        width=1,
    )
    f_title.draw(draw, ((W - tw) // 2, pill_y + 6), title_text, fill=(12, 14, 18, 255))

    draw.line([(pad_x + 60, pill_y + 13), (pill_x - 16, pill_y + 13)], fill=(255, 255, 255, 45), width=1)
    draw.ellipse([pill_x - 20, pill_y + 11, pill_x - 16, pill_y + 15], fill=(255, 255, 255, 120))
    draw.line([(pill_x + pill_w + 16, pill_y + 13), (W - pad_x - 60, pill_y + 13)], fill=(255, 255, 255, 45), width=1)
    draw.ellipse([pill_x + pill_w + 16, pill_y + 11, pill_x + pill_w + 20, pill_y + 15], fill=(255, 255, 255, 120))

    def render_node(x: int, y: int, node: TreeNode, size: int = 68, is_focus: bool = False, role_label: str | None = None):
        av_img = _safe_circle_avatar(node.av_bytes, size)
        bg.paste(av_img, (x - size // 2, y - size // 2), av_img)

        border_col = (255, 255, 255, 230) if is_focus else (255, 255, 255, 110)
        draw.ellipse([x - size // 2, y - size // 2, x + size // 2, y + size // 2], outline=border_col, width=2 if not is_focus else 3)
        draw.ellipse([x - size // 2 - 3, y - size // 2 - 3, x + size // 2 + 3, y + size // 2 + 3], outline=(255, 255, 255, 45), width=1)
        if is_focus:
            draw.ellipse([x - size // 2 - 7, y - size // 2 - 7, x + size // 2 + 7, y + size // 2 + 7], outline=(255, 255, 255, 22), width=1)

        n_txt = node.name[:11]
        nw = f_name.getlength(n_txt)
        f_name.draw(draw, (x - nw // 2, y + size // 2 + 5), n_txt, fill=(255, 255, 255, 245))

        role_txt = role_label or node.role
        if role_txt:
            rw = f_badge.getlength(role_txt)
            rb_w = rw + 12
            rb_h = 15
            rb_x = x - rb_w // 2
            rb_y = y + size // 2 + 22
            bg_col = (255, 255, 255, 225) if is_focus else (32, 35, 44, 230)
            txt_col = (12, 14, 18, 255) if is_focus else (185, 190, 205, 255)
            draw.rounded_rectangle([rb_x, rb_y, rb_x + rb_w, rb_y + rb_h], radius=7, fill=bg_col, outline=(255, 255, 255, 40), width=1)
            f_badge.draw(draw, (rb_x + 6, rb_y + 1), role_txt, fill=txt_col)

    curr_tier_y = pad_y + 80

    tier_gp_y = 0
    if has_grandparents:
        tier_gp_y = curr_tier_y + 40
        curr_tier_y += tier_height
        gp_count = len(grandparents)
        span_gp = (gp_count - 1) * 110
        start_gp_x = cx - span_gp // 2
        for i, gp in enumerate(grandparents):
            render_node(start_gp_x + i * 110, tier_gp_y, gp, size=60, role_label="Grandparent")

    tier_p_y = 0
    if has_parents:
        tier_p_y = curr_tier_y + 45
        curr_tier_y += tier_height
        if len(parents) == 1:
            render_node(cx, tier_p_y, parents[0], size=68, role_label="Parent")
        else:
            p1_x = cx - 95
            p2_x = cx + 95
            render_node(p1_x, tier_p_y, parents[0], size=68, role_label="Parent")
            render_node(p2_x, tier_p_y, parents[1], size=68, role_label="Parent")
            draw.line([(p1_x + 34, tier_p_y), (cx - 16, tier_p_y)], fill=(255, 255, 255, 75), width=2)
            draw.line([(cx + 16, tier_p_y), (p2_x - 34, tier_p_y)], fill=(255, 255, 255, 75), width=2)
            _draw_interlocking_rings(draw, cx, tier_p_y, r=7)

        if has_grandparents and tier_gp_y:
            draw.line([(cx, tier_gp_y + 44), (cx, tier_p_y - 42)], fill=(255, 255, 255, 45), width=1)

    tier_focus_y = curr_tier_y + 50
    curr_tier_y += tier_height

    # Parent drop line to Focus:
    # If 2 parents: starts at wedding rings (cx, tier_p_y + 14)
    # If 1 parent: starts below parent badge (cx, tier_p_y + 76)
    if has_parents and tier_p_y:
        drop_start_y = tier_p_y + 14 if len(parents) > 1 else tier_p_y + 76
        focus_top_target = tier_focus_y - 42
        if focus_top_target > drop_start_y:
            draw.line([(cx, drop_start_y), (cx, focus_top_target)], fill=(255, 255, 255, 55), width=2)
            draw.ellipse([cx - 3, focus_top_target - 2, cx + 3, focus_top_target + 4], fill=(255, 255, 255, 140))

    if spouse:
        f_x = cx - 85
        sp_x = cx + 85
        render_node(f_x, tier_focus_y, focus_user, size=80, is_focus=True, role_label="Focus")
        render_node(sp_x, tier_focus_y, spouse, size=74, role_label="Spouse")
        draw.line([(f_x + 40, tier_focus_y), (cx - 16, tier_focus_y)], fill=(255, 255, 255, 85), width=2)
        draw.line([(cx + 16, tier_focus_y), (sp_x - 37, tier_focus_y)], fill=(255, 255, 255, 85), width=2)
        _draw_interlocking_rings(draw, cx, tier_focus_y, r=7)
    else:
        f_x = cx
        render_node(f_x, tier_focus_y, focus_user, size=82, is_focus=True, role_label="Focus")

    if siblings:
        for i, sib in enumerate(siblings[:4]):
            if spouse:
                sib_x = cx - 85 - 110 - (i // 2) * 105 if i % 2 == 0 else cx + 85 + 110 + (i // 2) * 105
            else:
                sib_x = cx - 120 - (i // 2) * 105 if i % 2 == 0 else cx + 120 + (i // 2) * 105
            render_node(sib_x, tier_focus_y, sib, size=60, role_label="Sibling")
            if has_parents and tier_p_y:
                draw.line([(sib_x, tier_focus_y - 36), (sib_x, tier_focus_y - 48), (cx, tier_focus_y - 48)], fill=(255, 255, 255, 40), width=1)

    if has_children:
        tier_child_y = curr_tier_y + 45
        curr_tier_y += tier_height
        tier_grandchild_y = curr_tier_y + 45 if has_grandchildren else 0

        # Focus drop line to children bus:
        # If spouse present: starts at (cx, tier_focus_y + 14)
        # If no spouse: starts below focus badge at (cx, tier_focus_y + 86)
        focus_child_drop_start = tier_focus_y + 14 if spouse else tier_focus_y + 86
        bus_y = max(tier_focus_y + 95, focus_child_drop_start + 15)
        draw.line([(cx, focus_child_drop_start), (cx, bus_y)], fill=(255, 255, 255, 55), width=2)

        c_count = len(children_trees)
        cluster_widths = []
        for c in children_trees:
            c_gkids = len(c.children)
            c_width = max(110 if not c.spouse else 190, c_gkids * 100)
            cluster_widths.append(c_width)

        total_span = sum(cluster_widths) + (c_count - 1) * 30
        curr_cluster_x = cx - total_span // 2

        draw.line([(cx - total_span // 2 + cluster_widths[0] // 2, bus_y), 
                   (cx - total_span // 2 + total_span - cluster_widths[-1] // 2, bus_y)], 
                  fill=(255, 255, 255, 55), width=2)

        for i, child_tree in enumerate(children_trees):
            c_w = cluster_widths[i]
            c_center_x = curr_cluster_x + c_w // 2

            child_top_target = tier_child_y - 42
            draw.line([(c_center_x, bus_y), (c_center_x, child_top_target)], fill=(255, 255, 255, 55), width=2)
            draw.ellipse([c_center_x - 3, child_top_target - 2, c_center_x + 3, child_top_target + 4], fill=(255, 255, 255, 140))

            if child_tree.spouse:
                ch_x1 = c_center_x - 45
                ch_x2 = c_center_x + 45
                render_node(ch_x1, tier_child_y, child_tree, size=66, role_label="Child")
                render_node(ch_x2, tier_child_y, child_tree.spouse, size=62, role_label="In-law")
                draw.line([(ch_x1 + 33, tier_child_y), (c_center_x - 14, tier_child_y)], fill=(255, 255, 255, 75), width=2)
                draw.line([(c_center_x + 14, tier_child_y), (ch_x2 - 31, tier_child_y)], fill=(255, 255, 255, 75), width=2)
                _draw_interlocking_rings(draw, c_center_x, tier_child_y, r=6)
            else:
                render_node(c_center_x, tier_child_y, child_tree, size=66, role_label="Child")

            if child_tree.children and tier_grandchild_y:
                gk_count = len(child_tree.children)
                gk_drop_top = tier_child_y + 14 if child_tree.spouse else tier_child_y + 76
                gk_bus_y = max(tier_child_y + 85, gk_drop_top + 15)
                draw.line([(c_center_x, gk_drop_top), (c_center_x, gk_bus_y)], fill=(255, 255, 255, 45), width=2)

                gk_span = (gk_count - 1) * 95
                gk_start_x = c_center_x - gk_span // 2
                if gk_count > 1:
                    draw.line([(gk_start_x, gk_bus_y), (gk_start_x + gk_span, gk_bus_y)], fill=(255, 255, 255, 45), width=2)

                for j, gk in enumerate(child_tree.children):
                    gk_x = gk_start_x + j * 95
                    gk_top_target = tier_grandchild_y - 38
                    draw.line([(gk_x, gk_bus_y), (gk_x, gk_top_target)], fill=(255, 255, 255, 45), width=2)
                    render_node(gk_x, tier_grandchild_y, gk, size=58, role_label="Grandchild")

            curr_cluster_x += c_w + 30

    total_relatives = 1 + (1 if spouse else 0) + len(parents) + len(grandparents) + len(siblings) + \
                      len(children_trees) + sum(1 for c in children_trees if c.spouse) + total_grandkids
    stats_text = f"✦   FAMILY DYNASTY NETWORK   •   {total_relatives}   TOTAL MEMBERS   ✦"
    stw = f_title.getlength(stats_text)
    spill_w = stw + 32
    spill_h = 24
    spill_x = (W - spill_w) // 2
    spill_y = H - pad_y - 28
    draw.rounded_rectangle(
        [spill_x, spill_y, spill_x + spill_w, spill_y + spill_h],
        radius=12,
        fill=(245, 248, 255, 245),
        outline=(255, 255, 255, 120),
        width=1,
    )
    f_title.draw(draw, (spill_x + 16, spill_y + 5), stats_text, fill=(12, 14, 18, 255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


# ── Marriage Proposal View ─────────────────────────────────────────
class MarryProposalView(discord.ui.View):
    def __init__(self, author: discord.Member, target: discord.Member, cog: "Social"):
        super().__init__(timeout=60)
        self.author = author
        self.target = target
        self.cog = cog
        self.value = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("-# this proposal isn't for you", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="accept", style=discord.ButtonStyle.secondary, emoji="💍")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True

        m1 = await self.cog.get_marriage(self.author.id)
        if m1:
            return await interaction.response.edit_message(
                content=f"-# {self.author.mention} is already married to <@{m1[0]}>",
                view=self,
            )
        m2 = await self.cog.get_marriage(self.target.id)
        if m2:
            return await interaction.response.edit_message(
                content=f"-# {self.target.mention} is already married to <@{m2[0]}>",
                view=self,
            )

        await self.cog.create_marriage(self.author.id, self.target.id, interaction.guild_id or 0)
        await self.cog.record_proposal_result(self.author.id, self.target.id, accepted=True)
        
        await interaction.response.defer()
        try:
            av1_bytes = await self.cog._fetch_avatar(self.author)
            av2_bytes = await self.cog._fetch_avatar(self.target)
            now_dt = datetime.now(timezone.utc)
            date_str = now_dt.strftime("%b %d, %Y")
            
            stats = await self.cog.get_proposal_stats(self.author.id)
            sent, recv, _, _ = stats
            
            card_buf = await asyncio.to_thread(
                _render_marriage_card,
                av1_bytes,
                av2_bytes,
                self.author.display_name,
                self.target.display_name,
                f"@{self.author.name}",
                f"@{self.target.name}",
                "Just Married 💍",
                date_str,
                sent,
                recv,
                "✦  N E W L Y W E D S  ✦",
            )
            file = discord.File(card_buf, filename="wedding.png")
            await interaction.message.edit(content=None, attachments=[file], view=self)
        except Exception as e:
            log.warning(f"error rendering wedding card: {e}")
            await interaction.message.edit(
                content=f"-# {self.author.mention} and {self.target.mention} are now married 💍",
                view=self,
            )
        self.stop()

    @discord.ui.button(label="decline", style=discord.ButtonStyle.secondary, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await self.cog.record_proposal_result(self.author.id, self.target.id, accepted=False)
        await interaction.response.edit_message(
            content=f"-# {self.target.mention} declined {self.author.mention}'s proposal",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"-# proposal from {self.author.mention} to {self.target.mention} timed out ⏳",
                    view=self,
                )
            except discord.HTTPException:
                pass
        self.stop()


# ── Adoption Proposal View ─────────────────────────────────────────
class AdoptProposalView(discord.ui.View):
    def __init__(self, author: discord.Member, target: discord.Member, cog: "Social"):
        super().__init__(timeout=60)
        self.author = author
        self.target = target
        self.cog = cog
        self.value = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("-# this proposal isn't for you", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="accept", style=discord.ButtonStyle.secondary, emoji="🌿")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True

        parents = await self.cog.get_parents(self.target.id)
        if len(parents) >= 2:
            return await interaction.response.edit_message(
                content=f"-# {self.target.mention} already has the maximum of 2 parents",
                view=self,
            )
        if await self.cog.is_ancestor_or_descendant(self.author.id, self.target.id):
            return await interaction.response.edit_message(
                content="-# cannot complete adoption due to family cycle",
                view=self,
            )

        await self.cog.create_adoption(self.author.id, self.target.id, interaction.guild_id or 0)
        await interaction.response.edit_message(
            content=f"-# 🌿 {self.target.mention} is now adopted by {self.author.mention} ✦",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="decline", style=discord.ButtonStyle.secondary, emoji="✖")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await interaction.response.edit_message(
            content=f"-# {self.target.mention} declined {self.author.mention}'s adoption request",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"-# adoption proposal from {self.author.mention} to {self.target.mention} timed out ⏳",
                    view=self,
                )
            except discord.HTTPException:
                pass
        self.stop()


# ── Make Parent Proposal View ──────────────────────────────────────
class MakeParentProposalView(discord.ui.View):
    def __init__(self, author: discord.Member, target: discord.Member, cog: "Social"):
        super().__init__(timeout=60)
        self.author = author
        self.target = target
        self.cog = cog
        self.value = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("-# this proposal isn't for you", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="accept", style=discord.ButtonStyle.secondary, emoji="🌿")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True

        parents = await self.cog.get_parents(self.author.id)
        if len(parents) >= 2:
            return await interaction.response.edit_message(
                content=f"-# {self.author.mention} already has the maximum of 2 parents",
                view=self,
            )
        if await self.cog.is_ancestor_or_descendant(self.target.id, self.author.id):
            return await interaction.response.edit_message(
                content="-# cannot complete adoption due to family cycle",
                view=self,
            )

        # target becomes parent, author becomes child
        await self.cog.create_adoption(self.target.id, self.author.id, interaction.guild_id or 0)
        await interaction.response.edit_message(
            content=f"-# 🌿 {self.target.mention} is now the parent of {self.author.mention} ✦",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="decline", style=discord.ButtonStyle.secondary, emoji="✖")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await interaction.response.edit_message(
            content=f"-# {self.target.mention} declined to be {self.author.mention}'s parent",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"-# parent request from {self.author.mention} to {self.target.mention} timed out ⏳",
                    view=self,
                )
            except discord.HTTPException:
                pass
        self.stop()


# ── Disown Confirmation View ───────────────────────────────────────
class DisownConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member, child_id: int, cog: "Social"):
        super().__init__(timeout=30)
        self.author = author
        self.child_id = child_id
        self.cog = cog
        self.value = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("-# not your confirmation", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="disown", style=discord.ButtonStyle.danger, emoji="💔")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True
        await self.cog.delete_adoption(self.author.id, self.child_id)
        await interaction.response.edit_message(
            content=f"-# you have disowned <@{self.child_id}> 💔",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await interaction.response.edit_message(
            content="-# disown cancelled",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="-# disown confirmation timed out", view=self)
            except discord.HTTPException:
                pass
        self.stop()


# ── Emancipate Confirmation View ───────────────────────────────────
class EmancipateConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member, parent_ids: list[int], cog: "Social"):
        super().__init__(timeout=30)
        self.author = author
        self.parent_ids = parent_ids
        self.cog = cog
        self.value = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("-# not your confirmation", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="emancipate", style=discord.ButtonStyle.danger, emoji="🏃")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True
        for p in self.parent_ids:
            await self.cog.delete_adoption(p, self.author.id)
        await interaction.response.edit_message(
            content="-# you have emancipated and left your family lineage ✦",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await interaction.response.edit_message(
            content="-# emancipation cancelled",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="-# emancipation confirmation timed out", view=self)
            except discord.HTTPException:
                pass
        self.stop()


# ── Tree Pagination View ───────────────────────────────────────────
class TreePaginationView(discord.ui.View):
    def __init__(self, author: discord.Member, pages: list[str], title: str, color: int):
        super().__init__(timeout=60)
        self.author = author
        self.pages = pages
        self.title = title
        self.color = color
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.btn_prev.disabled = (self.current_page == 0)
        self.btn_next.disabled = (self.current_page >= len(self.pages) - 1)
        self.btn_indicator.label = f"{self.current_page + 1} / {len(self.pages)}"

    def get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description=f"```\n{self.pages[self.current_page]}\n```",
            color=self.color,
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("-# not your menu", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def btn_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        self.stop()


# ── Interactive Rock-Paper-Scissors View ──────────────────────────
class RPSView(discord.ui.View):
    CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

    def __init__(self, author: discord.Member, opponent: discord.Member | None = None):
        super().__init__(timeout=45)
        self.author = author
        self.opponent = opponent
        self.p1_choice: str | None = None
        self.p2_choice: str | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = [self.author.id]
        if self.opponent:
            allowed.append(self.opponent.id)
        if interaction.user.id not in allowed:
            await interaction.response.send_message("-# not your game", ephemeral=True)
            return False
        return True

    async def _handle_choice(self, interaction: discord.Interaction, choice: str):
        if self.opponent is None:
            # Solo vs Bot
            bot_choice = random.choice(list(self.CHOICES.keys()))
            for item in self.children:
                item.disabled = True

            if choice == bot_choice:
                res = f"tie — both chose {self.CHOICES[choice]}"
            elif self.BEATS[choice] == bot_choice:
                res = f"you won — {self.CHOICES[choice]} beats {self.CHOICES[bot_choice]}"
            else:
                res = f"you lost — {self.CHOICES[bot_choice]} beats {self.CHOICES[choice]}"

            await interaction.response.edit_message(content=f"-# {res}", view=self)
            self.stop()
            return

        # 2-player mode
        if interaction.user.id == self.author.id:
            if self.p1_choice is not None:
                return await interaction.response.send_message("-# you already picked", ephemeral=True)
            self.p1_choice = choice
            await interaction.response.send_message(f"-# you selected {self.CHOICES[choice]}", ephemeral=True)
        else:
            if self.p2_choice is not None:
                return await interaction.response.send_message("-# you already picked", ephemeral=True)
            self.p2_choice = choice
            await interaction.response.send_message(f"-# you selected {self.CHOICES[choice]}", ephemeral=True)

        if self.p1_choice and self.p2_choice:
            for item in self.children:
                item.disabled = True

            c1, c2 = self.p1_choice, self.p2_choice
            e1, e2 = self.CHOICES[c1], self.CHOICES[c2]

            if c1 == c2:
                winner_text = f"tie — both picked {e1}"
            elif self.BEATS[c1] == c2:
                winner_text = f"{self.author.mention} won ({e1} beats {e2})"
            else:
                winner_text = f"{self.opponent.mention} won ({e2} beats {e1})"

            if self.message:
                try:
                    await self.message.edit(content=f"-# {winner_text}", view=self)
                except discord.HTTPException:
                    pass
            self.stop()

    @discord.ui.button(label="rock", style=discord.ButtonStyle.secondary, emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "rock")

    @discord.ui.button(label="paper", style=discord.ButtonStyle.secondary, emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "paper")

    @discord.ui.button(label="scissors", style=discord.ButtonStyle.secondary, emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "scissors")


# ── Interactive Trivia View ────────────────────────────────────────
class TriviaView(discord.ui.View):
    def __init__(self, author: discord.Member, correct_answer: str, all_options: list[str]):
        super().__init__(timeout=25)
        self.author = author
        self.correct_answer = correct_answer
        self.answered = False

        for opt in all_options:
            btn = discord.ui.Button(
                label=opt[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"trivia_{opt}",
            )
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

    def _make_callback(self, chosen: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("-# not your trivia question", ephemeral=True)
            if self.answered:
                return
            self.answered = True
            for item in self.children:
                item.disabled = True
                if getattr(item, "label", "") == self.correct_answer:
                    item.style = discord.ButtonStyle.success
                elif getattr(item, "label", "") == chosen:
                    item.style = discord.ButtonStyle.danger

            if chosen == self.correct_answer:
                msg = f"-# correct — **{self.correct_answer}**"
            else:
                msg = f"-# wrong — the correct answer was **{self.correct_answer}**"

            await interaction.response.edit_message(content=msg, view=self)
            self.stop()

        return callback


# ── Would You Rather View ──────────────────────────────────────────
class WYRView(discord.ui.View):
    def __init__(self, opt1: str, opt2: str):
        super().__init__(timeout=120)
        self.opt1 = opt1
        self.opt2 = opt2
        self.votes1: set[int] = set()
        self.votes2: set[int] = set()

    def _render_content(self) -> str:
        t1 = len(self.votes1)
        t2 = len(self.votes2)
        total = t1 + t2
        p1 = round((t1 / total) * 100) if total > 0 else 50
        p2 = 100 - p1 if total > 0 else 50
        return (
            f"**would you rather...**\n\n"
            f"🅰️ **{self.opt1}** — `{p1}%` ({t1} votes)\n"
            f"🅱️ **{self.opt2}** — `{p2}%` ({t2} votes)"
        )

    @discord.ui.button(label="option a", style=discord.ButtonStyle.secondary, emoji="🅰️")
    async def btn_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes2.discard(uid)
        self.votes1.add(uid)
        await interaction.response.edit_message(content=self._render_content(), view=self)

    @discord.ui.button(label="option b", style=discord.ButtonStyle.secondary, emoji="🅱️")
    async def btn_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes1.discard(uid)
        self.votes2.add(uid)
        await interaction.response.edit_message(content=self._render_content(), view=self)


# ── The Social Cog ─────────────────────────────────────────────────
class Social(commands.Cog, name="Social"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: aiosqlite.Connection | None = None
        self._active_proposals: dict[int, float] = {}  # user_id -> monotonic expiration timestamp
        self._kinship_cache: dict[tuple[int, int, str], tuple[str, float]] = {}

    async def cog_load(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                married_at TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                PRIMARY KEY (user1_id, user2_id)
            )
        """)
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_marriages_u1 ON marriages(user1_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_marriages_u2 ON marriages(user2_id)")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS proposal_stats (
                user_id INTEGER PRIMARY KEY,
                sent_count INTEGER NOT NULL DEFAULT 0,
                received_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                declined_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS adoptions (
                parent_id INTEGER NOT NULL,
                child_id INTEGER NOT NULL,
                adopted_at TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            )
        """)
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_adoptions_parent ON adoptions(parent_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_adoptions_child ON adoptions(child_id)")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS family_milestones (
                user_id INTEGER NOT NULL,
                milestone_type TEXT NOT NULL,
                achieved_at TEXT NOT NULL,
                PRIMARY KEY (user_id, milestone_type)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS family_names (
                user_id INTEGER PRIMARY KEY,
                family_name TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS social_optout (
                user_id INTEGER PRIMARY KEY,
                opted_out_at TEXT NOT NULL
            )
        """)
        await self.db.commit()

    async def cog_unload(self):
        if self.db:
            await self.db.close()
            self.db = None

    # ── State Lock & Cache Helpers ─────────────────────────────────
    def _is_locked(self, user_id: int) -> bool:
        now = time.monotonic()
        self._active_proposals = {uid: exp for uid, exp in self._active_proposals.items() if exp > now}
        return user_id in self._active_proposals

    def _acquire_locks(self, *user_ids: int, ttl: float = 65.0):
        now = time.monotonic()
        exp = now + ttl
        for uid in user_ids:
            self._active_proposals[uid] = exp

    def _release_locks(self, *user_ids: int):
        for uid in user_ids:
            self._active_proposals.pop(uid, None)

    def _invalidate_kinship_cache(self):
        self._kinship_cache.clear()

    # ── Privacy Opt-Out ─────────────────────────────────────────────
    async def is_opted_out(self, user_id: int) -> bool:
        if not self.db:
            return False
        async with self.db.execute("SELECT 1 FROM social_optout WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone()) is not None

    async def set_optout(self, user_id: int, optout: bool):
        if not self.db:
            return
        if optout:
            now_iso = datetime.now(timezone.utc).isoformat()
            await self.db.execute("INSERT OR REPLACE INTO social_optout (user_id, opted_out_at) VALUES (?, ?)", (user_id, now_iso))
        else:
            await self.db.execute("DELETE FROM social_optout WHERE user_id = ?", (user_id,))
        await self.db.commit()

    # ── Avatar & Formatting Helpers ────────────────────────────────
    async def _fetch_avatar(self, user: discord.abc.User | discord.Member | None) -> bytes | None:
        if user is None:
            return None
        try:
            url = str(user.display_avatar.with_format("png").with_size(256).url)
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        return await r.read()
        except Exception as e:
            log.warning(f"failed to fetch avatar for {getattr(user, 'id', None)}: {e}")
        return None

    async def _get_user_display(self, user_id: int, guild: discord.Guild | None = None) -> tuple[str, str]:
        member = guild.get_member(user_id) if guild else None
        if member:
            return member.display_name, f"@{member.name}"
        u = self.bot.get_user(user_id)
        if u:
            return u.display_name, f"@{u.name}"
        try:
            u = await self.bot.fetch_user(user_id)
            if u:
                return u.display_name, f"@{u.name}"
        except Exception:
            pass
        return f"User {user_id}", f"@{user_id}"

    # ── Marriage DB Methods ─────────────────────────────────────────
    async def get_marriage(self, user_id: int) -> tuple[int, str, int] | None:
        """Returns (partner_id, married_at_iso, guild_id) or None."""
        if not self.db:
            return None
        async with self.db.execute(
            "SELECT user2_id, married_at, guild_id FROM marriages WHERE user1_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2]

        async with self.db.execute(
            "SELECT user1_id, married_at, guild_id FROM marriages WHERE user2_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2]
        return None

    async def create_marriage(self, u1: int, u2: int, guild_id: int):
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO marriages (user1_id, user2_id, married_at, guild_id) VALUES (?, ?, ?, ?)",
            (u1, u2, now_iso, guild_id),
        )
        await self.db.commit()
        self._invalidate_kinship_cache()

    async def delete_marriage(self, user_id: int):
        if not self.db:
            return
        await self.db.execute(
            "DELETE FROM marriages WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id),
        )
        await self.db.commit()
        self._invalidate_kinship_cache()

    # ── Adoption & Lineage DB Methods ──────────────────────────────
    async def create_adoption(self, parent_id: int, child_id: int, guild_id: int):
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO adoptions (parent_id, child_id, adopted_at, guild_id) VALUES (?, ?, ?, ?)",
            (parent_id, child_id, now_iso, guild_id),
        )
        await self.db.commit()
        self._invalidate_kinship_cache()

    async def delete_adoption(self, parent_id: int, child_id: int):
        if not self.db:
            return
        await self.db.execute(
            "DELETE FROM adoptions WHERE parent_id = ? AND child_id = ?",
            (parent_id, child_id),
        )
        await self.db.commit()
        self._invalidate_kinship_cache()

    async def get_parents(self, child_id: int) -> list[int]:
        if not self.db:
            return []
        async with self.db.execute(
            "SELECT parent_id FROM adoptions WHERE child_id = ? ORDER BY adopted_at ASC",
            (child_id,),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def get_children(self, parent_id: int) -> list[int]:
        if not self.db:
            return []
        async with self.db.execute(
            "SELECT child_id FROM adoptions WHERE parent_id = ? ORDER BY adopted_at ASC",
            (parent_id,),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def get_siblings(self, user_id: int) -> list[int]:
        parents = await self.get_parents(user_id)
        if not parents:
            return []
        siblings = set()
        for p in parents:
            for c in await self.get_children(p):
                if c != user_id:
                    siblings.add(c)
        return sorted(siblings)

    async def is_ancestor(self, potential_ancestor: int, target: int, visited: set[int] = None) -> bool:
        if visited is None:
            visited = set()
        if target in visited:
            return False
        visited.add(target)
        target_parents = await self.get_parents(target)
        if potential_ancestor in target_parents:
            return True
        for p in target_parents:
            if await self.is_ancestor(potential_ancestor, p, visited):
                return True
        return False

    async def is_descendant(self, potential_descendant: int, target: int, visited: set[int] = None) -> bool:
        if visited is None:
            visited = set()
        if target in visited:
            return False
        visited.add(target)
        target_children = await self.get_children(target)
        if potential_descendant in target_children:
            return True
        for c in target_children:
            if await self.is_descendant(potential_descendant, c, visited):
                return True
        return False

    async def is_ancestor_or_descendant(self, u1: int, u2: int) -> bool:
        return (await self.is_ancestor(u1, u2)) or (await self.is_descendant(u1, u2))

    # ── Kinship Resolver ────────────────────────────────────────────
    async def resolve_relationship(self, u1: int, u2: int, mode: str = "combined") -> str:
        if u1 == u2:
            return "Self"

        # Check direct spouse
        m1 = await self.get_marriage(u1)
        if m1 and m1[0] == u2:
            return "Married (Spouse)"

        cache_key = (u1, u2, mode)
        cached = self._kinship_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]

        u1_parents = set(await self.get_parents(u1))
        u2_parents = set(await self.get_parents(u2))
        u1_children = set(await self.get_children(u1))
        u2_children = set(await self.get_children(u2))

        # 1. Direct Parent / Child
        if u2 in u1_children:
            res = "Child"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res
        if u1 in u2_children:
            res = "Parent"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res

        # 2. Siblings (Shared parent)
        if u1_parents and u2_parents and (u1_parents & u2_parents):
            res = "Sibling"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res

        # 3. Grandparent / Grandchild
        for p in u2_parents:
            p_parents = set(await self.get_parents(p))
            if u1 in p_parents:
                res = "Grandparent"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res
        for p in u1_parents:
            p_parents = set(await self.get_parents(p))
            if u2 in p_parents:
                res = "Grandchild"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res

        # 4. Aunt/Uncle vs Niece/Nephew
        for p in u2_parents:
            p_parents = set(await self.get_parents(p))
            if p_parents and (p_parents & u1_parents):
                res = "Aunt/Uncle"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res
        for p in u1_parents:
            p_parents = set(await self.get_parents(p))
            if p_parents and (p_parents & u2_parents):
                res = "Niece/Nephew"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res

        # 5. First Cousins
        u1_grandparents = set()
        for p in u1_parents:
            u1_grandparents.update(await self.get_parents(p))
        u2_grandparents = set()
        for p in u2_parents:
            u2_grandparents.update(await self.get_parents(p))
        if u1_grandparents and u2_grandparents and (u1_grandparents & u2_grandparents):
            res = "Cousin"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res

        if mode == "blood":
            if await self.is_ancestor(u1, u2):
                res = "Ancestor"
            elif await self.is_descendant(u1, u2):
                res = "Descendant"
            else:
                res = "Unrelated"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res

        # In combined mode, check in-law and step relations
        m2 = await self.get_marriage(u2)
        u1_spouse = m1[0] if m1 else None
        u2_spouse = m2[0] if m2 else None

        # Parent-in-law / Child-in-law
        if u1_spouse and u2 in await self.get_parents(u1_spouse):
            res = "Parent-in-law"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res
        if u2_spouse and u2_spouse in u1_children:
            res = "Child-in-law"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res

        # Sibling-in-law
        if u1_spouse:
            u1_sp_parents = set(await self.get_parents(u1_spouse))
            if u1_sp_parents and (u1_sp_parents & u2_parents):
                res = "Sibling-in-law"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res
        if u2_spouse:
            u2_sp_parents = set(await self.get_parents(u2_spouse))
            if u2_sp_parents and (u2_sp_parents & u1_parents):
                res = "Sibling-in-law"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res

        # Step-child / Step-parent
        if u1_spouse and u2 in await self.get_children(u1_spouse):
            res = "Step-Child"
            self._kinship_cache[cache_key] = (res, now + 120)
            return res
        for p in u1_parents:
            pm = await self.get_marriage(p)
            if pm and pm[0] == u2:
                res = "Step-Parent"
                self._kinship_cache[cache_key] = (res, now + 120)
                return res

        # Deep search across all connections (up to depth 6)
        network = await self.get_family_network(u1, max_depth=6)
        res = "Extended Family" if u2 in network else "Unrelated"
        self._kinship_cache[cache_key] = (res, now + 120)
        return res

    async def get_family_network(self, user_id: int, max_depth: int = 6) -> set[int]:
        if not self.db:
            return {user_id}
        visited = {user_id}
        q = deque([(user_id, 0)])
        while q:
            curr, depth = q.popleft()
            if depth >= max_depth:
                continue
            parents = await self.get_parents(curr)
            children = await self.get_children(curr)
            m = await self.get_marriage(curr)
            neighbors = set(parents) | set(children)
            if m:
                neighbors.add(m[0])
            for n in neighbors:
                if n not in visited:
                    visited.add(n)
                    q.append((n, depth + 1))
        return visited

    async def get_family_size(self, user_id: int) -> int:
        return len(await self.get_family_network(user_id, max_depth=6))

    async def get_family_name(self, user_id: int) -> str | None:
        if not self.db:
            return None
        async with self.db.execute("SELECT family_name FROM family_names WHERE user_id = ?", (user_id,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def set_family_name(self, user_id: int, name: str):
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO family_names (user_id, family_name, set_at) VALUES (?, ?, ?)",
            (user_id, name, now_iso),
        )
        await self.db.commit()

    # ── Proposal Statistics DB Methods ─────────────────────────────
    async def get_proposal_stats(self, user_id: int) -> tuple[int, int, int, int]:
        if not self.db:
            return 0, 0, 0, 0
        async with self.db.execute(
            "SELECT sent_count, received_count, accepted_count, declined_count FROM proposal_stats WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
        return 0, 0, 0, 0

    async def record_proposal(self, sender_id: int, target_id: int):
        if not self.db:
            return
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, 1, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET sent_count = sent_count + 1
        """, (sender_id,))
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, 0, 1, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET received_count = received_count + 1
        """, (target_id,))
        await self.db.commit()

    async def record_proposal_result(self, sender_id: int, target_id: int, accepted: bool):
        if not self.db:
            return
        col = "accepted_count" if accepted else "declined_count"
        for uid in (sender_id, target_id):
            await self.db.execute(f"""
                INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
                VALUES (?, 0, 0, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET {col} = {col} + 1
            """, (uid, 1 if accepted else 0, 1 if not accepted else 0))
        await self.db.commit()

    async def set_proposal_stats(self, user_id: int, sent: int, received: int):
        if not self.db:
            return
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, ?, ?, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET sent_count = ?, received_count = ?
        """, (user_id, sent, received, sent, received))
        await self.db.commit()

    # ── Tree Lineage Builder Helper ────────────────────────────────
    async def _build_tree_pages(self, user_id: int, guild: discord.Guild | None) -> list[str]:
        lines = []
        user_name, _ = await self._get_user_display(user_id, guild)
        m = await self.get_marriage(user_id)
        spouse_id = m[0] if m else None
        if spouse_id:
            sp_name, _ = await self._get_user_display(spouse_id, guild)
            spouse_name = f" 💍 {sp_name}"
        else:
            spouse_name = ""

        # 1. Parents & Grandparents
        user_parents = await self.get_parents(user_id)
        if user_parents:
            lines.append("Ancestors ✦")
            for i, p in enumerate(user_parents):
                is_last_p = (i == len(user_parents) - 1)
                p_prefix = "└── " if is_last_p else "├── "
                p_name, _ = await self._get_user_display(p, guild)
                pm = await self.get_marriage(p)
                p_spouse = pm[0] if pm else None
                if p_spouse and p_spouse not in user_parents:
                    psp_name, _ = await self._get_user_display(p_spouse, guild)
                    p_sp_str = f" 💍 {psp_name}"
                else:
                    p_sp_str = ""
                lines.append(f"{p_prefix}{p_name}{p_sp_str} (Parent)")

                # Grandparents
                p_parents = await self.get_parents(p)
                for j, gp in enumerate(p_parents):
                    is_last_gp = (j == len(p_parents) - 1)
                    sub_prefix = "    " if is_last_p else "│   "
                    gp_branch = "└── " if is_last_gp else "├── "
                    gp_name, _ = await self._get_user_display(gp, guild)
                    lines.append(f"{sub_prefix}{gp_branch}{gp_name} (Grandparent)")
            lines.append("│")

        # 2. Main Focus & Spouse
        fam_name = await self.get_family_name(user_id)
        dynasty_str = f" [House of {fam_name}]" if fam_name else ""
        lines.append(f"● {user_name}{spouse_name}{dynasty_str}")

        # 3. Siblings
        siblings = await self.get_siblings(user_id)
        if siblings:
            lines.append("│")
            lines.append("├── Siblings ✦")
            for s in siblings:
                s_name, _ = await self._get_user_display(s, guild)
                sm = await self.get_marriage(s)
                s_sp = sm[0] if sm else None
                s_sp_str = ""
                if s_sp:
                    s_sp_name, _ = await self._get_user_display(s_sp, guild)
                    s_sp_str = f" 💍 {s_sp_name}"
                lines.append(f"│   ├── {s_name}{s_sp_str}")

        # 4. Children & Grandchildren
        user_children = await self.get_children(user_id)
        if spouse_id:
            for sc in await self.get_children(spouse_id):
                if sc not in user_children:
                    user_children.append(sc)

        if user_children:
            lines.append("│")
            lines.append("└── Children ✦")
            for i, c in enumerate(user_children):
                is_last_c = (i == len(user_children) - 1)
                c_branch = "└── " if is_last_c else "├── "
                c_name, _ = await self._get_user_display(c, guild)
                cm = await self.get_marriage(c)
                c_spouse = cm[0] if cm else None
                c_sp_str = ""
                if c_spouse:
                    c_sp_name, _ = await self._get_user_display(c_spouse, guild)
                    c_sp_str = f" 💍 {c_sp_name}"
                lines.append(f"    {c_branch}{c_name}{c_sp_str}")

                # Grandchildren
                c_children = await self.get_children(c)
                if c_spouse:
                    for gc in await self.get_children(c_spouse):
                        if gc not in c_children:
                            c_children.append(gc)
                for j, gc in enumerate(c_children):
                    is_last_gc = (j == len(c_children) - 1)
                    sub_indent = "    " if is_last_c else "│   "
                    gc_branch = "└── " if is_last_gc else "├── "
                    gc_name, _ = await self._get_user_display(gc, guild)
                    lines.append(f"    {sub_indent}{gc_branch}{gc_name}")

        full_text = "\n".join(lines)
        if len(full_text) <= 1800:
            return [full_text]

        # Paginate lines if long
        pages = []
        cur_page = []
        cur_len = 0
        for line in lines:
            if cur_len + len(line) + 1 > 1800:
                pages.append("\n".join(cur_page))
                cur_page = [line]
                cur_len = len(line)
            else:
                cur_page.append(line)
                cur_len += len(line) + 1
        if cur_page:
            pages.append("\n".join(cur_page))
        return pages

    # ── Marriage Commands ───────────────────────────────────────────
    @commands.command(name="marry", aliases=["propose"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    @help_meta(
        usage="`.marry <@user>`",
        desc="Proposes to another server member with an interactive wedding proposal.",
        section="Fun",
        perm_tier="public",
        examples=[".marry @someone"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to propose to."},
        ],
        note="Target user must click accept within 60 seconds.",
    )
    async def marry(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if user is None:
            return await ctx.send("-# usage: `.marry <@user>`")
        if user.id == ctx.author.id:
            return await ctx.send("-# you can't marry yourself")
        if user.bot:
            return await ctx.send("-# you can't marry a bot")

        # Opt-out checks
        if await self.is_opted_out(ctx.author.id):
            return await ctx.send("-# you have opted out of social proposals. use `.optout` to re-enable")
        if await self.is_opted_out(user.id):
            return await ctx.send(f"-# {user.display_name} has opted out of social proposals")

        m1 = await self.get_marriage(ctx.author.id)
        if m1:
            return await ctx.send(f"-# you're already married to <@{m1[0]}>. use `.divorce` first")

        m2 = await self.get_marriage(user.id)
        if m2:
            return await ctx.send(f"-# {user.display_name} is already married to <@{m2[0]}>")

        # Kinship blocker (direct bloodline check)
        rel = await self.resolve_relationship(ctx.author.id, user.id, mode="blood")
        if rel in {"Parent", "Child", "Sibling", "Grandparent", "Grandchild", "Aunt/Uncle", "Niece/Nephew", "Ancestor", "Descendant"}:
            return await ctx.send(f"-# you cannot marry {user.display_name} ({rel.lower()} relation)")

        await self.record_proposal(ctx.author.id, user.id)

        view = MarryProposalView(ctx.author, user, self)
        msg = await ctx.send(
            f"{user.mention}, **{ctx.author.display_name}** has proposed to you 💍",
            view=view,
        )
        view.message = msg

    @commands.command(name="divorce")
    @commands.cooldown(1, 20, commands.BucketType.user)
    @help_meta(
        usage="`.divorce`",
        desc="Divorces your current married partner with safety confirmation.",
        section="Fun",
        perm_tier="public",
        examples=[".divorce"],
        params=[],
        note="Requires interactive confirmation before dissolving marriage.",
    )
    async def divorce(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        m = await self.get_marriage(ctx.author.id)
        if not m:
            return await ctx.send("-# you aren't married to anyone")

        partner_id = m[0]
        p_name, _ = await self._get_user_display(partner_id, ctx.guild)

        view = DivorceConfirmView(ctx.author, partner_id, self)
        msg = await ctx.send(
            f"-# are you sure you want to divorce **{p_name}**?",
            view=view,
        )
        view.message = msg

    @commands.command(name="marriage", aliases=["marrystatus"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.marriage [@user]`",
        desc="Displays marriage status, partner, anniversary duration, proposal statistics, and custom glass card.",
        section="Fun",
        perm_tier="public",
        examples=[".marriage", ".marriage @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def marriage(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        target = user or ctx.author
        m = await self.get_marriage(target.id)
        stats = await self.get_proposal_stats(target.id)
        sent, recv, accepted, declined = stats

        if not m:
            av_bytes = await self._fetch_avatar(target)
            card_buf = await asyncio.to_thread(
                _render_single_card,
                av_bytes,
                target.display_name,
                f"@{target.name}",
                sent,
                recv,
            )
            file = discord.File(card_buf, filename="marriage.png")
            return await ctx.send(file=file)

        partner_id, iso_str, _ = m
        try:
            married_dt = datetime.fromisoformat(iso_str)
            long_dur, short_dur, days, tier_title = _format_married_duration(married_dt)
            date_str = married_dt.strftime("%b %d, %Y")
        except Exception:
            long_dur = "Some Time"
            tier_title = "✦  E T E R N A L   V O W  ✦"
            date_str = "Recently"

        partner_name, partner_tag = await self._get_user_display(partner_id, ctx.guild)
        partner_obj = ctx.guild.get_member(partner_id) or self.bot.get_user(partner_id)

        av1_bytes = await self._fetch_avatar(target)
        av2_bytes = await self._fetch_avatar(partner_obj) if partner_obj else None

        card_buf = await asyncio.to_thread(
            _render_marriage_card,
            av1_bytes,
            av2_bytes,
            target.display_name,
            partner_name,
            f"@{target.name}",
            partner_tag,
            long_dur,
            date_str,
            sent,
            recv,
            tier_title,
        )

        file = discord.File(card_buf, filename="marriage.png")
        await ctx.send(file=file)

    @commands.command(name="marrysetstats", aliases=["setproposals"])
    @help_meta(
        usage="`.marrysetstats <@user> <sent> <received>`",
        desc="Sets the proposal statistics for a user.",
        section="Fun",
        perm_tier="whitelist",
        examples=[".marrysetstats @someone 5 12"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to update."},
            {"name": "sent", "type": "int", "required": True, "desc": "Proposals sent count."},
            {"name": "received", "type": "int", "required": True, "desc": "Proposals received count."},
        ],
        note="Server Owner / Whitelisted only.",
    )
    async def marrysetstats(self, ctx: commands.Context, user: discord.Member = None, sent: int = 0, received: int = 0):
        if not is_owner_or_creator(ctx):
            config = load_json(CONFIG_FILE)
            guild_config = config.get(str(ctx.guild.id), {}) if ctx.guild else {}
            whitelist = guild_config.get("whitelist", [])
            if str(ctx.author.id) not in {str(uid) for uid in whitelist}:
                return await ctx.send("no perms")

        if user is None:
            return await ctx.send("usage: `.marrysetstats <@user> <sent> <received>`")

        await self.set_proposal_stats(user.id, max(0, sent), max(0, received))
        try:
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        except Exception:
            await ctx.send(f"-# updated proposal stats for {user.display_name}: `{sent}` sent • `{received}` received")

    @commands.command(name="marriages", aliases=["marrylist", "marrytop"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @help_meta(
        usage="`.marriages`",
        desc="Lists the top longest lasting marriages in this server.",
        section="Fun",
        perm_tier="public",
        examples=[".marriages", ".marrytop"],
        params=[],
    )
    async def marrylist(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not self.db:
            return await ctx.send("-# no marriage records found")

        async with self.db.execute(
            "SELECT user1_id, user2_id, married_at FROM marriages WHERE guild_id = ? ORDER BY married_at ASC",
            (ctx.guild.id,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return await ctx.send("-# no marriages in this server yet")

        lines = []
        for i, (u1, u2, iso_str) in enumerate(rows[:20], 1):
            try:
                days = (datetime.now(timezone.utc) - datetime.fromisoformat(iso_str)).days
                d_str = f"{days}d"
            except Exception:
                d_str = "?"
            lines.append(f"`{i}.` 💍 <@{u1}> & <@{u2}> — `{d_str}`")

        embed = discord.Embed(
            title="server marriages",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    # ── Adoption & Family Commands ─────────────────────────────────
    @commands.command(name="adopt")
    @commands.cooldown(1, 15, commands.BucketType.user)
    @help_meta(
        usage="`.adopt <@user>`",
        desc="Sends an adoption proposal to adopt another member into your family.",
        section="Fun",
        perm_tier="public",
        examples=[".adopt @someone"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to adopt."},
        ],
        note="Target user must accept within 60 seconds.",
    )
    async def adopt(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if user is None:
            return await ctx.send("-# usage: `.adopt <@user>`")
        if user.id == ctx.author.id:
            return await ctx.send("-# you can't adopt yourself")
        if user.bot:
            return await ctx.send("-# you can't adopt a bot")

        # Opt-out checks
        if await self.is_opted_out(ctx.author.id):
            return await ctx.send("-# you have opted out of social proposals. use `.optout` to re-enable")
        if await self.is_opted_out(user.id):
            return await ctx.send(f"-# {user.display_name} has opted out of social proposals")

        # Check existing parents cap (max 2)
        parents = await self.get_parents(user.id)
        if len(parents) >= 2:
            return await ctx.send(f"-# {user.display_name} already has 2 parents")
        if ctx.author.id in parents:
            return await ctx.send(f"-# {user.display_name} is already your child")

        # Spouse check
        m = await self.get_marriage(ctx.author.id)
        if m and m[0] == user.id:
            return await ctx.send(f"-# you cannot adopt your married spouse")

        # Cycle check
        if await self.is_ancestor_or_descendant(ctx.author.id, user.id):
            return await ctx.send(f"-# you cannot adopt {user.display_name} due to family cycle")

        view = AdoptProposalView(ctx.author, user, self)
        msg = await ctx.send(
            f"{user.mention}, **{ctx.author.display_name}** wants to adopt you 🌿",
            view=view,
        )
        view.message = msg

    @commands.command(name="makeparent", aliases=["askparent"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    @help_meta(
        usage="`.makeparent <@user>`",
        desc="Asks another server member to become your parent.",
        section="Fun",
        perm_tier="public",
        examples=[".makeparent @someone"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to request as parent."},
        ],
    )
    async def makeparent(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if user is None:
            return await ctx.send("-# usage: `.makeparent <@user>`")
        if user.id == ctx.author.id:
            return await ctx.send("-# you can't be your own parent")
        if user.bot:
            return await ctx.send("-# a bot cannot be your parent")

        if await self.is_opted_out(ctx.author.id):
            return await ctx.send("-# you have opted out of social proposals. use `.optout` to re-enable")
        if await self.is_opted_out(user.id):
            return await ctx.send(f"-# {user.display_name} has opted out of social proposals")

        # Author's parent cap
        parents = await self.get_parents(ctx.author.id)
        if len(parents) >= 2:
            return await ctx.send("-# you already have 2 parents")
        if user.id in parents:
            return await ctx.send(f"-# {user.display_name} is already your parent")

        # Spouse check
        m = await self.get_marriage(ctx.author.id)
        if m and m[0] == user.id:
            return await ctx.send("-# you cannot make your spouse your parent")

        # Cycle check
        if await self.is_ancestor_or_descendant(user.id, ctx.author.id):
            return await ctx.send(f"-# you cannot make {user.display_name} your parent due to family cycle")

        view = MakeParentProposalView(ctx.author, user, self)
        msg = await ctx.send(
            f"{user.mention}, **{ctx.author.display_name}** wants you to be their parent 🌿",
            view=view,
        )
        view.message = msg

    @commands.command(name="disown")
    @commands.cooldown(1, 20, commands.BucketType.user)
    @help_meta(
        usage="`.disown <@user>`",
        desc="Disowns an adopted child with safety confirmation.",
        section="Fun",
        perm_tier="public",
        examples=[".disown @someone"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Child to disown."},
        ],
    )
    async def disown(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if user is None:
            return await ctx.send("-# usage: `.disown <@user>`")

        children = await self.get_children(ctx.author.id)
        if user.id not in children:
            return await ctx.send(f"-# {user.display_name} is not your child")

        view = DisownConfirmView(ctx.author, user.id, self)
        msg = await ctx.send(
            f"-# are you sure you want to disown **{user.display_name}**?",
            view=view,
        )
        view.message = msg

    @commands.command(name="emancipate", aliases=["runaway", "leavefamily"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    @help_meta(
        usage="`.emancipate`",
        desc="Emancipate and remove yourself from your parent(s) lineage.",
        section="Fun",
        perm_tier="public",
        examples=[".emancipate", ".runaway"],
        params=[],
    )
    async def emancipate(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        parents = await self.get_parents(ctx.author.id)
        if not parents:
            return await ctx.send("-# you do not have any parents")

        view = EmancipateConfirmView(ctx.author, parents, self)
        msg = await ctx.send(
            "-# are you sure you want to emancipate from your parents?",
            view=view,
        )
        view.message = msg

    @commands.command(name="parents", aliases=["parent"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.parents [@user]`",
        desc="Displays the parent(s) of a member.",
        section="Fun",
        perm_tier="public",
        examples=[".parents", ".parents @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def parents(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        parents = await self.get_parents(target.id)
        if not parents:
            return await ctx.send(f"-# **{target.display_name}** has no parents")

        p_mentions = [f"<@{p}>" for p in parents]
        await ctx.send(f"-# **{target.display_name}**'s parents: {' & '.join(p_mentions)} ✦")

    @commands.command(name="children", aliases=["child", "kids"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.children [@user]`",
        desc="Displays the children of a member.",
        section="Fun",
        perm_tier="public",
        examples=[".children", ".children @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def children(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        children = await self.get_children(target.id)
        if not children:
            return await ctx.send(f"-# **{target.display_name}** has no children")

        c_mentions = [f"<@{c}>" for c in children]
        await ctx.send(f"-# **{target.display_name}**'s children ({len(children)}): {', '.join(c_mentions)} ✦")

    @commands.command(name="siblings", aliases=["sibling", "bros"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.siblings [@user]`",
        desc="Displays the siblings of a member.",
        section="Fun",
        perm_tier="public",
        examples=[".siblings", ".siblings @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def siblings(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        siblings = await self.get_siblings(target.id)
        if not siblings:
            return await ctx.send(f"-# **{target.display_name}** has no siblings")

        s_mentions = [f"<@{s}>" for s in siblings]
        await ctx.send(f"-# **{target.display_name}**'s siblings ({len(siblings)}): {', '.join(s_mentions)} ✦")

    @commands.command(name="family", aliases=["fam"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.family [@user]`",
        desc="Displays a complete family summary overview for a member.",
        section="Fun",
        perm_tier="public",
        examples=[".family", ".family @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def family(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        m = await self.get_marriage(target.id)
        parents = await self.get_parents(target.id)
        if len(parents) == 1:
            p_m = await self.get_marriage(parents[0])
            if p_m and p_m[0] not in parents and p_m[0] != target.id:
                parents.append(p_m[0])

        children = await self.get_children(target.id)
        siblings = await self.get_siblings(target.id)
        fam_size = await self.get_family_size(target.id)
        fam_name = await self.get_family_name(target.id)

        lines = []
        if fam_name:
            lines.append(f"**House of {fam_name}** ✦\n")

        # Spouse
        if m:
            sp_name, _ = await self._get_user_display(m[0], ctx.guild)
            try:
                days = (datetime.now(timezone.utc) - datetime.fromisoformat(m[1])).days
                d_str = f" • `{days}d`"
            except Exception:
                d_str = ""
            lines.append(f"💍 **Spouse:** <@{m[0]}>{d_str}")
        else:
            lines.append("💍 **Spouse:** None")

        # Parents
        if parents:
            p_str = ", ".join(f"<@{p}>" for p in parents)
            lines.append(f"🌿 **Parents:** {p_str}")
        else:
            lines.append("🌿 **Parents:** None")

        # Children
        if children:
            c_str = ", ".join(f"<@{c}>" for c in children[:8])
            if len(children) > 8:
                c_str += f" *(+{len(children)-8} more)*"
            lines.append(f"👶 **Children ({len(children)}):** {c_str}")
        else:
            lines.append("👶 **Children:** None")

        # Siblings
        if siblings:
            s_str = ", ".join(f"<@{s}>" for s in siblings[:8])
            if len(siblings) > 8:
                s_str += f" *(+{len(siblings)-8} more)*"
            lines.append(f"👥 **Siblings ({len(siblings)}):** {s_str}")

        lines.append(f"\n*Total Family Size: `{fam_size}` member{'s' if fam_size != 1 else ''}*")

        embed = discord.Embed(
            title=f"{target.display_name}'s family",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="tree", aliases=["familytree", "lineage"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.tree [@user]`",
        desc="Generates a customized visual family lineage graphic card.",
        section="Fun",
        perm_tier="public",
        examples=[".tree", ".tree @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to view tree for. Defaults to you."},
        ],
    )
    async def tree(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        m = await self.get_marriage(target.id)
        spouse_id = m[0] if m else None
        parent_ids = await self.get_parents(target.id)
        if len(parent_ids) == 1:
            p_m = await self.get_marriage(parent_ids[0])
            if p_m and p_m[0] not in parent_ids and p_m[0] != target.id:
                parent_ids.append(p_m[0])

        # Grandparents (parents of parents)
        grandparent_ids = []
        for pid in parent_ids[:2]:
            gp_list = await self.get_parents(pid)
            for gp in gp_list:
                if gp not in grandparent_ids:
                    grandparent_ids.append(gp)

        child_ids = await self.get_children(target.id)
        if spouse_id:
            for sc in await self.get_children(spouse_id):
                if sc not in child_ids:
                    child_ids.append(sc)
        sibling_ids = await self.get_siblings(target.id)
        fam_name = await self.get_family_name(target.id)

        async def fetch_node(uid: int, role: str) -> TreeNode:
            name, tag = await self._get_user_display(uid, ctx.guild)
            member_obj = ctx.guild.get_member(uid) or self.bot.get_user(uid) if ctx.guild else self.bot.get_user(uid)
            if member_obj is None:
                try:
                    member_obj = await self.bot.fetch_user(uid)
                except Exception:
                    pass
            av_bytes = await self._fetch_avatar(member_obj)
            return TreeNode(uid, name, tag, role, av_bytes)

        focus_node = await fetch_node(target.id, "Focus")
        spouse_node = await fetch_node(spouse_id, "Spouse") if spouse_id else None

        parent_nodes = await asyncio.gather(*[fetch_node(pid, "Parent") for pid in parent_ids[:2]])
        grandparent_nodes = await asyncio.gather(*[fetch_node(gpid, "Grandparent") for gpid in grandparent_ids[:4]])
        sibling_nodes = await asyncio.gather(*[fetch_node(sid, "Sibling") for sid in sibling_ids[:3]])

        # Build children trees (with their spouses and grandchildren)
        children_trees = []
        for cid in child_ids[:4]:
            c_node = await fetch_node(cid, "Child")
            c_m = await self.get_marriage(cid)
            if c_m:
                c_node.spouse = await fetch_node(c_m[0], "In-law")
            grandkids = await self.get_children(cid)
            if grandkids:
                c_node.children = await asyncio.gather(*[fetch_node(gkid, "Grandchild") for gkid in grandkids[:3]])
            children_trees.append(c_node)

        card_buf = await asyncio.to_thread(
            _render_tree_card,
            focus_node,
            spouse_node,
            list(parent_nodes),
            list(grandparent_nodes),
            children_trees,
            list(sibling_nodes),
            fam_name,
        )
        file = discord.File(card_buf, filename="tree.png")
        await ctx.send(file=file)

    @commands.command(name="relationship", aliases=["rel", "relation"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.relationship <@user1> [user2]`",
        desc="Calculates and explains the exact family kinship relation between two members.",
        section="Fun",
        perm_tier="public",
        examples=[".relationship @someone", ".relationship @user1 @user2"],
        params=[
            {"name": "user1", "type": "user", "required": True, "desc": "First member."},
            {"name": "user2", "type": "user", "required": False, "desc": "Second member. Defaults to you."},
        ],
    )
    async def relationship(self, ctx: commands.Context, user1: discord.Member = None, user2: discord.Member = None):
        if user1 is None:
            return await ctx.send("-# usage: `.relationship <@user1> [user2]`")

        if user2 is None:
            u1, u2 = ctx.author, user1
        else:
            u1, u2 = user1, user2

        if u1.id == u2.id:
            return await ctx.send("-# they are the same person")

        blood_rel = await self.resolve_relationship(u1.id, u2.id, mode="blood")
        combined_rel = await self.resolve_relationship(u1.id, u2.id, mode="combined")

        if blood_rel != "Unrelated":
            desc = f"**{u1.display_name}** is **{u2.display_name}**'s **{blood_rel}** (Bloodline) ✦"
        elif combined_rel != "Unrelated":
            desc = f"**{u1.display_name}** is **{u2.display_name}**'s **{combined_rel}** (Combined Family) ✦"
        else:
            desc = f"**{u1.display_name}** and **{u2.display_name}** are not related."

        await ctx.send(f"-# {desc}")

    @commands.command(name="familysize")
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.familysize [@user]`",
        desc="Calculates total number of connected relatives in a member's family network.",
        section="Fun",
        perm_tier="public",
        examples=[".familysize", ".familysize @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def familysize(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        size = await self.get_family_size(target.id)
        await ctx.send(f"-# **{target.display_name}**'s family network contains **{size}** connected member{'s' if size != 1 else ''} ✦")

    @commands.command(name="familytop", aliases=["famtop", "topfamilies"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @help_meta(
        usage="`.familytop`",
        desc="Leaderboard of the largest family dynasties in this server.",
        section="Fun",
        perm_tier="public",
        examples=[".familytop"],
        params=[],
    )
    async def familytop(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not self.db:
            return await ctx.send("-# no family data available")

        # Collect unique users in this guild with relations
        member_ids = {m.id for m in ctx.guild.members if not m.bot}
        if not member_ids:
            return await ctx.send("-# no members found")

        # Compute sizes for members with at least 1 relationship
        family_sizes: list[tuple[int, int]] = []
        seen_roots = set()

        for mid in member_ids:
            if mid in seen_roots:
                continue
            parents = await self.get_parents(mid)
            children = await self.get_children(mid)
            m = await self.get_marriage(mid)
            if parents or children or m:
                net = await self.get_family_network(mid, max_depth=6)
                seen_roots.update(net)
                family_sizes.append((mid, len(net)))

        if not family_sizes:
            return await ctx.send("-# no active families in this server yet")

        family_sizes.sort(key=lambda x: x[1], reverse=True)

        lines = []
        for i, (uid, size) in enumerate(family_sizes[:10], 1):
            fam_name = await self.get_family_name(uid)
            name_str = f" (House of {fam_name})" if fam_name else ""
            lines.append(f"`{i}.` <@{uid}>{name_str} — `{size}` members")

        embed = discord.Embed(
            title="top server families ✦",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    @commands.command(name="orphans")
    @commands.cooldown(1, 15, commands.BucketType.user)
    @help_meta(
        usage="`.orphans`",
        desc="Detects and cleans up adoptions where parents are deleted Discord accounts.",
        section="Fun",
        perm_tier="public",
        examples=[".orphans"],
        params=[],
    )
    async def orphans(self, ctx: commands.Context):
        parents = await self.get_parents(ctx.author.id)
        if not parents:
            return await ctx.send("-# you do not have any parents")

        cleaned = []
        for p in parents:
            u = self.bot.get_user(p)
            if u is None:
                try:
                    u = await self.bot.fetch_user(p)
                except Exception:
                    u = None
            if u is None:
                await self.delete_adoption(p, ctx.author.id)
                cleaned.append(p)

        if cleaned:
            await ctx.send(f"-# cleaned up `{len(cleaned)}` orphaned adoption connection(s) from deleted accounts ✦")
        else:
            await ctx.send("-# all of your registered parents are active Discord accounts ✦")

    @commands.command(name="optout")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.optout`",
        desc="Toggles opting out of receiving marriage and adoption proposals.",
        section="Fun",
        perm_tier="public",
        examples=[".optout"],
        params=[],
    )
    async def optout(self, ctx: commands.Context):
        current = await self.is_opted_out(ctx.author.id)
        new_state = not current
        await self.set_optout(ctx.author.id, new_state)
        status = "disabled (opted out)" if new_state else "enabled (opted in)"
        await ctx.send(f"-# social proposals are now **{status}** for your account ✦")

    @commands.command(name="familyname", aliases=["setfamilyname", "housename"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    @help_meta(
        usage="`.familyname <name>`",
        desc="Sets a custom dynasty name / house name for your family lineage.",
        section="Fun",
        perm_tier="public",
        examples=[".familyname Phoenix", ".familyname Celestial"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Dynasty name (max 32 chars)."},
        ],
    )
    async def familyname(self, ctx: commands.Context, *, name: str = None):
        if not name:
            cur = await self.get_family_name(ctx.author.id)
            if cur:
                return await ctx.send(f"-# your current family house name is **House of {cur}**")
            return await ctx.send("-# usage: `.familyname <name>`")

        clean_name = name.strip()[:32]
        await self.set_family_name(ctx.author.id, clean_name)
        await ctx.send(f"-# your family dynasty is now established as **House of {clean_name}** ✦")

    # ── 8ball ───────────────────────────────────────────────────────
    @commands.command(name="8ball", aliases=["eightball"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.8ball <question>`",
        desc="Consult the magic 8-ball for an answer.",
        section="Fun",
        perm_tier="public",
        examples=[".8ball will today be good", ".8ball should i sleep"],
        params=[{"name": "question", "type": "str", "required": True, "desc": "Question to ask the 8ball."}],
    )
    async def eightball(self, ctx: commands.Context, *, question: str = None):
        if not question:
            return await ctx.send("-# ask a question")
        ans = random.choice(EIGHTBALL_RESPONSES)
        await ctx.send(f"-# {ans}")

    # ── Choose ──────────────────────────────────────────────────────
    @commands.command(name="choose", aliases=["pick"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.choose <option1>, <option2>, ...`",
        desc="Randomly chooses between comma-separated options.",
        section="Fun",
        perm_tier="public",
        examples=[".choose coffee, tea, boba", ".choose play valorant, watch anime"],
        params=[{"name": "options", "type": "str", "required": True, "desc": "Comma-separated list of choices."}],
    )
    async def choose(self, ctx: commands.Context, *, choices_str: str = None):
        if not choices_str:
            return await ctx.send("-# usage: `.choose <opt1>, <opt2>, ...`")
        if "," in choices_str:
            opts = [c.strip() for c in choices_str.split(",") if c.strip()]
        else:
            opts = [c.strip() for c in choices_str.split(" or ") if c.strip()]
        if len(opts) < 2:
            return await ctx.send("-# give me at least 2 choices separated by commas")
        pick = random.choice(opts)
        await ctx.send(f"-# i choose **{pick}**")

    # ── Rock Paper Scissors ─────────────────────────────────────────
    @commands.command(name="rps")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @help_meta(
        usage="`.rps [@user]`",
        desc="Play an interactive game of Rock-Paper-Scissors against the bot or another member.",
        section="Fun",
        perm_tier="public",
        examples=[".rps", ".rps @someone"],
        params=[{"name": "user", "type": "user", "required": False, "desc": "Opponent to play against. Omit to play vs bot."}],
    )
    async def rps(self, ctx: commands.Context, opponent: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if opponent and opponent.id == ctx.author.id:
            return await ctx.send("-# you can't play against yourself")
        if opponent and opponent.bot:
            return await ctx.send("-# play against me by omitting the mention: `.rps`")

        view = RPSView(ctx.author, opponent)
        if opponent:
            msg = await ctx.send(
                f"{ctx.author.mention} challenged {opponent.mention} to rock-paper-scissors — make your picks",
                view=view,
            )
        else:
            msg = await ctx.send("choose your move:", view=view)
        view.message = msg

    # ── Trivia ──────────────────────────────────────────────────────
    @commands.command(name="trivia")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.trivia`",
        desc="Generates a multiple choice trivia challenge.",
        section="Fun",
        perm_tier="public",
        examples=[".trivia"],
        params=[],
    )
    async def trivia(self, ctx: commands.Context):
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.send("-# trivia api unavailable")
                    data = await r.json()
                    results = data.get("results", [])
                    if not results:
                        return await ctx.send("-# could not load question")
                    q_data = results[0]
        except Exception:
            return await ctx.send("-# trivia api unavailable")

        question = html.unescape(q_data.get("question", ""))
        correct = html.unescape(q_data.get("correct_answer", ""))
        incorrects = [html.unescape(a) for a in q_data.get("incorrect_answers", [])]
        category = html.unescape(q_data.get("category", "General"))
        difficulty = q_data.get("difficulty", "medium")

        options = [correct] + incorrects
        random.shuffle(options)

        view = TriviaView(ctx.author, correct, options)
        embed = discord.Embed(
            title=f"trivia ({difficulty})",
            description=f"**{question}**",
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        embed.set_footer(text=f"category: {category}")
        await ctx.send(embed=embed, view=view)

    # ── Would You Rather ────────────────────────────────────────────
    @commands.command(name="wyr", aliases=["wouldyourather"])
    @commands.cooldown(1, 8, commands.BucketType.channel)
    @help_meta(
        usage="`.wyr`",
        desc="Posts a Would You Rather dilemma with live community voting buttons.",
        section="Fun",
        perm_tier="public",
        examples=[".wyr"],
        params=[],
    )
    async def wyr(self, ctx: commands.Context):
        opt1, opt2 = random.choice(WYR_QUESTIONS)
        view = WYRView(opt1, opt2)
        await ctx.send(content=view._render_content(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Social(bot))
