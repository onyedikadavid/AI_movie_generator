"""A story the model returned as an object / printed dict is shown as plain text. Run: python scripts/selftest_text_clean.py"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.services.text_clean import clean_text  # noqa: E402

plain = "As the memory fades, a voice whispers.\n\nVesper finds a shard."
printed = repr({"hook": "As the memory fades, a voice whispers.\r\n\r\nVesper finds a shard."})   # what the app showed
assert printed.startswith("{'hook'") and "\\r\\n" in printed
assert clean_text(printed) == plain, clean_text(printed)                        # a printed dict (single quotes)
assert clean_text({"hook": plain}) == plain                                      # a real object
assert clean_text('{"story": "A.\\n\\nB."}') == "A.\n\nB."                      # JSON text
assert clean_text(["A.", "B."]) == "A.\n\nB."                                    # list of paragraphs
assert clean_text({"beginning": "A.", "middle": "B.", "end": "C."}) == "A.\n\nB.\n\nC."
assert clean_text("A.\\r\\n\\r\\nB.") == "A.\n\nB."                              # escaped breaks that were never real
assert clean_text("Plain story.\nSecond line.") == "Plain story.\nSecond line."  # normal text untouched
assert clean_text("He said {nothing} here.") == "He said {nothing} here."        # braces inside text are not an object
assert clean_text(None) == "" and clean_text(42) == "42"
assert clean_text({"a": {"b": ["x", {"c": "y"}]}}) == "x\n\ny"
print("selftest_text_clean OK")
