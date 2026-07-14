import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ['DISCORD_TOKEN'] = 'test_token_placeholder'
os.environ['CREATOR_ID'] = '123456789'

# Mock missing modules for offline testing
for module in ['ddgs', 'spotify_scraper']:
    sys.modules[module] = MagicMock()

