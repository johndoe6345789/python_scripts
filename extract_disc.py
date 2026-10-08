#!/usr/bin/env python3
"""Extract everything from a disc/folder (default I:\\) into an output folder.

* Plain files and folders are copied as-is.
* InstallShield (v5) cabinets (NAME.hdr + NAME.cab, magic "ISc(") are unpacked
  into <out>/<NAME>/ using a pure-Python reader, so no extra tools are needed.

Usage:
    python extract_disc.py [source] [output] [--list] [--no-copy]

    source     default I:\\
    output     default C:\\extracted_I
    --list     only list the files inside the cabinets, extract nothing
    --no-copy  skip copying the plain files, only unpack cabinets
"""
import argparse
import os
import shutil
import struct
import sys
import zlib

ISC_MAGIC = 0x28635349  # "ISc("

FILE_SPLIT = 0x01
FILE_OBFUSCATED = 0x02
FILE_COMPRESSED = 0x04
FILE_INVALID = 0x08


class ISCabinet:
    def __init__(self, hdr_path, cab_path):
        self.cab_path = cab_path
        with open(hdr_path, "rb") as f:
            self.hdr = f.read()
        sig, tag, _vol, desc_off, desc_size = struct.unpack_from("<5I", self.hdr, 0)
        if sig != ISC_MAGIC:
            raise ValueError("not an InstallShield header")
        self.version = (tag >> 12) & 0xF if (tag >> 24) == 1 else 0
        if self.version != 5:
            raise ValueError(f"unsupported InstallShield version {self.version}")
        self.desc_off = desc_off
        # The file table lives just past the descriptor's stated size, so keep the rest of the header.
        d = self.hdr[desc_off:]
        self.d = d
        self.table_off, = struct.unpack_from("<I", d, 0x0C)
        self.dir_count, = struct.unpack_from("<I", d, 0x1C)
        self.file_count, = struct.unpack_from("<I", d, 0x28)
        n = self.dir_count + self.file_count
        self.table = struct.unpack_from(f"<{n}I", d, self.table_off)

    def _cstr(self, off):
        end = self.d.index(b"\0", off)
        return self.d[off:end].decode("cp1252", "replace")

    def directory_name(self, idx):
        return self._cstr(self.table_off + self.table[idx])

    def files(self):
        for i in range(self.file_count):
            off = self.table_off + self.table[self.dir_count + i]
            (name_off, dir_idx, flags, exp_size, comp_size) = struct.unpack_from("<IIHII", self.d, off)
            data_off, = struct.unpack_from("<I", self.d, off + 18 + 20)
            name = self._cstr(self.table_off + name_off)
            directory = self.directory_name(dir_idx) if dir_idx < self.dir_count else ""
            yield {
                "path": os.path.join(*[p for p in directory.replace("/", "\\").split("\\") if p], name),
                "flags": flags,
                "size": exp_size,
                "csize": comp_size,
                "offset": data_off,
            }

    def extract(self, entry, dest):
        flags = entry["flags"]
        if flags & FILE_INVALID or entry["size"] == 0 and entry["csize"] == 0:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            open(dest, "wb").close()
            return
        if flags & FILE_SPLIT:
            raise NotImplementedError("file is split across volumes")
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        written = 0
        with open(self.cab_path, "rb") as cab, open(dest, "wb") as out:
            cab.seek(entry["offset"])
            remaining = entry["csize"]
            if flags & FILE_COMPRESSED:
                while remaining > 0:
                    (clen,) = struct.unpack("<H", cab.read(2))
                    chunk = cab.read(clen)
                    remaining -= clen + 2
                    # Each chunk is a raw deflate stream; terminate it with a sync marker.
                    data = zlib.decompressobj(-15).decompress(chunk + b"\x00\x00\xff\xff")
                    out.write(data)
                    written += len(data)
            else:
                while remaining > 0:
                    data = cab.read(min(remaining, 1 << 20))
                    if not data:
                        break
                    out.write(data)
                    remaining -= len(data)
                    written += len(data)
        if written != entry["size"]:
            print(f"    warning: {dest}: wrote {written} bytes, expected {entry['size']}")


def find_cabinets(src):
    for name in sorted(os.listdir(src)):
        base, ext = os.path.splitext(name)
        if ext.lower() != ".hdr":
            continue
        hdr = os.path.join(src, name)
        cab = next((os.path.join(src, base + e) for e in (".cab", ".CAB") if os.path.exists(os.path.join(src, base + e))), None)
        if cab:
            yield base, hdr, cab


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", default="I:\\")
    ap.add_argument("output", nargs="?", default="C:\\extracted_I")
    ap.add_argument("--list", action="store_true", help="list cabinet contents only")
    ap.add_argument("--no-copy", action="store_true", help="don't copy plain files")
    args = ap.parse_args()

    src, out = args.source, args.output
    if not os.path.isdir(src):
        sys.exit(f"Source not found: {src}")

    if not args.list:
        os.makedirs(out, exist_ok=True)
        free = shutil.disk_usage(out).free
        print(f"Output: {out}  ({free / 2**30:.1f} GiB free)")

    cabinets = list(find_cabinets(src))
    cab_files = {os.path.normcase(p) for _, h, c in cabinets for p in (h, c)}

    if not args.list and not args.no_copy:
        print("Copying plain files...")
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for f in files:
                p = os.path.join(root, f)
                if os.path.normcase(p) in cab_files:
                    continue
                dest = os.path.join(out, "" if rel == "." else rel, f)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    shutil.copyfile(p, dest)
                    print("  ", os.path.relpath(dest, out))
                except OSError as ex:  # keep going on an unreadable file
                    print(f"   FAILED: {os.path.relpath(dest, out)}: {ex}")

    for name, hdr, cab in cabinets:
        try:
            cabinet = ISCabinet(hdr, cab)
        except (ValueError, struct.error) as e:
            print(f"Skipping {name}: {e}")
            continue
        # Skip junk table entries (empty, with control characters in the name).
        entries = [e for e in cabinet.files()
                   if e["size"] or e["csize"] or e["path"].isprintable()]
        print(f"{name}.cab: {len(entries)} files")
        for e in entries:
            if args.list:
                print(f"  {e['size']:>12}  {e['path']}")
                continue
            dest = os.path.join(out, name, e["path"])
            print("  ", os.path.join(name, e["path"]))
            try:
                cabinet.extract(e, dest)
            except Exception as ex:  # keep going on a bad file
                print(f"    FAILED: {ex}")
    print("Done.")


if __name__ == "__main__":
    main()
