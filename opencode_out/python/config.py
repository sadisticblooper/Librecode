# Config for OpenCode.

# Compaction always uses the free endpoint (separate settings)
COMPACTION_API_URL = "https://opencode.ai/zen/v1/chat/completions"
COMPACTION_MODEL = "minimax-m2.5-free"
COMPACTION_MAX_TOKENS = 128000

HOST = "0.0.0.0"
PORT = 5000
EXA_API_KEY = ""
WORKING_DIR = ""
MAX_TOKENS = 128000

# Context compaction threshold
COMPACTION_THRESHOLD = 80_000