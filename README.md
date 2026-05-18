# Legacy to Pine Template Conversion Tool

Augments conversion of a legacy template to pine syntax using an agentic pipeline. The tool uses human in the loop to approve mappings and improve generations. Mappings that are verified will be reused in future generations to cut down on repeated llm calls and make use of human verification.

# Using the tool
The tool requires python and node.js, these libraires must be install on the machine running the tool. Once those libraries are installed simply run the terminal command: 

make run

this will launch the application and you can begin converting templates.

An openai api key is required, if you are using the tool for the first time ensure that you set the api key in settings.

