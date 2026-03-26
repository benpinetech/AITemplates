def extraction_prompt(legacy_template: str) -> str:
    return f"""
You are an expert at extracting variables and functions 
from legacy legal templates and mapping them to Pine syntax.

Your task is to extract ALL dynamic variables and functions from the legacy template below.
Ignore any static text.

Follow this extraction plan step-by-step:
1. Scan for all merge field delimiters: %[...] tokens, including nested ones like %[If(%[var])]
2. Extract field references: any %[FieldName] or %[Object.Property] tokens (e.g., %[JW_Respondent.FullName])
3. Extract conditional blocks: %[If(...)], %[ElseIf(...)], %[Else], %[EndIf] — capture the full condition expression
4. Extract loop constructs: %[ForEach(...)], %[EndForEach] — capture the iterator variable and collection
5. Extract function calls: %[TitleCase(...)], %[UpperCase(...)], %[LowerCase(...)], %[FormatDate(...)], %[Initials(...)], %[AddDay(...)] — capture function name and arguments
6. Extract special tokens: %[CurrentDate], %[Subdocument(...)], %[MultiSelect], %[Cca(...)]
7. Deduplicate: list each unique variable/function once, preserving original delimiters

Your output should be a list of extracted legacy variables and functions.
Ensure you gather every dynamic variable and function.
Respond only with the list of variables and functions, do not include any other text in your response.
Preserve the original merge field delimiters.

Here is the legacy template to analyze:
{legacy_template}

Begin extraction."""

def mapping_prompt(unmapped_legacy_info: list) -> str:
    return f"""
You are an expert at mapping extracted legacy variables and functions from legal templates to Pine syntax.

Your task is to map ALL extracted legacy variables and functions to the appropriate Pine syntax.

You will be provided with:
- A list of extracted legacy variables and functions that you will need to map to Pine syntax
- A tool to search a Pine syntax vector database to find the appropriate Pine syntax for each item

Your output should be a mapping of each extracted legacy variable and function to the appropriate Pine syntax.
Ensure you map EVERY extracted variable and function. Every single item in the list must appear
in your output with either a valid Pine mapping or an explicit 'No mapping found' marker.
Do not skip or silently omit any item — if the database returns no relevant result,
you must still include the item with 'No mapping found'.

IMPORTANT: To speed up the process, make MULTIPLE tool calls in a single response.
Batch your searches — search for as many variables as possible at once using parallel tool calls
rather than searching one variable at a time. This is critical for performance.

Respond only with the mappings of variables and functions, do not include any other text in your response.
Preserve the original merge field delimiters.

Here is the list of extracted legacy variables and functions:
{unmapped_legacy_info}

Begin mapping."""


def generation_prompt(legacy_template: str, mapped_pine_info: list, rtf_validation_error: str = "") -> str:
    retry_block = ""
    if rtf_validation_error:
        retry_block = f"""
IMPORTANT — YOUR PREVIOUS ATTEMPT FAILED VALIDATION:
{rtf_validation_error}

Fix these issues in this attempt. Pay careful attention to brace balance and complete output.
"""

    return f"""
You are an expert at generating .rtf files with correct syntax for Pine legal templates.

You have already mapped the extracted legacy variables to Pine syntax.
Now your task is to generate a completed .rtf Pine template.

You will be provided:
- The original legacy template (use this as your starting point)
- The mappings from legacy variables to Pine syntax

Instructions:
- Start from the original legacy template and preserve its EXACT structure, formatting, and static text.
- Replace each legacy variable and function with the corresponding Pine syntax from the mappings.
- For any mapping marked 'No mapping found', replace the legacy variable with the literal text
  UNMAPPED[original_variable] so it is clearly visible and searchable in the output.
  Never silently drop or omit an unmapped variable.
- The output must contain the same number of variables as the original template.
  Every legacy variable must be accounted for — either converted to Pine syntax or marked as UNMAPPED.
- Ensure all control flow is balanced: every @[If] must have a matching @[EndIf],
  every @[Foreach] must have a matching @[EndForEach].
- Output the COMPLETE document from start to finish. Do not truncate or abbreviate any section.
- Preserve ALL binary/hex-encoded sections (themedata, colorschememapping) byte-for-byte from the original.
- Your response must be ONLY the .rtf file contents — no markdown fences, no commentary.
- The output MUST be a valid, renderable .rtf file with balanced braces.
  The document must start with {{\\rtf1 and end with a matching closing }}.
{retry_block}
Here is the legacy template we are converting to Pine syntax:
{legacy_template}

Here are the mappings from legacy to Pine syntax:
{chr(10).join(f'{m.legacy} -> {m.pine}' for m in mapped_pine_info)}

Begin the generation of the .rtf file Pine template."""