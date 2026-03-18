
def extraction_planning_prompt(legacy_template: str, example_pine_template: str) -> str:
    return f"""
You are an expert planning extraction of variables and functions 
from legacy legal templates and mapping them to Pine syntax.

You will be provided with a legacy template and a reference Pine template.

Your task is to provide a structured, numbered plan to extract ALL dynamic
variables and functions from the legacy template. Ignore any static text.

Your output should be a numbered step by step plan to complete the extraction.
DO NOT perform any extraction or conversion to Pine syntax — only provide the plan.
Respond only with the plan, do not include any other text in your response.

Here is the legacy template to analyze:
{legacy_template}

Here is an example Pine template for reference:
{example_pine_template}

Begin planning."""


def extraction_prompt(legacy_template: str) -> str:
    return f"""
You are an expert at extracting variables and functions 
from legacy legal templates and mapping them to Pine syntax.

Your task is to extract ALL dynamic variables and functions from the legacy template below.
Ignore any static text.

Your output should be a list of extracted legacy variables and functions.
Ensure you gather every dynamic variable and function.
Respond only with the list of variables and functions, do not include any other text in your response.
Preserve the original merge field delimiters.

Here is the legacy template to analyze:
{legacy_template}

Begin extraction."""


def mapping_planning_prompt(extracted_legacy_info: list) -> str:
    return f"""
You are an expert planner for mapping extracted legacy variables and functions from legal templates to Pine syntax.

You have already extracted a list of legacy variables and functions from a legacy template.

Now your task is to create a plan to map EACH extracted legacy variable and function
to the appropriate Pine syntax.

During the next step you will be provided with:
- A tool to search a Pine syntax vector database
- The list of extracted variables and functions

The plan you create does not need to include extraction — that is already done.
The plan should be a numbered step by step process to map each extracted legacy
variable and function to Pine syntax.

Respond only with the plan, do not include any other text in your response.

Here is the list of extracted legacy variables and functions:
{extracted_legacy_info}

Begin planning."""


def mapping_prompt(extracted_legacy_info: list) -> str:
    return f"""
You are an expert at mapping extracted legacy variables and functions from legal templates to Pine syntax.

Your task is to map ALL extracted legacy variables and functions to the appropriate Pine syntax.

You will be provided with:
- A list of extracted legacy variables and functions that you will need to map to Pine syntax
- A tool to search a Pine syntax vector database to find the appropriate Pine syntax for each item

Your output should be a mapping of each extracted legacy variable and function to the appropriate Pine syntax.
If you cannot find a mapping for a particular item, respond with 'No mapping found' for that item.
Ensure you map EVERY extracted variable and function.

IMPORTANT: To speed up the process, make MULTIPLE tool calls in a single response.
Batch your searches — search for as many variables as possible at once using parallel tool calls
rather than searching one variable at a time. This is critical for performance.

Respond only with the mappings of variables and functions, do not include any other text in your response.
Preserve the original merge field delimiters.

Here is the list of extracted legacy variables and functions:
{extracted_legacy_info}

Begin mapping."""


def generation_prompt(legacy_template: str, example_pine_template: str, mapped_pine_info: list, rtf_rendering_output: str = "") -> str:
    return f"""
You are an expert at generating .rtf files with correct syntax for Pine legal templates.

You have already mapped the extracted legacy variables to Pine syntax.
Now your task is to generate a completed .rtf Pine template.

You will be provided:
- The original legacy template (use this as your starting point)
- An example Pine template for reference
- The mappings from legacy variables to Pine syntax

Instructions:
- Keep the EXACT structure and formatting of the legacy template.
- Replace the legacy variables and functions with the appropriate Pine syntax based on the mappings.
- If a mapping says 'No mapping found', insert the plain text 'No mapping found' in place of that variable.
- Your response must be ONLY the .rtf file contents — no other text.
- The output MUST be a valid, renderable .rtf file.

Here is the legacy template we are converting to Pine syntax:
{legacy_template}

Here is an example Pine template for reference:
{example_pine_template}

Here are the mappings from legacy to Pine syntax:
{mapped_pine_info}

Here is the ouput of your previous generation attempt:
{rtf_rendering_output}

Begin the generation of the .rtf file Pine template."""