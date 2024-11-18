import logging
from typing import List

logger = logging
logger_kwargs = {
    "level": logging.INFO,
    "format": "%(asctime)s %(levelname)s - %(message)s",
    "force": True,
}
logger.basicConfig(**logger_kwargs)


def build_repo_hierarchy(paths: List[List[str]]) -> List[dict]:
    # Helper function to insert a path into the hierarchy
    def insert_path(_tree, _path):
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
    root = []
    for path in paths:
        insert_path(root, path)

    return root
