"""Provide the simulation package without running a model at import time.

python -m simulation starts __main__.main, then runner.run_once. Other modules
keep generation, routing, forecasts and physical rules separate.
Importing this package alone does not load plotting dependencies.
"""
