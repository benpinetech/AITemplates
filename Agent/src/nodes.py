from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage
from prompts import extraction_prompt, mapping_prompt, generation_prompt
from striprtf.striprtf import rtf_to_text
import re
from mappingdb import MappingDB
from state import mapping

class MappingResponse(BaseModel):
    """List of legacy-to-Pine mappings returned by the LLM."""
    mappings: List[mapping]


def _is_hex(c: str) -> bool:
    return c in '0123456789abcdefABCDEF'


def extract_fillpoints(rtf_content: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Extract legacy %[...] fillpoints from raw RTF, stripping embedded RTF control codes.

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


def make_extraction_call():
    """Deterministic extraction of legacy fillpoints from RTF.
    No LLM needed — parses %[...] tokens directly.
    """
    def extraction_call(state: dict):
        fillpoints, spans = extract_fillpoints(state['legacy_template'])
        print(f"Extracted {len(fillpoints)} unique fillpoints ({len(spans)} total occurrences)")
        return {
            "extracted_legacy_info": fillpoints,
            "fillpoint_spans": spans,
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
        tool_messages = [
            m for m in state["messages"]
            if isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls)
        ]
        structured_model = model.with_structured_output(MappingResponse)
        response = structured_model.invoke(
            [
                SystemMessage(
                    content=mapping_prompt(state['unmapped_legacy_info'])
                )
            ]
            + tool_messages
        )

        for m in response.mappings:
            m.loaded = False

        print("Finalizing mapped Pine syntax")
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
    with MappingDB("../mapping_db") as db:
        mappings = []
        unmapped_legacy_info = []
        for legacy_var in state['extracted_legacy_info']:
            pine_var = db.get_mapping(legacy_var)
            if pine_var:
                mappings.append(mapping(legacy=legacy_var, pine=pine_var, loaded=True))
            else:
                unmapped_legacy_info.append(legacy_var)
    
    print(f"Loaded {len(mappings)} existing mappings from the database")
    return {"mapped_pine_info": mappings, "unmapped_legacy_info": unmapped_legacy_info}

def save_mappings(state: dict):
    """ Saves new mappings to the mappingsdb for future use.
        Filters out bad mappings before saving.

        Args: state: the current agent state
    """
    print("Saving new mappings to the database")
    mapping_counter = 0
    skipped = 0
    with MappingDB("../mapping_db") as db:
        for mapped in state.get('mapped_pine_info', []):
            if mapped.loaded:
                continue
            # Don't save bad mappings
            if _is_bad_mapping(mapped.pine):
                skipped += 1
                continue
            mapping_counter += 1
            db.add_mapping(mapped.legacy, mapped.pine)
            mapped.loaded = True
    print(f"Saved {mapping_counter} new mappings to the database (skipped {skipped} bad entries)")
    return {}


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