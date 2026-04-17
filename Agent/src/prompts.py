import os

_context_path = os.path.join(os.path.dirname(__file__), "..", "pine_context.md")
with open(_context_path, "r") as _f:
    _pine_context = _f.read()


def mapping_prompt(unmapped_legacy_info: list) -> str:
    return f"""
## Awareness — Read Before Searching
{_pine_context}

---

## Your Task
You are an expert at converting legacy template variables (%[...] syntax) to Pine syntax (@[...]).

Map ALL legacy variables below to Pine syntax. Every item must appear in your output with either
a valid Pine @[...] mapping or 'No mapping found'. Do not skip or omit any item.

**Your workflow:**
1. Read the Awareness section above to understand mapping pitfalls and quirks.
2. For EACH legacy variable, search the RAG tool to find the correct Pine syntax.
3. Use the RAG results to construct the Pine @[...] expression, applying the MANDATORY RULES below.
4. If the RAG returns nothing relevant, mark the item as 'No mapping found'.

Batch your RAG searches — make MULTIPLE tool calls in a single response for performance.

Every mapping you produce MUST use @[...] delimiters. Never output %[...] or legacy prefixes.

### MANDATORY RULES — apply these even if RAG results suggest otherwise:

1. **FullName MUST split into two tokens.** Legacy `.FullName` always becomes SEPARATE first + last Pine tokens.
   NEVER use FormatName(F L) or FormatName(F M L) for FullName — that function is ONLY for signatures/closings.
   Search the RAG for the entity's individual name fields (they differ by entity category).

2. **Strip TitleCase() and UpperCase() wrappers.** In OBA/bar context, produce the plain field.
   Do NOT add .SetCasing(Title) or .SetCasing(Upper) unless you find explicit RAG evidence for that exact field.

3. **MrMs → 'No mapping found'.** Always. Never invent a replacement.

4. **OBAAttorney.Title → 'No mapping found'.** Pine typically drops this field.

5. **Prompt variables (X.X pattern) → simple @[Name] token.** Search RAG for the correct Pine name.

6. **Event dates in letter headers → prompt variable.** Do NOT produce data-bound DocumentEvents expressions.

7. **Do NOT invent conditionals** that don't exist in the legacy template.

### Legacy variables to map:
{unmapped_legacy_info}

Respond only with the mappings. Preserve the original merge field delimiters."""