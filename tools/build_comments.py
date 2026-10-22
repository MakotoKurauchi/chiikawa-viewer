#!/usr/bin/env python3
"""Build sharded comment JSON (X replies) for the viewer from chiikawa_manga.db.

Called by build_viewer.py; can also run alone:
    python3 build_comments.py [--db PATH] [--out DIR]
Output: DIR/YYYY-MM.json (month of the work's JST date) =
    { "<work id>": {"x": <reply count on X>, "c": [ {id,u,n,t,d,l[,p]} ... ]} }
Only works with at least one fetched post (comment_fetch.status='done') are included.
Returns {work id: collected comment count} for those works (used for the button badge).
"""
import argparse, html, json, re, sqlite3, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "site_comments" / "comments"
LEAD_MENTIONS = re.compile(r"^(?:\s*@\w{1,15})+\s*")
TCO = re.compile(r"https?://t\.co/\w+")


def clean_comment(t):
    t = html.unescape(t or "")
    t = LEAD_MENTIONS.sub("", t)          # "@ngnchiikawa @other ..." reply prefix
    t = TCO.sub("🔗", t)                   # t.co (mostly attached images) -> marker; open on X to see
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if not t.replace("🔗", "").strip():
        return ""                         # image/link only -> viewer shows （画像・リンクのみ）
    return t


def build(db, works, out=DEFAULT_OUT):
    """works: list of dicts with 'id', 'd' (JST 'YYYY-MM-DD HH:MM') and 'p' (ordered post ids)."""
    out = Path(out)
    con = sqlite3.connect(db)
    has = con.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('comments','comment_fetch')").fetchone()[0] == 2
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    counts, shards = {}, {}
    if not has:
        con.close()
        return counts, {"files": 0, "bytes": 0, "comments": 0}
    done = {r[0] for r in con.execute("SELECT post_id FROM comment_fetch WHERE status='done'")}
    rc = dict(con.execute("SELECT id, reply_count FROM posts"))
    by_post = {}
    for r in con.execute("SELECT id, post_id, username, name, text, created_at_jst, like_count FROM comments"):
        by_post.setdefault(r[1], []).append(r)
    con.close()
    total = 0
    for w in works:
        parts = w["p"]
        if not any(p in done for p in parts):
            continue
        cs = []
        for pi, pid in enumerate(parts):
            for cid, _, u, n, t, d, l in by_post.get(pid, []):
                c = {"id": cid, "u": u, "n": n, "t": clean_comment(t), "d": (d or "")[:16], "l": l or 0}
                if len(parts) > 1:
                    c["p"] = pi + 1
                cs.append(c)
        cs.sort(key=lambda c: (-c["l"], c["d"], c["id"]))
        counts[w["id"]] = len(cs)
        total += len(cs)
        shards.setdefault(w["d"][:7], {})[w["id"]] = {"x": sum(rc.get(p) or 0 for p in parts), "c": cs}
    size = 0
    for ym, m in sorted(shards.items()):
        f = out / f"{ym}.json"
        f.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        size += f.stat().st_size
    return counts, {"files": len(shards), "bytes": size, "comments": total}


if __name__ == "__main__":
    import build_viewer
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE.parent / "chiikawa_manga.db"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    works, _ = build_viewer.load_works(a.db)
    counts, st = build(a.db, works, a.out)
    print(f"comments: {st['comments']} in {len(counts)} works -> {st['files']} files, {st['bytes']/1024:.0f} KB at {a.out}")
