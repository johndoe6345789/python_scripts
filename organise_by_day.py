#!/usr/bin/env python3
"""
Organise all items (files and folders) in the *current working directory*
into day folders based on last modification time.

Behaviour:
- Operates only on top-level items in the current working directory.
- Moves both files and directories.
- Skips:
    * This script file itself.
    * Hidden/temp items starting with '.' or '~$' (e.g. OneDrive temp files).
    * Day folders it creates (names like '2025-12-08_Mon').
- Avoids overwriting by auto-renaming on collisions.
- Logs what it does.
- Never stops on permission errors; just reports and continues.
- Pauses at the end so the window stays open.
"""

import datetime
import pathlib
import re
import shutil
from typing import Union, Optional


PathLike = Union[str, "pathlib.Path"]


def make_day_folder(dt: datetime.datetime) -> str:
    """
    Return folder name like '2025-12-08_Mon'.

    Uses calendar (not ISO) date; weekday is Mon/Tue/.../Sun.
    """
    date_part = dt.strftime("%Y-%m-%d")
    weekday_part = dt.strftime("%a")
    return f"{date_part}_{weekday_part}"


def is_day_folder_name(name: str) -> bool:
    """
    Detect folders that look like our own day folders.

    Match 'YYYY-MM-DD_XXX' where XXX is 3 letters (weekday).
    """
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}_[A-Za-z]{3}$", name))


def try_move(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """
    Attempt to move src -> dst.

    Returns True on success, False if there was a recoverable error
    (permission, OS error). Does *not* raise.
    """
    try:
        print(f"MOVE: {src} -> {dst}")
        shutil.move(str(src), str(dst))
        return True
    except PermissionError as exc:
        print(f"SKIP (permission error): {src} -> {dst} [{exc}]")
        return False
    except OSError as exc:
        print(f"SKIP (OS error): {src} -> {dst} [{exc}]")
        return False


def safe_move(src: PathLike, dest_folder: PathLike) -> None:
    """
    Move file or directory 'src' into 'dest_folder'.

    If an item with the same name already exists in dest_folder,
    append ' (n)' before the extension (for files) or at the end (for folders).
    All errors are handled gracefully; no exceptions leak out.
    """
    src_path = pathlib.Path(src)
    dest_folder_path = pathlib.Path(dest_folder)

    try:
        dest_folder_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"SKIP (cannot create folder {dest_folder_path}): {exc}")
        return

    base_name = src_path.name
    dest_path = dest_folder_path / base_name

    # Simple case: no collision.
    if not dest_path.exists():
        try_move(src_path, dest_path)
        return

    # Collision handling.
    stem = src_path.stem
    suffix = src_path.suffix

    counter = 1
    while True:
        if src_path.is_dir():
            new_name = f"{base_name} ({counter})"
        else:
            new_name = f"{stem} ({counter}){suffix}"

        new_dest = dest_folder_path / new_name
        if not new_dest.exists():
            try_move(src_path, new_dest)
            return

        counter += 1
        # Safety guard against infinite loops (should never hit in practice).
        if counter > 10_000:
            print(f"SKIP (too many collisions): {src_path}")
            return


def should_skip_item(
    item: pathlib.Path,
    script_path: Optional[pathlib.Path],
) -> bool:
    """
    Decide whether to skip this item entirely.
    """
    # Skip script itself
    if script_path is not None:
        try:
            if item.resolve() == script_path:
                print(f"SKIP (script):       {item}")
                return True
        except FileNotFoundError:
            # Item disappeared mid-run
            return True

    # Skip hidden/temp-style names (covers OneDrive temp files like .849C...)
    if item.name.startswith(".") or item.name.startswith("~$"):
        print(f"SKIP (hidden/temp):  {item}")
        return True

    # Skip our own day folders
    if item.is_dir() and is_day_folder_name(item.name):
        print(f"SKIP (day folder):   {item}")
        return True

    # Skip things that no longer exist
    if not item.exists():
        print(f"SKIP (gone):         {item}")
        return True

    return False


def main() -> None:
    # Directory we actually operate on: the current working directory
    base_dir = pathlib.Path.cwd().resolve()

    # Script path (may or may not be in base_dir, but we skip it if seen)
    try:
        script_path = pathlib.Path(__file__).resolve()
    except NameError:
        # In some weird environments __file__ may not exist
        script_path = None

    print(f"Organising directory: {base_dir}")
    if script_path is not None:
        print(f"Script file:          {script_path}")
    print()

    for item in base_dir.iterdir():
        if should_skip_item(item, script_path):
            continue

        # Get modification time and decide day folder
        try:
            stat = item.stat()
        except PermissionError as exc:
            print(f"SKIP (stat denied):  {item} [{exc}]")
            continue
        except OSError as exc:
            print(f"SKIP (stat error):   {item} [{exc}]")
            continue

        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        day_folder_name = make_day_folder(mtime)
        day_folder_path = base_dir / day_folder_name

        safe_move(item, day_folder_path)

    print("\nDone.")
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
