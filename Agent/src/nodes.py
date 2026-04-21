from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from prompts import mapping_prompt
from striprtf.striprtf import rtf_to_text
import re
from pathlib import Path
from mappingdb import MappingDB
from state import mapping

_MAPPING_DB_DIR = str(Path(__file__).resolve().parent.parent / "mapping_db")

class MappingResponse(BaseModel):
    """List of legacy-to-Pine mappings returned by the LLM."""
    mappings: List[mapping]


def _is_hex(c: str) -> bool:
    return c in '0123456789abcdefABCDEF'


def _normalize_rtf(rtf_content: str) -> str:
    """Collapse split %[...] tokens where RTF puts % and [ in adjacent runs.

    RTF sometimes emits: ...loch\f1 %}{\rtlch...\loch\f1 \n[Token]}
    After collapsing:    ...loch\f1 %[Token]}

    This must be applied to the template BEFORE span computation so that the
    returned spans are valid positions in the normalized string.
    """
    return re.sub(r'%\}(?:\{[^\[\]]*?\n)(?=\[)', '%', rtf_content)


def extract_fillpoints(rtf_content: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Extract legacy %[...] fillpoints from raw RTF, stripping embedded RTF control codes.

    rtf_content must already be normalized via _normalize_rtf() so that spans
    correspond to positions in the same string that replace_fillpoints will use.

    Returns:
        (unique_fillpoints, spans) where spans is a list of (start, end, cleaned)
        for every occurrence in the RTF (including duplicates), and
        unique_fillpoints is the deduplicated list for the mapping step.
    """
    spans: list[tuple[int, int, str]] = []  # (start, end, cleaned) for every hit
    i = 0
    while i < len(rtf_content):
        start = rtf_content.find('%[', i)
        if start < 0:
            break

        depth = 1
        pos = start + 2  # after the '['

        while pos < len(rtf_content) and depth > 0:
            c = rtf_content[pos]

            if c == '\\':
                # Skip RTF control word / symbol / hex escape
                pos += 1
                if pos >= len(rtf_content):
                    break
                # Hex escape \'XX
                if rtf_content[pos] == "'" and pos + 2 < len(rtf_content) \
                        and _is_hex(rtf_content[pos + 1]) and _is_hex(rtf_content[pos + 2]):
                    pos += 3
                    continue
                # Control word: letters, optional minus, optional digits
                if rtf_content[pos].isalpha():
                    while pos < len(rtf_content) and rtf_content[pos].isalpha():
                        pos += 1
                    if pos < len(rtf_content) and rtf_content[pos] == '-':
                        pos += 1
                    while pos < len(rtf_content) and rtf_content[pos].isdigit():
                        pos += 1
                    # control word delimiter (single space consumed)
                    if pos < len(rtf_content) and rtf_content[pos] == ' ':
                        pos += 1
                else:
                    pos += 1  # escaped symbol like \{ \} \;
                continue

            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
            pos += 1

        if depth != 0:
            i = start + 1
            continue

        # pos is one past the closing ]
        raw = rtf_content[start:pos]

        # Strip inner RTF: control words then braces
        cleaned = re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', raw)  # control words
        cleaned = re.sub(r"\\'\w{2}", '', cleaned)           # hex escapes
        cleaned = re.sub(r'[{}]', '', cleaned)                # RTF braces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()        # collapse whitespace

        if cleaned.startswith('%[') and cleaned.endswith(']') and len(cleaned) > 4:
            # Trim whitespace inside the delimiters
            inner = cleaned[2:-1].strip()
            cleaned = '%[' + inner + ']'
            spans.append((start, pos, cleaned))

        i = pos

    # Deduplicate for the mapping step while preserving order
    seen = set()
    unique = []
    for _, _, cleaned in spans:
        if cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique, spans


_INVERTED_IF_RE = re.compile(
    r'%\[if\([^]]*\.\s*(isempty|isnullorempty)\s*[=(]\s*(true|1)\b',
    re.IGNORECASE
)

# Involvement entities: respondent/complainant/defendant being investigated
_INVOLVEMENT_TOKEN_RE = re.compile(
    r'^%\[\s*(?:JW_|Cust_|KF_|kf_)?(?:Respondent|Complainant|Defendant|VicWitOff)\.',
    re.IGNORECASE
)
# Assignment entities: attorneys/counsel/staff on the case
_ASSIGNMENT_TOKEN_RE = re.compile(
    r'^%\[\s*(?:JW_|Cust_|KF_|kf_)?(?:RespondentAtty|DefAtty|Defense|OBAAttorney|Prosecutor|AttyPros)',
    re.IGNORECASE
)


def _reverse_co_direction(branch_content: str) -> str:
    """If the branch has [involvement_token] ... c/o ... [assignment_token], swap their positions.

    In Pine, the convention is to address the letter to the attorney (assignment entity)
    with the respondent (involvement entity) as the "care of" party — the opposite of
    how the legacy JDA template often writes it.
    """
    _, branch_spans = extract_fillpoints(branch_content)
    if len(branch_spans) < 2:
        return branch_content

    t1_start, t1_end, t1_clean = branch_spans[0]
    t2_start, t2_end, t2_clean = branch_spans[1]

    middle = branch_content[t1_end:t2_start]
    if 'c/o' not in middle.lower():
        return branch_content

    if not (_INVOLVEMENT_TOKEN_RE.match(t1_clean) and _ASSIGNMENT_TOKEN_RE.match(t2_clean)):
        return branch_content

    # Reverse: assignment entity first, then middle (includes "c/o"), then involvement entity
    reversed_content = (
        branch_content[:t1_start]
        + branch_content[t2_start:t2_end]   # assignment token where involvement was
        + branch_content[t1_end:t2_start]   # middle (unchanged, contains "c/o")
        + branch_content[t1_start:t1_end]   # involvement token where assignment was
        + branch_content[t2_end:]
    )
    print(f"  Reversed c/o direction in branch: {t2_clean[:40]} c/o {t1_clean[:40]}")
    return reversed_content


def _swap_inverted_branches(rtf_content: str) -> str:
    """Swap If/Else branch content for JDA conditionals that use IsEmpty=true.

    JDA: %[If(X.IsEmpty=true)] [A] %[Else] [B] %[EndIf]
      means "if X is empty, show A; otherwise show B"

    Pine maps the condition to Any()==true (entity exists), which is the opposite
    polarity, so the branches must be swapped BEFORE token replacement:
      %[If(X.IsEmpty=true)] [B] %[Else] [A] %[EndIf]
    After replacement this becomes:
      @[If(@[X.Any()] == true)] [B] @[Else] [A] @[EndIf]  ← correct semantics

    Only swaps when a matching %[Else] exists (no-Else blocks are different).
    Handles nested conditionals correctly via depth tracking.
    """
    _, spans = extract_fillpoints(rtf_content)

    inversions = []
    for i, (start, end, cleaned) in enumerate(spans):
        if not _INVERTED_IF_RE.search(cleaned):
            continue
        depth = 1
        else_idx = endif_idx = None
        for j in range(i + 1, len(spans)):
            c = spans[j][2].lower().strip()
            if re.match(r'%\[if\(', c):
                depth += 1
            elif c == '%[else]' and depth == 1:
                else_idx = j
            elif c == '%[endif]':
                depth -= 1
                if depth == 0:
                    endif_idx = j
                    break
        if else_idx is not None and endif_idx is not None:
            inversions.append((i, else_idx, endif_idx))

    # Apply swaps back-to-front so earlier span positions stay valid
    for if_idx, else_idx, endif_idx in reversed(inversions):
        if_start,    if_end,    _ = spans[if_idx]
        else_start,  else_end,  _ = spans[else_idx]
        endif_start, endif_end, _ = spans[endif_idx]

        if_branch   = rtf_content[if_end:else_start]
        else_branch = rtf_content[else_end:endif_start]
        if_token    = rtf_content[if_start:if_end]
        else_token  = rtf_content[else_start:else_end]
        endif_token = rtf_content[endif_start:endif_end]

        # The Else-branch becomes the new If-true branch (has-entity case).
        # In Pine the convention is: attorney (assignment entity) listed first, then
        # "c/o" respondent (involvement entity) — opposite of many legacy templates.
        else_branch = _reverse_co_direction(else_branch)

        rtf_content = (
            rtf_content[:if_start]
            + if_token + else_branch + else_token + if_branch + endif_token
            + rtf_content[endif_end:]
        )
        print(f"  Swapped inverted branch: {spans[if_idx][2][:60]}...")

    return rtf_content


def make_extraction_call():
    """Deterministic extraction of legacy fillpoints from RTF.
    No LLM needed — parses %[...] tokens directly.
    """
    def extraction_call(state: dict):
        # 1. Collapse RTF-split %[ tokens
        normalized = _normalize_rtf(state['legacy_template'])
        # 2. Swap branches for IsEmpty=true conditionals before span extraction
        prepared = _swap_inverted_branches(normalized)
        fillpoints, spans = extract_fillpoints(prepared)
        print(f"Extracted {len(fillpoints)} unique fillpoints ({len(spans)} total occurrences)")
        return {
            "extracted_legacy_info": fillpoints,
            "fillpoint_spans": spans,
            "legacy_template": prepared,  # replace_fillpoints uses the same string
        }

    return extraction_call

def make_mapping_call(model):
    """Makes a call to the LLM to search the RAG tool for Pine syntax.
       The model can make tool calls to query the vector DB.
    
    Args:
        model: The LLM model (with RAG tool bound) to use for searching
    """
    def mapping_call(state: dict):
        """ The llm searches the RAG tool for Pine syntax mappings.
            It may make tool calls that trigger the rag_tool node."""
        tool_messages = [
            m for m in state["messages"]
            if isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls)
        ]
        # Use the model WITH tools (not structured output) so it can make RAG tool calls
        response = model.invoke(
            [
                SystemMessage(
                    content=mapping_prompt(state['unmapped_legacy_info'])
                )
            ]
            + tool_messages
        )

        print("Mapping variables to pine syntax")
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "mapping_calls": state.get('mapping_calls', 0) + 1,
        }
    
    return mapping_call


def make_mapping_finalize(model):
    """After RAG tool calls are done, parse the final mapping into structured output.
    
    Args:
        model: The LLM model to use for structured output parsing
    """
    def mapping_finalize(state: dict):
        """ Parse the tool call results into structured MappingResponse."""
        # Collect the RAG tool results and the model's mapping reasoning
        # into plain text so the structured output model can consume them
        # (structured output models can't receive raw ToolMessages).
        parts = []
        for m in state["messages"]:
            if isinstance(m, AIMessage) and m.tool_calls:
                # Capture the model's reasoning text (if any) alongside its tool calls
                if m.content:
                    parts.append(f"Assistant reasoning:\n{m.content}")
                for tc in m.tool_calls:
                    parts.append(f"Tool query: {tc['args'].get('query', '')}")
            elif isinstance(m, ToolMessage):
                parts.append(f"Tool result:\n{m.content}")
            elif isinstance(m, AIMessage) and m.content:
                parts.append(f"Assistant:\n{m.content}")

        context_text = "\n\n---\n\n".join(parts) if parts else "No tool results available."

        structured_model = model.with_structured_output(MappingResponse)
        response = structured_model.invoke(
            [
                SystemMessage(
                    content=mapping_prompt(state['unmapped_legacy_info'])
                ),
                HumanMessage(
                    content=f"Here are the Pine syntax reference results from the database:\n\n{context_text}"
                ),
            ]
        )

        for m in response.mappings:
            m.loaded = False
            m.pine = _strip_invented_structure(m.legacy, m.pine)

        print("Finalizing mapped Pine syntax")
        for m in response.mappings:
            print(f"  {m.legacy} -> {m.pine}")
        all_mappings = list(state.get('mapped_pine_info', [])) + list(response.mappings)
        return {
            "mapped_pine_info": all_mappings,
        }
    
    return mapping_finalize

def make_pine_template_generation_call(model):
    """Make a call to the LLM to generate a Pine template
       based on the mapped variables and functions
    
    Args:
        model: The LLM model to use for planning
    """
    def pine_template_generation_call(state: dict):
        """ The llm generates a pine templated based on the
            mapped legacy to pine variables and functions.
        """
        response = model.invoke(
            [
                SystemMessage(
                    content=generation_prompt(
                        state['legacy_template'],
                        state['mapped_pine_info'],
                        state.get('rtf_validation_error', '')
                    )
                )
            ]
        )
        print("Generating the Pine template")
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "generated_pine_template": response.content,
        }
    
    return pine_template_generation_call

def load_mappings(state: dict):
    """ Loads existing mappings from the mappingsdb prevents additional mapping
        calls by the agent.

        Args: state: the current agent state
    """
    print("Loading existing mappings from the database")
    with MappingDB(_MAPPING_DB_DIR) as db:
        mappings = []
        unmapped_legacy_info = []
        purged = 0
        for legacy_var in state['extracted_legacy_info']:
            pine_var = db.get_mapping(legacy_var)
            if pine_var:
                # Re-validate cached entries against current filters
                if _is_bad_mapping(pine_var) or _is_prompt_variable_mapping(pine_var) or _is_context_dependent(legacy_var, pine_var):
                    db.delete_mapping(legacy_var)
                    unmapped_legacy_info.append(legacy_var)
                    purged += 1
                else:
                    mappings.append(mapping(legacy=legacy_var, pine=pine_var, loaded=True))
            else:
                unmapped_legacy_info.append(legacy_var)
    
    if purged:
        print(f"Purged {purged} stale/invalid cached mappings")
    print(f"Loaded {len(mappings)} existing mappings from the database")
    return {"mapped_pine_info": mappings, "unmapped_legacy_info": unmapped_legacy_info}

def save_mappings(state: dict):
    """ Saves new mappings to the mappingsdb for future use.
        Filters out bad mappings before saving.

        Args: state: the current agent state
    """
    print("Saving new mappings to the database")
    mapping_counter = 0
    skipped_bad = 0
    skipped_prompt = 0
    skipped_ctx = 0
    with MappingDB(_MAPPING_DB_DIR) as db:
        for mapped in state.get('mapped_pine_info', []):
            if mapped.loaded:
                continue
            # Don't save bad mappings
            if _is_bad_mapping(mapped.pine):
                print(f"  BAD (not cached): {mapped.legacy} -> {mapped.pine}")
                skipped_bad += 1
                continue
            # Don't cache prompt variable mappings (template-specific names)
            if _is_prompt_variable_mapping(mapped.pine):
                print(f"  PROMPT (not cached): {mapped.legacy} -> {mapped.pine}")
                skipped_prompt += 1
                mapped.loaded = True
                continue
            # Don't cache context-dependent mappings (conditionals, event dates, etc.)
            if _is_context_dependent(mapped.legacy, mapped.pine):
                print(f"  CTX-DEP (not cached): {mapped.legacy} -> {mapped.pine}")
                skipped_ctx += 1
                mapped.loaded = True
                continue
            mapping_counter += 1
            db.add_mapping(mapped.legacy, mapped.pine)
            mapped.loaded = True
    print(f"Saved {mapping_counter} new mappings (skipped: {skipped_bad} bad, {skipped_prompt} prompt, {skipped_ctx} context-dep)")
    return {}


_STRUCTURAL_PATTERN = re.compile(
    r'@\[\s*(?:If\b|ElseIf\b|Else\b|EndIf\b|ForEach\b|EndForEach\b)[^\]]*\]',
    re.IGNORECASE
)

def _strip_invented_structure(legacy: str, pine: str) -> str:
    """Remove structural Pine tokens (@[If], @[Else], @[EndIf], @[ForEach], @[EndForEach])
    that the LLM invents when the input token is not itself a structural/conditional token.

    Structural tokens are only valid mappings when the source JDA token is itself structural
    (e.g., %[If(...)], %[Else], %[EndIf]).
    """
    lower = legacy.strip().lower()
    # If the input IS a structural token, leave the output untouched
    if re.match(r'%\[\s*(if\b|elseif\b|else\b|endif\b|foreach\b|endforeach\b)', lower):
        return pine
    # Otherwise strip any structural tokens the LLM added
    cleaned = _STRUCTURAL_PATTERN.sub('', pine)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    return cleaned if cleaned else pine


def _is_bad_mapping(pine_value: str) -> bool:
    """Returns True if the pine mapping is invalid and should not be saved."""
    if not pine_value or not pine_value.strip():
        return True
    lower = pine_value.strip().lower()
    if "no mapping found" in lower:
        return True
    # Reject Jinja2-style syntax (not Pine)
    if "{{" in pine_value or "}}" in pine_value:
        return True
    if "{%" in pine_value or "%}" in pine_value:
        return True
    # Reject entries with RTF control codes
    if "\\rtlch" in pine_value or "\\ltrch" in pine_value or "\\fcs" in pine_value:
        return True
    # Reject mappings that still contain legacy prefixes (JDA-to-JDA)
    if re.search(r'\bJW_|\bCust_|\bJD_|\bOCA_', pine_value):
        return True
    # Reject legacy %[...] syntax in pine output
    if '%[' in pine_value:
        return True
    return False


def _is_prompt_variable_mapping(pine_value: str) -> bool:
    """Returns True if the pine value is a simple prompt variable (template-specific, not cacheable).

    Prompt variables are simple identifiers like @[DateOfLetter] or @[Remarks] —
    they don't contain dots, method calls, or entity.field patterns.
    These are template-specific naming conventions and should NOT be cached.
    """
    stripped = pine_value.strip()
    # Must be a single @[...] token
    if not stripped.startswith("@[") or not stripped.endswith("]"):
        return False
    inner = stripped[2:-1].strip()
    # Simple identifier: no dots, no parens, no spaces, no operators
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', inner):
        return True
    return False


def _is_context_dependent(legacy_value: str, pine_value: str) -> bool:
    """Returns True if this mapping is context-dependent and should NOT be cached.

    Context-dependent mappings produce different Pine output depending on
    which template they appear in. Caching them causes cross-template bleeding.
    """
    lower_legacy = legacy_value.strip().lower()
    lower_pine = pine_value.strip().lower()

    # Control-flow tokens — these are structural, not variable mappings
    if lower_pine in ('@[else]', '@[endif]', '@[endforeach]'):
        return True

    # Conditionals — the correct Pine conditional depends on template context
    if lower_legacy.startswith('%[if(') or lower_legacy.startswith('%[elseif('):
        return True

    # FormatDate on event/document dates — template context determines
    # whether this should be a prompt variable or a data-bound expression
    if 'formatdate' in lower_legacy and 'event' in lower_legacy:
        return True
    if 'documentevents' in lower_legacy:
        return True

    return False


def replace_fillpoints(state: dict):
    """Replace legacy %[...] fillpoints in the original RTF with mapped Pine @[...] syntax.

    Uses the stored (start, end, cleaned) spans from extraction to do a precise
    back-to-front replacement so positions never shift.
    """
    # Build a lookup: cleaned legacy fillpoint -> pine syntax
    mapping_lookup: dict[str, str] = {}
    for m in state.get('mapped_pine_info', []):
        mapping_lookup[m.legacy] = m.pine

    spans = state.get('fillpoint_spans', [])
    template = state['legacy_template']

    replaced = 0
    skipped = 0
    # Replace back-to-front so earlier positions stay valid
    for raw_start, raw_end, cleaned in reversed(spans):
        pine = mapping_lookup.get(cleaned)
        if pine:
            template = template[:raw_start] + pine + template[raw_end:]
            replaced += 1
        else:
            skipped += 1

    print(f"Replaced {replaced} fillpoints in template ({skipped} had no mapping)")
    return {
        "generated_pine_template": template,
    }


def validate_rtf(state: dict):
    """ Validates that the generated .rtf template is correctly formatted.
        Auto-fixes minor issues (trailing braces, markdown fences).
        Reports unfixable errors for retry.
        This function is generated by Claude.

        Args: state: the current agent state
    """

    rtf = state["generated_pine_template"]
    errors = []
    warnings = []
    retries = state.get("generation_retries", 0) + 1

    # --- Auto-fix: strip markdown code fences ---
    stripped = rtf.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        rtf = "\n".join(lines)
        stripped = rtf.strip()
        warnings.append("Stripped markdown code fences from output")

    # --- Header check ---
    if not stripped.startswith("{\\rtf1"):
        errors.append("Missing or invalid RTF header: must start with {\\rtf1")

    # --- Brace balance ---
    depth = 0
    for ch in stripped:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if depth < 0:
            errors.append("Unbalanced braces: found closing '}' without matching '{'")
            break

    # Auto-fix small positive depth (missing trailing braces)
    if 0 < depth <= 3:
        stripped += "}" * depth
        rtf = stripped
        warnings.append(f"Auto-appended {depth} closing '}}' to balance braces")
        depth = 0
    elif depth > 3:
        errors.append(f"Unbalanced braces: {depth} unclosed '{{' remaining (too many to auto-fix)")

    if not stripped.endswith("}"):
        errors.append("RTF document does not end with '}' (likely truncated)")

    # --- Control flow balance ---
    if_count = len(re.findall(r'@\[If\b', stripped, re.IGNORECASE))
    endif_count = len(re.findall(r'@\[EndIf\b', stripped, re.IGNORECASE))
    if if_count > endif_count:
        errors.append(f"Truncated control flow: {if_count} @[If] but only {endif_count} @[EndIf]")

    foreach_count = len(re.findall(r'@\[Foreach\b', stripped, re.IGNORECASE))
    endforeach_count = len(re.findall(r'@\[EndForEach\b', stripped, re.IGNORECASE))
    if foreach_count > endforeach_count:
        errors.append(f"Truncated control flow: {foreach_count} @[Foreach] but only {endforeach_count} @[EndForEach]")

    # --- Hex data integrity (themedata) ---
    theme_match = re.search(r'\{\\\*\\themedata\s+([0-9a-fA-F\s]+)\}', stripped)
    if theme_match:
        hex_data = theme_match.group(1).replace(' ', '').replace('\n', '').replace('\r', '')
        if not re.fullmatch(r'[0-9a-fA-F]+', hex_data):
            errors.append("Corrupted themedata: contains non-hex characters")
        elif not hex_data.startswith("504b0304"):
            errors.append("Corrupted themedata: hex does not start with PK ZIP header (504b0304)")

    # --- Hex data integrity (colorschememapping) ---
    csm_match = re.search(r'\{\\\*\\colorschememapping\s+([0-9a-fA-F\s]+)\}', stripped)
    if csm_match:
        hex_data = csm_match.group(1).replace(' ', '').replace('\n', '').replace('\r', '')
        if not re.fullmatch(r'[0-9a-fA-F]+', hex_data):
            errors.append("Corrupted colorschememapping: contains non-hex characters")
        elif not hex_data.startswith("3c3f786d6c"):
            errors.append("Corrupted colorschememapping: hex does not start with XML declaration (3c3f786d6c = <?xml)")

    if warnings:
        print(f"RTF validation warnings: {'; '.join(warnings)}")

    if errors:
        error_msg = "; ".join(errors)
        print(f"RTF validation FAILED: {error_msg}")
        print(f"Retrying generation of Pine template: Attempt {retries}")
        return {"rtf_validation_error": error_msg, "generation_retries": retries, "generated_pine_template": rtf}
    
    print("RTF validation passed")
    return {"rtf_validation_error": "", "generation_retries": retries, "generated_pine_template": stripped}