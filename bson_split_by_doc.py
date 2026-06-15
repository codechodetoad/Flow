"""
BSON Splitter — grouped by document_id
======================================
Like bson_split.py, but guarantees that ALL parts of a single `document_id`
land in the SAME output chunk file (a document never straddles two files).

Why: the per-chunk parent resolution in import_to_neo4j_v*_parallel.py
(`_worker_2a`) resolves a part's `parent` string against its in-chunk siblings.
The plain byte-splitter (bson_split.py) scatters a document's parts across
chunks — ~47% of documents straddle ≥2 chunks — so cross-chunk parents are
invisible and the tree breaks (planning/parallel_and_weird_branches.md, C1).
Grouping by document fixes that without touching the parallel architecture.

Two streaming passes over the BSON (never holds all parts in memory):
  Pass 1 — sum each document's serialized byte size, then greedy bin-pack the
           documents into ~`--size` MB bins -> {document_id: bin_index}.
  Pass 2 — stream again, route each part to its bin's file.

A document larger than one chunk gets its own (overflowing) bin and is still
kept whole — the resolution invariant beats the size target.

Requirements:
    pip install pymongo tqdm

Usage:
    python bson_split_by_doc.py --file Document_Parts.bson --size 100 --out-dir parts_chunks
"""

import bson
import json
import argparse
import os
import sys
from datetime import datetime
from collections import defaultdict
from tqdm import tqdm


# ─────────────────────────────────────────
# SERIALIZER  (identical framing to bson_split.py so downstream readers are unchanged)
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


def get_document_id(doc):
    """Extract document_id as a plain string (handles ObjectId / {$oid} / str)."""
    did = doc.get("document_id")
    if isinstance(did, dict):
        if "$oid" in did:
            return did["$oid"]
        return ""
    return str(did) if did is not None else ""


# ─────────────────────────────────────────
# PASS 1 — size every document, then bin-pack
# ─────────────────────────────────────────
def build_bin_map(filepath, chunk_bytes):
    """Return (bin_of, n_bins, n_no_docid). bin_of maps document_id -> bin index.

    Parts with no document_id are pooled under '' and routed to their own bin
    (they cannot participate in any document's tree anyway)."""
    doc_bytes = defaultdict(int)
    doc_order = []                       # preserve first-seen order (dump order)
    n_no_docid = 0

    with open(filepath, "rb") as bsonfile:
        for doc in tqdm(bson.decode_file_iter(bsonfile),
                        desc="Pass 1 (sizing)", unit=" parts",
                        miniters=5000, mininterval=1.0):
            did = get_document_id(doc)
            if not did:
                n_no_docid += 1
            if did not in doc_bytes:
                doc_order.append(did)
            # +2 accounts for the ",\n" separator each part adds in the array
            doc_bytes[did] += len(serialize(doc).encode("utf-8")) + 2

    # Greedy bin-pack in dump order. The `cur_bytes > 0` guard guarantees an
    # oversized document gets its own bin instead of being split.
    bin_of = {}
    cur_bin = 0
    cur_bytes = 0
    for did in doc_order:
        size = doc_bytes[did]
        if cur_bytes > 0 and cur_bytes + size > chunk_bytes:
            cur_bin += 1
            cur_bytes = 0
        bin_of[did] = cur_bin
        cur_bytes += size

    n_bins = (cur_bin + 1) if doc_order else 0
    return bin_of, n_bins, n_no_docid


# ─────────────────────────────────────────
# PASS 2 — route each part to its bin's file
# ─────────────────────────────────────────
def write_bins(filepath, out_dir, base, bin_of, n_bins):
    os.makedirs(out_dir, exist_ok=True)

    handles = [None] * n_bins
    first_in_bin = [True] * n_bins
    counts = [0] * n_bins
    paths = []

    for i in range(n_bins):
        path = os.path.join(out_dir, f"{base}_chunk{i:04d}.json")
        paths.append(path)
        f = open(path, "w", encoding="utf-8")
        f.write("[\n")
        handles[i] = f

    with open(filepath, "rb") as bsonfile:
        for doc in tqdm(bson.decode_file_iter(bsonfile),
                        desc="Pass 2 (routing)", unit=" parts",
                        miniters=5000, mininterval=1.0):
            b = bin_of[get_document_id(doc)]
            f = handles[b]
            if not first_in_bin[b]:
                f.write(",\n")
            f.write(serialize(doc))
            first_in_bin[b] = False
            counts[b] += 1

    for i, f in enumerate(handles):
        f.write("\n]\n")
        f.close()

    return paths, counts


# ─────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────
def split_by_doc(filepath, chunk_mb, out_dir):
    chunk_bytes = chunk_mb * 1024 * 1024
    base = os.path.splitext(os.path.basename(filepath))[0]

    print(f"\nTarget chunk size : {chunk_mb} MB ({chunk_bytes:,} bytes)")
    print(f"Output directory  : {out_dir}")
    print(f"Grouping          : all parts of a document_id stay in one file\n")

    bin_of, n_bins, n_no_docid = build_bin_map(filepath, chunk_bytes)
    n_docs = len(set(bin_of.values())) if bin_of else 0
    print(f"\n  Distinct documents : {len(bin_of):,}")
    print(f"  Bins (chunk files) : {n_bins}")
    if n_no_docid:
        print(f"  ⚠️  parts with no document_id (pooled): {n_no_docid:,}")

    paths, counts = write_bins(filepath, out_dir, base, bin_of, n_bins)

    # ── Summary ──────────────────────────────
    print(f"\n{'='*55}")
    print(f"Split complete (grouped by document)")
    print(f"{'='*55}")
    print(f"  Total parts     : {sum(counts):,}")
    print(f"  Chunks created  : {len(paths)}")
    print(f"\n  Files:")
    for path, c in zip(paths, counts):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"    {os.path.basename(path)}  ({size_mb:.1f} MB, {c:,} parts)")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BSON → JSON chunk splitter, grouped so each document_id stays in one file")
    parser.add_argument("--file",    required=True, help="Path to .bson file (Document_Parts.bson)")
    parser.add_argument("--size",    type=int, default=100,
                        help="Target chunk size in MB (default: 100)")
    parser.add_argument("--out-dir", default="parts_chunks",
                        help="Output directory for chunks (default: ./parts_chunks)")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}")
        sys.exit(1)

    split_by_doc(args.file, args.size, args.out_dir)
