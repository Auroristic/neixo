import tomllib

try:
    with open("neixoset.toml", "rb") as f:
        _config = tomllib.load(f)
    Neixoname = _config["neixoname"]
    _embedcolor_raw = _config["embedcolor"]
    _raw_color = str(_embedcolor_raw).strip().lstrip("#")
    Neixocolor = _embedcolor_raw if isinstance(_embedcolor_raw, int) else int(_raw_color, 16)
    Neixoemojis        = _config["neixoemojis"]
    Neixogifs          = _config.get("gifs", {})
    SeoulitiesServerID = _config.get("seoulities_server_id", 1252213834370777171)
except Exception as e:
    print(f"Warning: failed to load neixoset.toml: {e}")
    Neixoname          = "Neixo"
    Neixocolor         = 0x121516
    Neixoemojis        = {}
    Neixogifs          = {}
    SeoulitiesServerID = 1252213834370777171
