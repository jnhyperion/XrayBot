import logging
from typing import List
import sys


class Logger(logging.RootLogger):
    def __init__(self):
        super().__init__(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        self.handlers = []
        self.addHandler(handler)
        self.propagate = False


logger = Logger()


def build_repo_hierarchy(paths: List[List[str]]) -> List[dict]:
    # Helper function to insert a path into the hierarchy
    def insert_path(_tree: List[dict], _path: List[str]):
        # If the path is empty, we stop
        if not _path:
            return
        # The current folder name is the first element of the path
        folder_name = _path[0]
        # Search for the folder in the current level of the tree
        folder = next((f for f in _tree if f["name"] == folder_name), None)
        if not folder:
            # If the folder doesn't exist, create it
            folder = {"name": folder_name, "folders": []}
            _tree.append(folder)
        # Recursively insert the rest of the path into the subfolders
        insert_path(folder["folders"], _path[1:])

    # Start with an empty root tree
    root: List[dict] = []
    for path in paths:
        insert_path(root, path)

    return root


def dict_to_graphql_param(d: dict, multilines_keys: List[str] = []) -> str:
    """
    Convert a Python dict to GraphQL parameter string format.

    Example:
        >>> dict_to_graphql_param({"summary": "Exploratory Test", "project": {"key": "CALC"}})
        '{ summary:"Exploratory Test", project: {key: "CALC"} }'

    Args:
        d: Dictionary to convert

    Returns:
        GraphQL parameter string
    """
    assert isinstance(d, dict), f"Expected dict, got {type(d).__name__}"

    def format_value(key, value):
        """Format a single value for GraphQL."""
        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape quotes in strings
            if len(value.splitlines()) > 1 or key in multilines_keys:
                return f'"""\n{value}\n"""'
            else:
                escaped = value.replace('"', '\\"')
                return f'"{escaped}"'
        elif isinstance(value, dict):
            return dict_to_graphql_param(value)
        elif isinstance(value, (list, tuple)):
            # Format list as GraphQL array
            items = ", ".join(format_value(None, item) for item in value)
            return f"[{items}]"
        else:
            # Fallback: convert to string and quote it
            return f'"{str(value)}"'

    # Format key-value pairs
    pairs = []
    for key, value in d.items():
        formatted_value = format_value(key, value)
        pairs.append(f"{key}: {formatted_value}")

    # Join pairs with comma and space, wrap in braces
    return "{" + ", ".join(pairs) + "}"
