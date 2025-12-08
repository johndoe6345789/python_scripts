#!/usr/bin/env python3
import os
import zipfile
import tarfile
import time
import io

def list_zip_files() -> list[str]:
    return [f for f in os.listdir(".") if f.lower().endswith(".zip")]

def pick_zip_file(zips: list[str]) -> str:
    print("Found the following ZIP files:\n")
    for i, z in enumerate(zips, 1):
        print(f"  {i}. {z}")
    print()

    while True:
        choice = input("Select ZIP to convert (number): ").strip()
        if not choice.isdigit():
            print("Please enter a number.")
            continue
        idx = int(choice)
        if 1 <= idx <= len(zips):
            return zips[idx - 1]
        print("Invalid selection.")

def zip_to_tar(zip_path: str, tar_path: str):
    with zipfile.ZipFile(zip_path, "r") as zf, tarfile.open(tar_path, "w") as tf:
        for zi in zf.infolist():
            name = zi.filename

            # Timestamp conversion to TAR mtime
            mtime = time.mktime(zi.date_time + (0, 0, -1))

            if zi.is_dir():
                ti = tarfile.TarInfo(name=name.rstrip("/"))
                ti.type = tarfile.DIRTYPE
                ti.mtime = mtime
                tf.addfile(ti)
            else:
                data = zf.read(name)
                ti = tarfile.TarInfo(name=name)
                ti.size = len(data)
                ti.mtime = mtime
                tf.addfile(ti, fileobj=io.BytesIO(data))

def main():
    zips = list_zip_files()
    if not zips:
        print("No ZIP files found in current folder.")
        return

    zip_file = pick_zip_file(zips)
    print(f"\nSelected: {zip_file}")

    default_tar = os.path.splitext(zip_file)[0] + ".tar"
    out = input(f"Output TAR name [{default_tar}]: ").strip()
    tar_file = out if out else default_tar

    if os.path.exists(tar_file):
        confirm = input(f"{tar_file} exists. Overwrite? (y/n): ").lower()
        if confirm != "y":
            print("Cancelled.")
            return

    print(f"\nConverting {zip_file} → {tar_file} ...")
    zip_to_tar(zip_file, tar_file)
    print("Done.")

if __name__ == "__main__":
    main()
