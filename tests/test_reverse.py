from cogs.reverse import _parse_lens_block, _resolve_source


class _FakeAttachment:
    def __init__(self, url):
        self.url = url


class _FakeEmbedImage:
    def __init__(self, url):
        self.url = url


class _FakeEmbed:
    def __init__(self, image=None, thumbnail=None):
        self.image = image
        self.thumbnail = thumbnail


class _FakeMessage:
    def __init__(self, attachments=None, embeds=None):
        self.attachments = attachments or []
        self.embeds = embeds or []
        self.reference = None


class _FakeReference:
    def __init__(self, resolved):
        self.resolved = resolved


class _FakeCtx:
    def __init__(self, msg):
        self.message = msg


def test_resolve_explicit_url_wins():
    ctx = _FakeCtx(_FakeMessage(attachments=[_FakeAttachment("https://cdn.att")]))
    assert _resolve_source(ctx, "https://example.com/a.png") == "https://example.com/a.png"


def test_resolve_attachment():
    ctx = _FakeCtx(_FakeMessage(attachments=[_FakeAttachment("https://cdn.att")]))
    assert _resolve_source(ctx, None) == "https://cdn.att"


def test_resolve_reply_takes_first_attachment():
    ctx = _FakeCtx(_FakeMessage())
    ctx.message.reference = _FakeReference(_FakeMessage(attachments=[_FakeAttachment("https://repl.gif")]))
    assert _resolve_source(ctx, None) == "https://repl.gif"


def test_resolve_reply_embed_image():
    ctx = _FakeCtx(_FakeMessage())
    ctx.message.reference = _FakeReference(_FakeMessage(embeds=[_FakeEmbed(image=_FakeEmbedImage("https://emb.png"))]))
    assert _resolve_source(ctx, None) == "https://emb.png"


def test_resolve_reply_embed_thumbnail():
    ctx = _FakeCtx(_FakeMessage())
    ctx.message.reference = _FakeReference(_FakeMessage(embeds=[_FakeEmbed(thumbnail=_FakeEmbedImage("https://th.png"))]))
    assert _resolve_source(ctx, None) == "https://th.png"


def test_resolve_own_embed_image():
    ctx = _FakeCtx(_FakeMessage(embeds=[_FakeEmbed(image=_FakeEmbedImage("https://emb.png"))]))
    assert _resolve_source(ctx, None) == "https://emb.png"


def test_resolve_none():
    ctx = _FakeCtx(_FakeMessage())
    assert _resolve_source(ctx, None) is None


def test_lens_block_extracts_matches():
    html = (
        "AF_initDataCallback({key: 'ds:5', data:["
        '["first hit","https://danbooru.donmai.us/posts/1",null,null],'
        '["second hit","https://pixiv.net/artworks/2",null,null]'
        "], sideChannel: {}});"
    )
    out = _parse_lens_block(html, "ds:5")
    assert out[0]["text"] == "first hit"
    assert out[0]["domain"] == "danbooru.donmai.us"
    assert out[1]["url"] == "https://pixiv.net/artworks/2"


def test_lens_block_wrong_key_empty():
    html = "AF_initDataCallback({key: 'ds:5', data:[], sideChannel: {}});"
    assert _parse_lens_block(html, "ds:9") == []


def test_lens_block_bad_json_empty():
    assert _parse_lens_block("no init data here", "ds:5") == []
