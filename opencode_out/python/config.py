API_URL = "https://opencode.ai/zen/v1/chat/completions"
MODEL = "minimax-m2.5-free"
HOST = "0.0.0.0"
PORT = 5000
EXA_API_KEY = ""
WORKING_DIR = ""  # Get free key at https://dashboard.exa.ai
MAX_TOKENS = 128000  # Allow much larger responses for coding

# Context compaction settings (mirrors opencode desktop, scaled for mobile)
# Compaction triggers when estimated tokens exceed this fraction of the context window.
# The compaction module's is_overflow() applies a more precise check internally;
# this value is passed as context_limit.
COMPACTION_THRESHOLD = 80_000   # ~80k estimated tokens → compact
