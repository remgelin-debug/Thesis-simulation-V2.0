"""Keep the existing root command; its implementation is tools.kpis.main.

Running this script passes the same terminal arguments to that main function.
Importing it only imports the tool entry point. Prefer python -m tools.kpis
for new commands. No simulation rules or extra processing live in this wrapper.
"""
from tools.kpis import main

if __name__ == "__main__":
    raise SystemExit(main())
