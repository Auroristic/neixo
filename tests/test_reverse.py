from cogs.reverse import _resolve_source


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
