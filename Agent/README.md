Created by Chase Hammond 03/12/2026

Overview:
    This project contains an AI agent built to analyze legacy legal templates and convert them
    to the Pine syntax. The current process takes a legacy template, extracts the functions
    and variables from it, creates mappings to Pine syntax using a ground truth db, then it
    regenerates the template with the new variables.  If there are any rendering issues
    the agent will retry template generation.
Current Issues:
    -slow: Working on speeding up the flow by saving mappings and reloading them instead of regenerating mappings each time.
    -Incomplete ground truth: In generations there will be missings mappings. The gruound truth is incomplete in its current state.
Running:
    Requirements:
        - OpenAI api key
    Directions:
        - Create a .env file in the AITemplates folder
        - Add the OpenAI api key to the .env file:   OPENAI_API_KEY="your key"
        - Install the necessary requirements, a venv is recommended:   pip install -r requirements.txt
        - run the agent from main.py:   python3 main.py "path to legacy template"