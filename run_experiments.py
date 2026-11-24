"""Keep the existing root command; its implementation is tools.experiments.main.

Running this script passes the same terminal arguments to that main function.
Importing it only imports the tool entry point. Prefer python -m tools.experiments
for new commands. No simulation rules or extra processing live in this wrapper.
"""
from tools.experiments import main

if __name__ == "__main__":
    raise SystemExit(main())
