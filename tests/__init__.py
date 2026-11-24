"""Make direct unittest module invocation and shared test-helper imports work.

Discovery also works with python -m unittest discover -s tests -v.
Importing the package alone does not run tests or change simulation data.
"""
