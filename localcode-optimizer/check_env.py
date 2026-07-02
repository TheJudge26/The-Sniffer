import os
key = os.environ.get("GEMINI_API_KEY", "")
print("GEMINI_API_KEY:", "SET" if key else "NOT SET")
print("Length        :", len(key))
print("Prefix        :", (key[:8] + "...") if len(key) > 8 else "(too short or empty)")

vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "NOT SET")
print("USE_VERTEXAI  :", vertex)
