"""
import_to_neo4j_v6_parallel.py
v7 schema (planning/schema_v7_rethink.md) + the weird-branches fixes
(planning/v6_weird_branches_fix_plan.md) on top of the v5_parallel worker architecture.

What changed vs v5 (root-cause fixes from planning/parallel_and_weird_branches.md):
  - C1 (cross-chunk split): EXPECTS parts pre-chunked by document via
    bson_split_by_doc.py, so every part of a document_id is in one file and
    `_worker_2a`'s per-document resolution is complete. No code change needed in
    the worker beyond trusting that grouping.
  - C2 (duplicate-title siblings): `_resolve_part_parent` now walks the FULL
    `parent` path (not just the last segment) and disambiguates same-title
    candidates by checking the grandparent segment. `title_map` keeps ALL ids
    per title (not first-wins).
  - C3 (no tier-order check): a candidate parent is rejected when both endpoints
    are title-anchored and parent_tier >= child_tier (kills "Chương←Mục").
  - C4/D (type vs tier coupled): `part_type` is now the NOMINAL keyword label and
    `tier` is the STRUCTURAL chain depth — they may legitimately disagree for
    inverted docs ("Mục under Điều" -> part_type='Muc', tier=6) instead of being a
    silent violation.
  - E (double-attach): a part whose parentId does not resolve to an in-document
    sibling is treated as a ROOT with empty parentId — it gets CO_CHUA only, never
    both LA_CON_CUA and CO_CHUA.

Everything else (phase order, relay files, one-driver-per-worker, dimension nodes,
1a/1b/1c/2b/3 workers, relay row shape {id, document_id, parentId, is_root}) is
IDENTICAL to v5 — only the Phase-2a resolution region differs.

Phase execution order (must not be reordered):
  1a  VanBan nodes          — parallel, disjoint IDs, no contention
  1b  CoQuan/LinhVuc/ThuTuc — serial, tiny
  1c  3 relationship types   — parallel + execute_write retry
  2a  DieuKhoan nodes        — parallel; resolves parent (§4.0 + C2/C3/E) and
                                part_type/tier (§3 + C4); writes ONE relay file per
                                chunk: {id, document_id, parentId, is_root}
  2b  LA_CON_CUA edges       — parallel + execute_write retry, reads 2a relay
  3   CO_CHUA edges          — parallel + execute_write retry, reads 2a relay

Phases 4, 5, 6 are excluded (blocked or not requested).
"""

import os
import gc
import glob
import json
import re
import shutil
import tempfile
import atexit
from concurrent.futures import ProcessPoolExecutor, as_completed

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable
from tqdm import tqdm

# ── orjson: 2-5x faster JSON; graceful fallback. Both load AND dump variants.
try:
    import orjson

    def _load_json_file(path):
        with open(path, 'rb') as f:
            return orjson.loads(f.read())

    def _dump_json_file(path, data):
        with open(path, 'wb') as f:
            f.write(orjson.dumps(data))

except ImportError:
    def _load_json_file(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _dump_json_file(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)


# ==========================================
# CONNECTION
# ==========================================
URI  = "bolt://127.0.0.1:7687"
AUTH = ("neo4j", "FF15NUB@3")

NODE_BATCH_SIZE = 5000
EDGE_BATCH_SIZE = 5000
STREAM_SIZE     = 5000

# How many worker processes to run in parallel.
# Recommended 6–8. Neo4j write throughput saturates before cores do.
N_WORKERS = 6

# ==========================================
# DOMAIN MAPPINGS
# ==========================================
DOCUMENT_TYPE_MAP = {
    'QĐ':   'Quyết định',
    'NĐ':   'Nghị định',
    'TT':   'Thông tư',
    'CT':   'Chỉ thị',
    'PL':   'Pháp lệnh',
    'NQ':   'Nghị quyết',
    'TTLT': 'Thông tư liên tịch',
    'CV':   'Công văn',
    'TB':   'Thông báo',
    'HD':   'Hướng dẫn',
    'BC':   'Báo cáo',
    'KH':   'Kế hoạch',
    'QC':   'Quy chế',
    'QT':   'Quy trình',
}

# v7 structural tiers (schema_v7_rethink.md §2). `tier` is the STRUCTURAL depth in
# the resolved hierarchy; TIER_TYPE labels an *inherited* (unanchored) part by its
# depth. For an *anchored* part, the nominal label comes from TITLE_TYPE instead —
# the two can disagree for inverted docs ("Mục under Điều"), which is honest (C4).
TIER_TYPE = {
    1: 'Phan',
    2: 'Chuong',
    3: 'Muc',
    4: 'TieuMuc',
    5: 'Dieu',
    6: 'Khoan',
    7: 'Diem',
}

# Nominal part_type from the title KEYWORD (what the part is CALLED), independent
# of where it structurally SITS (tier). schema_v7 §3 + weird-branches §4 (C4/D).
TITLE_TYPE = dict(TIER_TYPE)

# Pass A — anchor tier from the title keyword. Order matters: "Tiểu mục" must be
# checked before "Mục" (it is a prefix match otherwise).
_TITLE_TIER_PATTERNS = [
    (re.compile(r'^tiểu\s*mục\b'), 4),
    (re.compile(r'^phần\b'),       1),
    (re.compile(r'^chương\b'),     2),
    (re.compile(r'^mục\b'),        3),
    (re.compile(r'^điều\b'),       5),
    (re.compile(r'^khoản\b'),      6),
    (re.compile(r'^điểm\b'),       7),
]


# ==========================================
# HELPERS
# ==========================================
def extract_value(val):
    if isinstance(val, dict):
        if '$oid'        in val: return val['$oid']
        if '$date'       in val:
            d = val['$date']
            return d['$numberLong'] if isinstance(d, dict) else d
        if '$numberLong' in val: return val['$numberLong']
    return str(val) if val is not None else None


def safe_get_string(doc, *keys, default=''):
    for key in keys:
        if key in doc:
            val = doc[key]
            if isinstance(val, dict):
                if '$oid' in val or '$date' in val or '$numberLong' in val:
                    return extract_value(val) or default
                continue
            if val is not None:
                return str(val).strip()
    return default


def safe_get_int(doc, *keys, default=0):
    for key in keys:
        if key in doc:
            val = doc[key]
            if val is not None:
                try:    return int(val)
                except: pass
    return default


def infer_doc_type(code):
    if not code:
        return ''
    parts = str(code).split('/')
    prefix = parts[-1].split('-')[0].strip() if parts else ''
    return DOCUMENT_TYPE_MAP.get(prefix, '')


def _tier_from_title(title):
    """Pass A — anchor a part's tier from its title keyword. Returns None if unanchored
    (numeric/letter Khoan-Diem titles, Roman numerals, free-text headers like 'Giới thiệu')."""
    t = (title or '').strip().lower()
    if not t:
        return None
    for pattern, tier in _TITLE_TIER_PATTERNS:
        if pattern.match(t):
            return tier
    return None


def _resolve_part_tiers(parts, anchored_tier):
    """Resolve (part_type, tier) for every part of one document (schema_v7 §3 + C4/D).

    parts: list of dicts with 'id', 'parentId'(RESOLVED), 'level', 'title'.
    anchored_tier: {id: keyword_tier or None} precomputed from the title.

    tier      = STRUCTURAL depth: walk the RESOLVED parentId chain; a root keeps its
                anchored tier, else falls back to level (last resort). Decoupled from
                the keyword so "Mục under Điều" gets tier 6, not a forced 3 (C4).
    part_type = NOMINAL keyword label when the title is anchored (TITLE_TYPE),
                else the inherited structural label (TIER_TYPE[tier]).
    Returns {id: (part_type, tier)}.
    """
    by_id = {p['id']: p for p in parts}
    tier_cache = {}

    def tier_of(pid, seen):
        if pid in tier_cache:
            return tier_cache[pid]
        p = by_id[pid]
        parent = p['parentId']                       # RESOLVED parent (set in worker)
        if parent and parent in by_id and pid not in seen:
            seen.add(pid)
            t = min(tier_of(parent, seen) + 1, 7)    # structural chain depth
        else:
            a = anchored_tier.get(pid)
            if a is not None:
                t = a                                # anchored root keeps its tier
            else:
                t = 5 if p['level'] == 1 else 6      # last-resort fallback (§3)
        tier_cache[pid] = t
        return t

    result = {}
    for pid in by_id:
        t = tier_of(pid, set())
        a = anchored_tier.get(pid)
        part_type = TITLE_TYPE[a] if a is not None else TIER_TYPE.get(t, 'Diem')
        result[pid] = (part_type, t)
    return result


def stream_json_file(path):
    """Yield STREAM_SIZE-record slices from a JSON array file."""
    data = _load_json_file(path)
    for i in range(0, len(data), STREAM_SIZE):
        yield data[i:i + STREAM_SIZE]
    del data


# ==========================================
# CYPHER CONSTANTS
# ==========================================
_VAN_BAN_NODE_Q = """
UNWIND $batch AS doc
MERGE (v:VanBan {id: doc.id})
SET v.name           = doc.name,
    v.document_code   = doc.document_code,
    v.document_source = doc.document_source,
    v.category        = doc.category,
    v.doc_type        = doc.doc_type,
    v.status          = doc.status,
    v.validity_status = doc.validity_status,
    v.cap_thuc_hien   = doc.cap_thuc_hien,
    v.department_name = doc.department_name,
    v.created_at      = doc.created_at,
    v.updated_at      = doc.updated_at,
    v.date_issued_at  = doc.date_issued_at
"""

_CO_QUAN_NODE_Q  = "UNWIND $batch AS r MERGE (:CoQuan {name: r})"
_LINH_VUC_NODE_Q = "UNWIND $batch AS r MERGE (:LinhVuc {name: r})"

_THU_TUC_NODE_Q = """
UNWIND $batch AS r
MERGE (t:ThuTuc {ma_thu_tuc: r.ma_thu_tuc})
SET t.ten_thu_tuc   = r.ten_thu_tuc,
    t.cap_thuc_hien = r.cap_thuc_hien
"""

_BAN_HANH_BOI_Q = """
UNWIND [x IN $batch WHERE x.co_quan_ban_hanh <> ''] AS doc
MATCH (cq:CoQuan {name: doc.co_quan_ban_hanh})
MATCH (v:VanBan  {id:   doc.id})
MERGE (cq)-[:BAN_HANH_BOI]->(v)
"""

_THUOC_LINH_VUC_Q = """
UNWIND [x IN $batch WHERE x.linh_vuc <> ''] AS doc
MATCH (lv:LinhVuc {name: doc.linh_vuc})
MATCH (v:VanBan   {id:   doc.id})
MERGE (v)-[:THUOC_LINH_VUC]->(lv)
"""

_QUY_DINH_BOI_Q = """
UNWIND [x IN $batch WHERE x.ma_thu_tuc <> ''] AS doc
MATCH (tt:ThuTuc {ma_thu_tuc: doc.ma_thu_tuc})
MATCH (v:VanBan  {id:         doc.id})
MERGE (tt)-[:QUY_DINH_BOI]->(v)
"""

_DIEU_KHOAN_NODE_Q = """
UNWIND $batch AS part
MERGE (p:DieuKhoan {id: part.id})
SET p.title       = part.title,
    p.document_id = part.document_id,
    p.parentId    = part.parentId,
    p.full_name   = part.full_name,
    p.content     = part.content,
    p.source      = part.source,
    p.document    = part.document,
    p.parent      = part.parent,
    p.part_type   = part.part_type,
    p.tier        = part.tier,
    p.level       = part.level,
    p.page        = part.page
"""

_LA_CON_CUA_Q = """
UNWIND $batch AS part
MATCH (child:DieuKhoan  {id: part.id})
MATCH (parent:DieuKhoan {id: part.parentId})
MERGE (child)-[:LA_CON_CUA]->(parent)
"""

_CO_CHUA_TOP_Q = """
UNWIND $batch AS part
MATCH (v:VanBan    {id: part.document_id})
MATCH (p:DieuKhoan {id: part.id})
MERGE (v)-[:CO_CHUA]->(p)
"""


# ==========================================
# RECORD BUILDERS
# ==========================================
def _build_van_ban_record(doc):
    doc_id = extract_value(doc.get('_id'))
    if not doc_id:
        return None
    doc_code = safe_get_string(doc, 'document_code')
    src      = safe_get_string(doc, 'document_source')
    return {
        'id':               doc_id,
        'name':             src,
        'document_code':    doc_code,
        'document_source':  src,
        'category':         safe_get_string(doc, 'category'),
        'doc_type':         infer_doc_type(doc_code),
        'status':           safe_get_string(doc, 'status'),
        'validity_status':  safe_get_string(doc, 'validity_status'),
        'created_at':       safe_get_string(doc, 'createdAt', 'created_at'),
        'updated_at':       safe_get_string(doc, 'updatedAt', 'updated_at'),
        'date_issued_at':   safe_get_string(doc, 'date_issued_at'),
        # VanBan properties with >50% determinable coverage (brainstorm.md rule):
        #   department_name 92.7%, cap_thuc_hien 40.6% (requested for exploration)
        'cap_thuc_hien':    safe_get_string(doc, 'cap_thuc_hien'),
        'department_name':  safe_get_string(doc, 'department_name'),
        # Dimension fields — not stored on VanBan; used for relay + satellite nodes
        'co_quan_ban_hanh': safe_get_string(doc, 'co_quan_ban_hanh'),
        'linh_vuc':         safe_get_string(doc, 'linh_vuc'),
        'ma_thu_tuc':       safe_get_string(doc, 'ma_thu_tuc'),
        'ten_thu_tuc':      safe_get_string(doc, 'ten_thu_tuc'),
    }


def _build_dieu_khoan_record(raw, part_id, doc_id, parent_id, level, part_type, tier):
    return {
        'id':          part_id,
        'document_id': doc_id,
        'parentId':    parent_id,
        'title':       safe_get_string(raw, 'title'),
        'full_name':   safe_get_string(raw, 'full_name'),
        'content':     safe_get_string(raw, 'content'),
        'source':      safe_get_string(raw, 'source'),
        'document':    safe_get_string(raw, 'document'),
        'parent':      safe_get_string(raw, 'parent'),
        'part_type':   part_type,
        'tier':        tier,
        'level':       level,
        'page':        safe_get_int(raw, 'page'),
    }


# ==========================================
# CONSTRAINTS & INDEXES  (run from main process)
# ==========================================
def create_constraints(driver):
    print('Setting up constraints and indexes...')
    ddl = [
        'CREATE CONSTRAINT unique_van_ban_id    IF NOT EXISTS FOR (v:VanBan)        REQUIRE v.id           IS UNIQUE',
        'CREATE CONSTRAINT unique_dk_id         IF NOT EXISTS FOR (p:DieuKhoan)     REQUIRE p.id           IS UNIQUE',
        'CREATE CONSTRAINT unique_co_quan_name  IF NOT EXISTS FOR (c:CoQuan)        REQUIRE c.name         IS UNIQUE',
        'CREATE CONSTRAINT unique_linh_vuc_name IF NOT EXISTS FOR (l:LinhVuc)       REQUIRE l.name         IS UNIQUE',
        'CREATE CONSTRAINT unique_thu_tuc_ma    IF NOT EXISTS FOR (t:ThuTuc)        REQUIRE t.ma_thu_tuc   IS UNIQUE',
        'CREATE CONSTRAINT unique_ki_id         IF NOT EXISTS FOR (k:KnowledgeItem) REQUIRE k.id           IS UNIQUE',
        'CREATE INDEX idx_dk_doc_id    IF NOT EXISTS FOR (p:DieuKhoan) ON (p.document_id)',
        'CREATE INDEX idx_dk_parent_id IF NOT EXISTS FOR (p:DieuKhoan) ON (p.parentId)',
        'CREATE INDEX idx_dk_part_type IF NOT EXISTS FOR (p:DieuKhoan) ON (p.part_type)',
        'CREATE INDEX idx_dk_tier      IF NOT EXISTS FOR (p:DieuKhoan) ON (p.tier)',
        'CREATE INDEX idx_vb_doc_type  IF NOT EXISTS FOR (v:VanBan)    ON (v.doc_type)',
        'CREATE INDEX idx_vb_validity  IF NOT EXISTS FOR (v:VanBan)    ON (v.validity_status)',
        'CREATE INDEX idx_vb_cap        IF NOT EXISTS FOR (v:VanBan)    ON (v.cap_thuc_hien)',
        'CREATE INDEX idx_vb_dept       IF NOT EXISTS FOR (v:VanBan)    ON (v.department_name)',
        'CREATE INDEX idx_vb_name      IF NOT EXISTS FOR (v:VanBan)    ON (v.name)',
        'CREATE INDEX idx_vb_doc_code  IF NOT EXISTS FOR (v:VanBan)    ON (v.document_code)',
    ]
    with driver.session() as session:
        for q in ddl:
            session.run(q)
    print('Constraints and indexes ready.')


# ==========================================
# PROCESS POOL — one driver per worker process
# ==========================================
# Set by _init_worker inside each spawned process. Never used in the main process.
_proc_driver = None


def _init_worker(uri, auth):
    """Called once per worker process at pool startup.  Creates the driver and
    registers its close on process exit so connections are not leaked."""
    global _proc_driver
    _proc_driver = GraphDatabase.driver(uri, auth=auth, max_connection_pool_size=2)
    atexit.register(_proc_driver.close)


# ==========================================
# PHASE 1a WORKER — VanBan nodes + relay file
# ==========================================
def _worker_1a(fpath, relay_dir):
    """Process one Document chunk:
      1. MERGE VanBan nodes (auto-commit, no contention — disjoint IDs).
      2. Write a small relay JSON file with the 6 dimension-related fields per record.
         Phase 1c reads these relay files instead of re-parsing the full 22.5 GB originals.
    Returns (n_written, n_skipped).
    """
    relay_path = os.path.join(relay_dir, os.path.basename(fpath))
    relay_rows = []
    n_written  = 0
    n_skipped  = 0

    with _proc_driver.session() as session:
        for window in stream_json_file(fpath):
            node_batch = []
            for doc in window:
                rec = _build_van_ban_record(doc)
                if rec is None:
                    n_skipped += 1
                    continue
                node_batch.append(rec)
                relay_rows.append({
                    'id':               rec['id'],
                    'co_quan_ban_hanh': rec['co_quan_ban_hanh'],
                    'linh_vuc':         rec['linh_vuc'],
                    'ma_thu_tuc':       rec['ma_thu_tuc'],
                    'ten_thu_tuc':      rec['ten_thu_tuc'],
                    'cap_thuc_hien':    rec['cap_thuc_hien'],
                })
            if node_batch:
                session.run(_VAN_BAN_NODE_Q, batch=node_batch)
                n_written += len(node_batch)

    _dump_json_file(relay_path, relay_rows)
    gc.collect()
    return n_written, n_skipped


# ==========================================
# PHASE 1b — dimension nodes  (serial, main process)
# ==========================================
def import_dimension_nodes(driver, relay_dir):
    """Read all relay files, collect distinct CoQuan/LinhVuc/ThuTuc values,
    and MERGE them in a single serial pass.  This eliminates lock contention
    on the hot dimension nodes during Phase 1c.
    """
    relay_files = sorted(glob.glob(os.path.join(relay_dir, '*.json')))
    co_quan_set  = set()
    linh_vuc_set = set()
    thu_tuc_dict = {}

    for rpath in relay_files:
        for rec in _load_json_file(rpath):
            cq = rec.get('co_quan_ban_hanh', '')
            lv = rec.get('linh_vuc', '')
            mt = rec.get('ma_thu_tuc', '')
            if cq: co_quan_set.add(cq)
            if lv: linh_vuc_set.add(lv)
            if mt and mt not in thu_tuc_dict:
                thu_tuc_dict[mt] = {
                    'ma_thu_tuc':    mt,
                    'ten_thu_tuc':   rec.get('ten_thu_tuc', ''),
                    'cap_thuc_hien': rec.get('cap_thuc_hien', ''),
                }

    print(f'  Unique CoQuan: {len(co_quan_set)} | LinhVuc: {len(linh_vuc_set)} | ThuTuc: {len(thu_tuc_dict)}')

    with driver.session() as session:
        if co_quan_set:
            session.run(_CO_QUAN_NODE_Q, batch=list(co_quan_set))
        if linh_vuc_set:
            session.run(_LINH_VUC_NODE_Q, batch=list(linh_vuc_set))
        if thu_tuc_dict:
            session.run(_THU_TUC_NODE_Q, batch=list(thu_tuc_dict.values()))

    return len(co_quan_set), len(linh_vuc_set), len(thu_tuc_dict)


# ==========================================
# PHASE 1c WORKER — 3 relationship types from relay file
# ==========================================
def _worker_1c(relay_path):
    """Process one relay file → MERGE BAN_HANH_BOI, THUOC_LINH_VUC, QUY_DINH_BOI.
    Uses execute_write for managed transaction retry (hot dimension endpoints).
    Returns total relationship records processed.
    """
    n_processed = 0

    with _proc_driver.session() as session:
        for batch in stream_json_file(relay_path):
            if not batch:
                continue
            # Capture batch for retry-safe closure
            b = batch
            session.execute_write(lambda tx, _b=b: tx.run(_BAN_HANH_BOI_Q,   batch=_b))
            session.execute_write(lambda tx, _b=b: tx.run(_THUOC_LINH_VUC_Q, batch=_b))
            session.execute_write(lambda tx, _b=b: tx.run(_QUY_DINH_BOI_Q,   batch=_b))
            n_processed += len(batch)

    gc.collect()
    return n_processed


# ==========================================
# PHASE 2a WORKER — DieuKhoan nodes (v7 + weird-branches fixes C2/C3/E)
# ==========================================
def _resolve_part_parent(p, by_id, title_map, parent_of, anchored_tier):
    """Resolve a part's real parent (schema_v7 §4.0 + C2 full-path + C3 tier guard).

    Inputs (all scoped to ONE document — guaranteed whole post bson_split_by_doc):
      p            : the part dict ('id', 'parentIdRaw', 'parentStr')
      by_id        : set of part ids in this document
      title_map    : {title -> [all ids with that title]}   (C2: not first-wins)
      parent_of    : {id -> parentStr}                       (C2: grandparent walk)
      anchored_tier: {id -> keyword_tier or None}            (C3: tier-order guard)

    Order:
      1. parentId FKs a sibling          -> resolved, not root
      2. last `parent` segment matches a sibling title; on duplicates disambiguate
         by the grandparent segment; reject any candidate whose anchored tier is
         not strictly smaller than the child's (C3)        -> resolved, not root
      3. else (parentId dangling / points at VanBan / none) -> ROOT, parentId='' (E)

    Returns (resolved_parent_id, is_root, n_rejected).
    """
    pid = p['parentIdRaw']
    if pid and pid in by_id:
        return pid, False, 0

    n_rejected = 0
    parent_str = p['parentStr']
    if parent_str:
        segs = [s.strip() for s in parent_str.split('/') if s.strip()]
        if segs:
            immediate   = segs[-1]
            grandparent = segs[-2] if len(segs) >= 2 else None
            candidates  = [cid for cid in title_map.get(immediate, []) if cid != p['id']]

            # C2: disambiguate same-title siblings via the grandparent segment.
            if len(candidates) > 1 and grandparent is not None:
                disambig = [cid for cid in candidates
                            if (parent_of.get(cid) or '').rstrip('/').endswith(grandparent)]
                if disambig:
                    candidates = disambig

            # C3: parent must be a STRICTLY smaller tier when both are anchored.
            child_t = anchored_tier.get(p['id'])
            for cid in candidates:
                par_t = anchored_tier.get(cid)
                if child_t is not None and par_t is not None and par_t >= child_t:
                    n_rejected += 1
                    continue
                return cid, False, n_rejected

    # E: no in-document parent resolved → root with empty parentId (CO_CHUA only,
    # never a spurious LA_CON_CUA to a dangling id).
    return '', True, n_rejected


def _worker_2a(fpath, relay_dir):
    """Process one Document_Parts chunk (grouped by document_id upstream):
      1. Group parts by document_id (whole documents — bson_split_by_doc guarantee).
      2. Resolve each part's real parent (parentId / full `parent` path with C2
         disambiguation + C3 tier guard, schema_v7 §4.0), then resolve part_type
         (nominal keyword label) and tier (structural depth) per document (§3 + C4).
      3. MERGE DieuKhoan nodes (auto-commit, disjoint IDs across chunks).
      4. Relay EVERY part {id, document_id, parentId(resolved), is_root} for 2b/3.
    Returns (n_written, n_skipped, n_rejected).
    """
    data = _load_json_file(fpath)
    docs = {}
    n_skipped = 0

    for raw in data:
        part_id = extract_value(raw.get('_id'))
        if not part_id:
            n_skipped += 1
            continue
        doc_id = extract_value(raw.get('document_id')) or safe_get_string(raw, 'document_id')
        parent_id_raw = extract_value(raw.get('parentId')) or safe_get_string(raw, 'parentId')
        if parent_id_raw == part_id:
            parent_id_raw = ''
        title = safe_get_string(raw, 'title') or safe_get_string(raw, 'full_name')
        docs.setdefault(doc_id, []).append({
            'raw':         raw,
            'id':          part_id,
            'parentIdRaw': parent_id_raw,
            'parentStr':   safe_get_string(raw, 'parent'),
            'level':       safe_get_int(raw, 'level'),
            'title':       title,
        })
    del data

    relay_rows = []
    node_batch = []
    n_written  = 0
    n_rejected = 0

    with _proc_driver.session() as session:
        for doc_id, parts in docs.items():
            by_id = {p['id'] for p in parts}

            # C2: keep ALL ids per title (not first-wins); precompute lookups.
            title_map = {}
            parent_of = {}
            anchored_tier = {}
            for p in parts:
                if p['title']:
                    title_map.setdefault(p['title'], []).append(p['id'])
                parent_of[p['id']]     = p['parentStr']
                anchored_tier[p['id']] = _tier_from_title(p['title'])

            for p in parts:
                resolved_parent, is_root, nrej = _resolve_part_parent(
                    p, by_id, title_map, parent_of, anchored_tier)
                p['parentId'] = resolved_parent
                p['isRoot']   = is_root
                n_rejected   += nrej

            tiers = _resolve_part_tiers(parts, anchored_tier)
            for p in parts:
                part_type, tier = tiers[p['id']]
                node_batch.append(_build_dieu_khoan_record(
                    p['raw'], p['id'], doc_id, p['parentId'], p['level'], part_type, tier))

                relay_rows.append({
                    'id':          p['id'],
                    'document_id': doc_id,
                    'parentId':    p['parentId'],
                    'is_root':     p['isRoot'],
                })

                if len(node_batch) >= NODE_BATCH_SIZE:
                    session.run(_DIEU_KHOAN_NODE_Q, batch=node_batch)
                    n_written += len(node_batch)
                    node_batch = []

        if node_batch:
            session.run(_DIEU_KHOAN_NODE_Q, batch=node_batch)
            n_written += len(node_batch)

    _dump_json_file(os.path.join(relay_dir, os.path.basename(fpath)), relay_rows)
    gc.collect()
    return n_written, n_skipped, n_rejected


# ==========================================
# PHASE 2b WORKER — LA_CON_CUA edges
# ==========================================
def _worker_2b(relay_path):
    """Process one Phase 2a relay file → MERGE LA_CON_CUA edges using the
    resolved parent (schema_v7 §4.0/§4.1), not raw parentId.
    Uses execute_write: different chunks may share parent DieuKhoan nodes,
    causing lock contention which execute_write retries automatically.
    Rows with no resolved parent (parentId == '') produce no LA_CON_CUA edge —
    Phase 3 attaches them via CO_CHUA instead (they're flagged is_root, §4.3).
    Returns n_edges written.
    """
    n_edges = 0

    with _proc_driver.session() as session:
        for window in stream_json_file(relay_path):
            batch = [{'id': r['id'], 'parentId': r['parentId']}
                     for r in window if r['parentId']]
            if batch:
                b = batch
                session.execute_write(lambda tx, _b=b: tx.run(_LA_CON_CUA_Q, batch=_b))
                n_edges += len(batch)

    gc.collect()
    return n_edges


# ==========================================
# PHASE 3 WORKER — CO_CHUA edges (root parts, from Phase 2a relay files)
# ==========================================
def _worker_3(relay_path):
    """MERGE CO_CHUA edges from a Phase 2a relay file: VanBan -> root DieuKhoan
    (schema_v7 §4.2/4.3: parts whose resolved parent is absent, including
    orphan reattachment). Uses execute_write: shared VanBan endpoints cause
    lock contention.
    Returns n_edges written.
    """
    n_edges = 0

    with _proc_driver.session() as session:
        for window in stream_json_file(relay_path):
            batch = [{'id': r['id'], 'document_id': r['document_id']}
                     for r in window if r['is_root']]
            if batch:
                b = batch
                session.execute_write(lambda tx, _b=b: tx.run(_CO_CHUA_TOP_Q, batch=_b))
                n_edges += len(batch)

    gc.collect()
    return n_edges


# ==========================================
# PHASE ORCHESTRATORS
# ==========================================
def _run_pool(worker_fn, task_args, desc, n_workers):
    """Submit all tasks to the pool, collect results with a tqdm progress bar.
    Returns list of results in completion order.
    Exceptions from workers are re-raised immediately.
    """
    results = []
    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_init_worker,
        initargs=(URI, AUTH),
    ) as pool:
        futures = {pool.submit(worker_fn, *args): args for args in task_args}
        with tqdm(total=len(futures), desc=desc) as bar:
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f'\n  Worker error ({desc}): {e}')
                finally:
                    bar.update(1)
    return results


def run_phase_1(driver, chunks_dir, n_workers):
    """Full Phase 1: 1a (parallel nodes) → 1b (serial dimensions) → 1c (parallel rels)."""
    files = sorted(glob.glob(os.path.join(chunks_dir, 'Documents_chunk*.json')))
    if not files:
        print(f'No Document chunks found in: {chunks_dir}')
        return {}

    relay_dir = tempfile.mkdtemp(prefix='neo4j_relay_')
    print(f'\nPhase 1 — {len(files)} Document chunks | {n_workers} workers | relay: {relay_dir}')

    try:
        # 1a — VanBan nodes (parallel, disjoint IDs)
        print('  Phase 1a — VanBan nodes...')
        results_1a = _run_pool(_worker_1a,
                               [(f, relay_dir) for f in files],
                               '  1a VanBan nodes', n_workers)
        total_vb  = sum(r[0] for r in results_1a)
        skipped_vb = sum(r[1] for r in results_1a)
        print(f'  1a done — {total_vb:,} VanBan | skipped: {skipped_vb}')

        # 1b — Dimension nodes (serial, eliminates hot-node contention in 1c)
        print('  Phase 1b — dimension nodes (serial)...')
        n_cq, n_lv, n_tt = import_dimension_nodes(driver, relay_dir)
        print(f'  1b done — CoQuan: {n_cq} | LinhVuc: {n_lv} | ThuTuc: {n_tt}')

        # 1c — Relationship edges (parallel + retry from relay files)
        relay_files = sorted(glob.glob(os.path.join(relay_dir, '*.json')))
        print('  Phase 1c — relationship edges (parallel+retry)...')
        results_1c = _run_pool(_worker_1c,
                               [(f,) for f in relay_files],
                               '  1c rels', n_workers)
        total_rels = sum(results_1c)
        print(f'  1c done — {total_rels:,} relay records processed → BAN_HANH_BOI + THUOC_LINH_VUC + QUY_DINH_BOI')

    finally:
        shutil.rmtree(relay_dir, ignore_errors=True)

    return {'van_ban': total_vb, 'skipped': skipped_vb,
            'co_quan': n_cq, 'linh_vuc': n_lv, 'thu_tuc': n_tt}


def run_phase_2a(parts_dir, n_workers):
    """Phase 2a — DieuKhoan nodes (parallel, disjoint IDs). Resolves each part's
    real parent (parentId / full `parent` path, schema_v7 §4.0 + C2/C3/E) and
    part_type/tier (§3 + C4), then writes one relay file per chunk containing
    {id, document_id, parentId(resolved), is_root} for EVERY part — consumed
    by both Phase 2b (LA_CON_CUA) and Phase 3 (CO_CHUA).
    Returns (stats_dict, relay_dir). Caller must rmtree(relay_dir) when done.
    """
    files = sorted(glob.glob(os.path.join(parts_dir, '*.json')))
    if not files:
        print(f'No parts chunks found in: {parts_dir}')
        return {}, None
    relay_dir = tempfile.mkdtemp(prefix='neo4j_parts_relay_')
    print(f'\nPhase 2a — {len(files)} parts chunks | {n_workers} workers → DieuKhoan nodes (v7 tiers + parent resolution + C2/C3/E)')
    results = _run_pool(_worker_2a, [(f, relay_dir) for f in files], '2a DieuKhoan nodes', n_workers)
    total    = sum(r[0] for r in results)
    skipped  = sum(r[1] for r in results)
    rejected = sum(r[2] for r in results)
    print(f'Phase 2a done — {total:,} DieuKhoan | skipped: {skipped} | tier-order candidates rejected (C3): {rejected:,}')
    return {'total': total, 'skipped': skipped, 'rejected': rejected}, relay_dir


def run_phase_2b(relay_dir, n_workers):
    """Phase 2b — LA_CON_CUA edges (parallel + retry), from Phase 2a relay files
    (resolved parent, schema_v7 §4.1). Must run after Phase 2a — all DieuKhoan
    nodes must exist.
    """
    if not relay_dir:
        return {}
    files = sorted(glob.glob(os.path.join(relay_dir, '*.json')))
    if not files:
        return {}
    print(f'\nPhase 2b — {len(files)} relay files | {n_workers} workers → LA_CON_CUA edges')
    results = _run_pool(_worker_2b, [(f,) for f in files], '2b LA_CON_CUA', n_workers)
    total   = sum(results)
    print(f'Phase 2b done — {total:,} LA_CON_CUA edges')
    return {'total': total}


def run_phase_3(relay_dir, n_workers):
    """Phase 3 — CO_CHUA edges: VanBan -> root DieuKhoan (schema_v7 §4.2/4.3).
    Reads the small relay files written by Phase 2a instead of re-parsing the
    full Document_Parts chunks. Must run after Phase 1 (VanBan) and Phase 2a.
    """
    if not relay_dir:
        return {}
    files = sorted(glob.glob(os.path.join(relay_dir, '*.json')))
    if not files:
        return {}
    print(f'\nPhase 3 — {len(files)} relay files | {n_workers} workers → CO_CHUA edges (root parts)')
    results = _run_pool(_worker_3, [(f,) for f in files], '3 CO_CHUA', n_workers)
    total   = sum(results)
    print(f'Phase 3 done — {total:,} CO_CHUA edges')
    return {'total': total}


# ==========================================
# ENTRY POINT
# ==========================================
if __name__ == '__main__':
    import time
    t_start = time.perf_counter()

    print(f'Connecting to Neo4j at {URI}...')
    driver = None
    try:
        driver = GraphDatabase.driver(URI, auth=AUTH)
        driver.verify_connectivity()
    except ServiceUnavailable:
        print(f'\n[ERROR] Cannot connect to Neo4j at {URI}')
        print('  Neo4j is not running. Start it first:')
        print('    • Neo4j Desktop → click "Start" on your database')
        print('    • Or from terminal: neo4j start')
        print(f'\n  Then re-run:')
        print(f'    ".venv\'\\Scripts\\python.exe" import_to_neo4j_v6_parallel.py')
        raise SystemExit(1)
    except AuthError:
        print(f'\n[ERROR] Authentication failed for user "{AUTH[0]}".')
        print(f'  Check the AUTH password in this script (line ~35).')
        raise SystemExit(1)
    except Exception as e:
        print(f'\n[ERROR] Unexpected startup error: {e}')
        import traceback; traceback.print_exc()
        raise SystemExit(1)

    parts_relay_dir = None
    try:
        print(f'Connected. Workers: {N_WORKERS}')

        # ── Phase 0: DDL
        create_constraints(driver)

        # ── Phase 1: VanBan + dimensions + 3 relationship types
        p1 = run_phase_1(driver, 'chunks', N_WORKERS)

        # ── Phase 2a: DieuKhoan nodes (v7 title+parent-resolution tiers; writes relay)
        #     NOTE: parts_chunks MUST be produced by bson_split_by_doc.py so every
        #     document is whole in one file (planning/v6_weird_branches_fix_plan.md).
        p2a, parts_relay_dir = run_phase_2a('parts_chunks', N_WORKERS)

        # ── Phase 2b: LA_CON_CUA (requires all DieuKhoan from 2a; reads 2a relay)
        p2b = run_phase_2b(parts_relay_dir, N_WORKERS)

        # ── Phase 3: CO_CHUA — VanBan -> root DieuKhoan (incl. orphan reattachment)
        p3 = run_phase_3(parts_relay_dir, N_WORKERS)

        elapsed = time.perf_counter() - t_start
        print('\n=== Import Summary ===')
        print(f'  VanBan nodes:        {p1.get("van_ban", 0):,}')
        print(f'  DieuKhoan nodes:     {p2a.get("total", 0):,}')
        print(f'  LA_CON_CUA edges:    {p2b.get("total", 0):,}')
        print(f'  CO_CHUA edges:       {p3.get("total", 0):,}')
        print(f'  Tier-order rejected: {p2a.get("rejected", 0):,} (C3 guard)')
        print(f'  CoQuan nodes:        {p1.get("co_quan", 0)}')
        print(f'  LinhVuc nodes:       {p1.get("linh_vuc", 0)}')
        print(f'  ThuTuc nodes:        {p1.get("thu_tuc", 0)}')
        print()
        print(f'  Workers used:        {N_WORKERS}')
        print(f'  Total wall time:     {elapsed/60:.1f} min ({elapsed:.0f}s)')
        print()
        print('  Active relationships: BAN_HANH_BOI · THUOC_LINH_VUC · QUY_DINH_BOI · LA_CON_CUA · CO_CHUA')
        print('  Deferred: TRICH_DAN · LIEN_KET (blocked) · SUA_DOI · HUONG_DAN (need NLP)')
        print('\nDone.')

    except Exception as e:
        print(f'\n[ERROR] Import failed: {e}')
        import traceback; traceback.print_exc()
    finally:
        if parts_relay_dir:
            shutil.rmtree(parts_relay_dir, ignore_errors=True)
        if driver:
            driver.close()
