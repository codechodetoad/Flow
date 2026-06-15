"""
BSON Splitter / Decoder
=======================
Decodes a large BSON file and splits output into ~N MB JSON chunks.
Each chunk is a valid JSON array of documents.

Requirements:
    pip install pymongo tqdm

Usage:
    python bson_split.py --file Documents.bson --size 100
    python bson_split.py --file Documents.bson --size 200
    python bson_split.py --file Documents.bson --size 100 --out-dir ./chunks
"""

import bson
import json
import argparse
import os
import sys
from datetime import datetime
from tqdm import tqdm


# ─────────────────────────────────────────
# SERIALIZER
# handles ObjectId, datetime, bytes — things
# json.dumps chokes on out of the box
# ─────────────────────────────────────────

class BSONEncoder(json.JSONEncoder):
    def default(self, obj):
        from bson import ObjectId
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


def serialize(doc):
    return json.dumps(doc, cls=BSONEncoder, ensure_ascii=False)


# ─────────────────────────────────────────
# SPLITTER
# ─────────────────────────────────────────

def split_bson(filepath, chunk_mb, out_dir):
    chunk_bytes = chunk_mb * 1024 * 1024
    os.makedirs(out_dir, exist_ok=True)

    base      = os.path.splitext(os.path.basename(filepath))[0]
    chunk_idx = 0
    written   = 0       # bytes in current chunk
    doc_count = 0       # docs in current chunk
    total     = 0       # total docs across all chunks
    fh        = None
    chunk_files = []

    def open_chunk():
        nonlocal chunk_idx, written, doc_count
        path = os.path.join(out_dir, f"{base}_chunk{chunk_idx:04d}.json")
        chunk_files.append(path)
        f = open(path, "w", encoding="utf-8")
        f.write("[\n")
        written   = 2
        doc_count = 0
        chunk_idx += 1
        return f

    def close_chunk(f):
        f.write("\n]\n")
        f.close()

    print(f"\nTarget chunk size : {chunk_mb} MB ({chunk_bytes:,} bytes)")
    print(f"Output directory  : {out_dir}\n")

    fh = open_chunk()
    first_in_chunk = True

    with open(filepath, "rb") as bsonfile:
        for doc in tqdm(bson.decode_file_iter(bsonfile),
                        desc="Decoding", unit=" docs",
                        miniters=5000, mininterval=1.0):
            line = serialize(doc)
            line_bytes = len(line.encode("utf-8"))

            # roll to next chunk if this doc would exceed limit
            # (always write at least one doc per chunk to avoid infinite loop)
            if not first_in_chunk and (written + line_bytes + 2) > chunk_bytes:
                close_chunk(fh)
                fh = open_chunk()
                first_in_chunk = True

            if not first_in_chunk:
                fh.write(",\n")
            fh.write(line)

            written        += line_bytes + 2
            doc_count      += 1
            total          += 1
            first_in_chunk  = False

    close_chunk(fh)

    # ── Summary ──────────────────────────────
    print(f"\n{'='*55}")
    print(f"Split complete")
    print(f"{'='*55}")
    print(f"  Total documents : {total:,}")
    print(f"  Chunks created  : {len(chunk_files)}")
    print(f"\n  Files:")
    for path in chunk_files:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"    {os.path.basename(path)}  ({size_mb:.1f} MB)")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BSON → JSON chunk splitter")
    parser.add_argument("--file",    required=True,  help="Path to .bson file")
    parser.add_argument("--size",    type=int, default=100,
                        help="Target chunk size in MB (default: 100)")
    parser.add_argument("--out-dir", default="chunks",
                        help="Output directory for chunks (default: ./chunks)")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}")
        sys.exit(1)

    split_bson(args.file, args.size, args.out_dir)