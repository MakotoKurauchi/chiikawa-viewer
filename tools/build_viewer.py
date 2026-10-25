#!/usr/bin/env python3
"""Build a self-contained Chiikawa manga viewer HTML from chiikawa_manga.db.

Usage (re-run after each DB update):
    python3 build_viewer.py [--db PATH] [--out PATH]
Defaults: ../chiikawa_manga.db -> ./chiikawa_viewer.html (relative to this script).
"""
import argparse, html, json, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TCO = re.compile(r"\s*https?://t\.co/\w+")
MEDIA = re.compile(r"^https://pbs\.twimg\.com/media/([^./?]+)\.(\w+)")


def clean_text(t):
    t = html.unescape(TCO.sub("", t or ""))
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def img_key(u):
    """Compact form 'KEY.ext' for pbs.twimg.com/media URLs; otherwise full URL."""
    m = MEDIA.match(u)
    return f"{m.group(1)}.{m.group(2)}" if m else u


def load_works(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = {r["id"]: dict(r) for r in con.execute("SELECT * FROM posts")}
    con.close()

    def root_of(pid, seen=()):
        r = rows[pid]
        rid = r.get("thread_root_id")
        if r.get("is_thread_continuation") and rid and rid in rows and rid not in seen:
            return root_of(rid, seen + (pid,))
        return pid

    groups = {}
    for pid in rows:
        groups.setdefault(root_of(pid), []).append(rows[pid])

    works = []
    for rid, parts in groups.items():
        parts.sort(key=lambda r: (r["created_at_utc"] or "", r["id"]))
        root = rows[rid]
        imgs, texts = [], []
        for p in parts:
            try:
                urls = json.loads(p["image_urls"] or "[]")
            except ValueError:
                urls = []
            imgs += [img_key(u) for u in urls]
            t = clean_text(p["text"])
            if t and t not in texts:
                texts.append(t)
        if not imgs:
            continue
        works.append({
            "id": rid,
            "d": (root["created_at_jst"] or "")[:16],
            "t": "\n\n".join(texts),
            "i": imgs,
            "l": root["like_count"] or 0,
            "r": root["repost_count"] or 0,
            "n": len(parts),
            "u": root["url"] or f"https://x.com/ngnchiikawa/status/{rid}",
            "p": [p["id"] for p in parts],   # post ids (for comments; not rendered)
        })
    works.sort(key=lambda w: (w["d"], w["id"]), reverse=True)
    return works, len(rows)


def fmt_n(n):
    if n >= 10000:
        v = f"{n/10000:.{0 if n >= 100000 else 1}f}".removesuffix(".0")
        return v + "万"
    return f"{n:,}"


# Without JS, browsers ignore loading="lazy" (HTML spec: lazy loading needs scripting),
# so only the newest works get inline <img>; older ones get plain image links.
FALLBACK_IMG_WORKS = 60


def full_img(k, size="medium"):
    if k.startswith("http"):
        return k
    key, ext = k.rsplit(".", 1)
    return f"https://pbs.twimg.com/media/{key}?format={ext}&name={size}"


def render_static(works, meta):
    """Plain HTML list of every work. Readable without JavaScript (e.g. iOS Quick Look);
    the JS app parses it as its data source, then removes it."""
    esc = lambda x: html.escape(str(x), quote=True)
    months = []
    for w in works:
        ym = w["d"][:7]
        if not months or months[-1] != ym:
            months.append(ym)
    years = {}
    for ym in months:
        years.setdefault(ym[:4], []).append(ym)
    toc = "".join(
        f'<p><b>{y}年</b> ' + " ".join(f'<a href="#m{ym}">{int(ym[5:])}月</a>' for ym in yms) + "</p>"
        for y, yms in years.items())
    out = [f'<div id="static" data-built="{esc(meta["built"])}" data-posts="{meta["posts"]}" data-works="{meta["works"]}">',
           '<p class="nj-note">かんたん表示です（JavaScript がオフのため検索・ビューアは使えません）。'
           '画像はインターネット接続が必要です。</p>',
           f'<nav class="nj-toc">{toc}</nav>',
           f'<p class="nj-note">新しい {FALLBACK_IMG_WORKS} 話は画像つき、それより前は「🖼 画像」リンクから開けます。</p>']
    cur = None
    for wi, w in enumerate(works):
        ym = w["d"][:7]
        if ym != cur:
            cur = ym
            out.append(f'<h2 id="m{ym}">{ym[:4]}年{int(ym[5:])}月</h2>')
        meta_attrs = f'data-l="{w["l"]}"' + (f' data-r="{w["r"]}"' if w["r"] else "") + (f' data-n="{w["n"]}"' if w["n"] > 1 else "")
        if w.get("c") is not None:
            meta_attrs += f' data-c="{w["c"]}"'   # collected comment count (absent = not collected yet)
        t = f'<p>{esc(w["t"])}</p>' if w["t"] else ""
        if wi < FALLBACK_IMG_WORKS:
            imgs = "".join(f'<img loading="lazy" src="{esc(full_img(k))}" alt="">' for k in w["i"])
        else:
            imgs = '<div class="nj-il">' + " ".join(
                f'<a class="ia" href="{esc(full_img(k, "large"))}">🖼 画像{j + 1 if len(w["i"]) > 1 else ""}</a>'
                for j, k in enumerate(w["i"])) + "</div>"
        out.append(f'<article id="p{w["id"]}" {meta_attrs}><h3><a href="{esc(w["u"])}"><time>{esc(w["d"])}</time></a>'
                   f' <span>♥ {fmt_n(w["l"])}</span></h3>{t}{imgs}</article>')
    out.append('<p class="nj-note"><a href="#top">▲ 上へ</a></p></div>')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE.parent / "chiikawa_manga.db"))
    ap.add_argument("--out", default=str(HERE / "chiikawa_viewer.html"))
    ap.add_argument("--comments-out", default=str(HERE / "site_comments" / "comments"),
                    help="directory for sharded comments/YYYY-MM.json (deploy next to index.html)")
    a = ap.parse_args()
    if not Path(a.db).exists():
        sys.exit(f"DB not found: {a.db}")
    works, nposts = load_works(a.db)
    import build_comments
    ccount, cst = build_comments.build(a.db, works, a.comments_out)
    for w in works:
        w["c"] = ccount.get(w["id"])
    meta = {"built": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
            "posts": nposts, "works": len(works)}
    out = TEMPLATE.replace("<!--__STATIC__-->", render_static(works, meta))
    Path(a.out).write_text(out, encoding="utf-8")
    print(f"wrote {a.out}: {len(works)} works from {nposts} posts, "
          f"{sum(len(w['i']) for w in works)} images, {Path(a.out).stat().st_size/1024:.0f} KB")
    print(f"comments: {cst['comments']} in {len(ccount)} works -> {cst['files']} files, "
          f"{cst['bytes']/1024:.0f} KB at {a.comments_out}")


TEMPLATE = r"""<!doctype html>
<html lang="ja" class="nojs" id="top">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<meta name="theme-color" content="#fffaf2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="ちいかわビューア">
<meta name="application-name" content="ちいかわビューア">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<title>ちいかわ まんがビューア</title>
<script>document.documentElement.className="js";</script>
<style>
:root{--bg:#fffaf2;--card:#fff;--ink:#5a4636;--sub:#9a8676;--pink:#ffc9d6;--pink2:#ff8fab;--blue:#cfe8f7;--blue2:#7dbbe0;--yel:#fff1b3;--line:#f1e4d6;--r:16px}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:"Hiragino Maru Gothic ProN","M PLUS Rounded 1c","Kosugi Maru","Hiragino Sans","Noto Sans JP",system-ui,sans-serif;-webkit-tap-highlight-color:transparent}
body{background-image:radial-gradient(#ffe3ea 1.2px,transparent 1.2px);background-size:22px 22px}
header{position:sticky;top:0;z-index:10;background:rgba(255,250,242,.93);backdrop-filter:blur(8px);border-bottom:2px dashed var(--pink);padding:10px 14px calc(8px)}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:20px;margin:0;letter-spacing:.05em;white-space:nowrap}
h1 .dot{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--pink2);margin-right:6px;box-shadow:16px 0 0 var(--blue2),32px 0 0 #f7d35c;margin-right:42px;vertical-align:1px}
.count{font-size:13px;color:var(--sub);background:var(--yel);border-radius:99px;padding:3px 10px}
.ctrl{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
.ctrl input,.ctrl select,.btn{font:inherit;font-size:14px;color:var(--ink);background:#fff;border:2px solid var(--line);border-radius:99px;padding:6px 12px;outline:none}
.ctrl input:focus,.ctrl select:focus{border-color:var(--pink2)}
.ctrl input[type=search]{flex:1 1 140px;min-width:120px}
.strip{display:contents}
.btn{cursor:pointer;background:var(--pink);border-color:var(--pink);font-weight:bold;transition:transform .1s}
.btn:active{transform:scale(.95)}
.btn.blue{background:var(--blue);border-color:var(--blue)}
.btn.ghost{background:#fff}
main{max-width:1400px;margin:0 auto;padding:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}
.sec{grid-column:1/-1;font-weight:bold;font-size:15px;margin:8px 0 -4px;padding-left:6px;border-left:6px solid var(--pink2)}
.card{background:var(--card);border-radius:var(--r);overflow:hidden;box-shadow:0 3px 0 var(--line),0 2px 10px rgba(150,110,80,.08);cursor:pointer;transition:transform .15s,box-shadow .15s;position:relative;content-visibility:auto;contain-intrinsic-size:auto 260px}
.card:hover{transform:translateY(-3px);box-shadow:0 5px 0 var(--pink),0 6px 16px rgba(150,110,80,.12)}
.card.last{outline:3px solid var(--blue2)}
.thumb{aspect-ratio:1/1;background:#fdf2e6;display:block;width:100%;object-fit:cover;object-position:top}
.meta{display:flex;justify-content:space-between;font-size:12px;padding:7px 10px;color:var(--sub)}
.meta .lk{color:var(--pink2);font-weight:bold}
.badge{position:absolute;top:8px;right:8px;background:rgba(90,70,54,.75);color:#fff;font-size:11px;border-radius:99px;padding:2px 8px}
.empty{text-align:center;color:var(--sub);padding:60px 0}
.foot{text-align:center;color:var(--sub);font-size:12px;padding:20px 0 40px}
.toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:#fff;border:2px solid var(--pink);border-radius:99px;padding:8px 10px 8px 16px;box-shadow:0 4px 14px rgba(0,0,0,.1);display:none;gap:8px;align-items:center;z-index:20;font-size:14px;white-space:nowrap}
.toast.show{display:flex}
.top-btn{position:fixed;right:14px;bottom:18px;z-index:9;width:44px;height:44px;border-radius:50%;padding:0;display:none}
/* modal */
.modal{position:fixed;inset:0;z-index:50;background:rgba(50,38,30,.96);display:none;flex-direction:column}
.modal.open{display:flex}
.mbar{display:flex;align-items:center;gap:8px;padding:8px 10px;color:#fff;font-size:13px;padding-top:max(8px,env(safe-area-inset-top))}
.mbar .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ibtn{background:rgba(255,255,255,.15);color:#fff;border:0;border-radius:99px;font:inherit;font-size:14px;padding:7px 12px;cursor:pointer;text-decoration:none;white-space:nowrap}
.ibtn:hover{background:rgba(255,255,255,.28)}
.stage{flex:1;position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none;min-height:0}
.stage img{max-width:100%;max-height:100%;object-fit:contain;user-select:none;-webkit-user-select:none;-webkit-user-drag:none;-webkit-touch-callout:none;transition:opacity .15s;transform-origin:50% 50%;will-change:transform}
.stage.zoomed .nav{display:none}
.nav{position:absolute;top:50%;transform:translateY(-50%);width:46px;height:46px;border-radius:50%;border:0;background:rgba(255,255,255,.85);color:var(--ink);font-size:22px;cursor:pointer;z-index:2}
.nav.l{left:10px}.nav.r{right:10px}
.spin{position:absolute;color:#fff;opacity:.6;font-size:13px}
.dots{display:flex;justify-content:center;gap:6px;padding:6px}
.dots span{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.35);cursor:pointer}
.dots span.on{background:var(--pink)}
.info{background:var(--bg);color:var(--ink);border-radius:18px 18px 0 0;padding:10px 16px calc(12px + env(safe-area-inset-bottom));max-height:32vh;overflow:auto}
.info .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--sub)}
.info .row b{color:var(--pink2)}
.info p{white-space:pre-wrap;margin:6px 0 2px;font-size:15px;line-height:1.6;word-break:break-word}
.pnav{display:flex;gap:8px;margin-left:auto}
.pnav .btn{font-size:13px;padding:5px 12px}
.xlink{color:var(--blue2);font-weight:bold;text-decoration:none}
.hint{font-size:11px;color:var(--sub);margin-top:4px}
@media (max-width:600px){h1{font-size:17px}.grid{grid-template-columns:repeat(2,1fr);gap:10px}main{padding:10px}.nav{width:38px;height:38px;font-size:18px;opacity:.75}.hint{display:none}
  .m-hide{display:none}
  .ctrl #cont,.ctrl #bmBtn{padding:6px 10px}
  .strip{display:flex;flex:1 1 100%;gap:8px;overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none;-webkit-overflow-scrolling:touch;margin:0 -14px;padding:0 14px 2px}
  .strip::-webkit-scrollbar{display:none}
  .strip>*{flex:none}}
/* no-JS fallback (static list) */
.js #static{display:none}
.nojs header .ctrl,.nojs #count,.nojs main,.nojs .toast,.nojs .top-btn,.nojs .modal{display:none}
#static{max-width:720px;margin:0 auto;padding:10px 12px 40px}
#static h2{scroll-margin-top:60px;font-size:17px;margin:26px 0 8px;padding-left:8px;border-left:6px solid var(--pink2)}
#static article{background:#fff;border-radius:var(--r);padding:10px 10px 6px;margin:0 0 14px;box-shadow:0 3px 0 var(--line)}
#static h3{font-size:13px;font-weight:normal;color:var(--sub);margin:0 0 6px}
#static h3 span{color:var(--pink2);font-weight:bold;margin-left:6px}
#static a{color:var(--blue2)}
#static article p{white-space:pre-wrap;font-size:14px;line-height:1.6;margin:0 0 8px;word-break:break-word}
#static img{display:block;width:100%;height:auto;min-height:120px;background:#fdf2e6;border-radius:10px;margin:0 0 6px}
.nj-note{font-size:13px;color:var(--sub);background:var(--yel);border-radius:12px;padding:8px 12px}
.nj-il{font-size:14px;margin:0 0 6px}.nj-il a{display:inline-block;background:var(--blue);color:var(--ink);text-decoration:none;border-radius:99px;padding:4px 12px;margin:0 6px 4px 0}
.nj-toc{font-size:14px;line-height:1.9}.nj-toc p{margin:0}.nj-toc a{margin-right:6px}
/* しおり / 既読 */
.nojs #bmBtn,.nojs #cont,.nojs .chip,.nojs .sheet,.nojs .snack{display:none}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:14px;background:#fff;border:2px solid var(--line);border-radius:99px;padding:5px 12px;cursor:pointer;user-select:none;white-space:nowrap}
.chip input{margin:0;accent-color:var(--pink2)}
.chip:has(input:checked){border-color:var(--pink2);background:#fff0f4}
.btn .n{display:inline-block;min-width:18px;font-size:11px;background:#fff;color:var(--pink2);border-radius:99px;padding:0 5px;margin-left:3px}
.rib{position:absolute;top:0;left:10px;z-index:1;background:var(--blue2);color:#fff;font-size:13px;line-height:1;padding:4px 5px 11px;clip-path:polygon(0 0,100% 0,100% 100%,50% 78%,0 100%);filter:drop-shadow(0 1px 1px rgba(0,0,0,.2))}
.rib.main{background:var(--pink2);font-size:12px;font-weight:bold;padding:5px 7px 12px;letter-spacing:.05em}
.card.bm-main{box-shadow:0 3px 0 var(--pink),0 0 0 3px var(--pink2),0 2px 10px rgba(150,110,80,.08)}
.rd{display:none;font-style:normal;font-size:10px;color:#fff;background:#9fd3b4;border-radius:99px;padding:1px 6px;margin-left:5px;vertical-align:1px}
.card.read .rd{display:inline-block}
.card.read .thumb{opacity:.86}
.ibtn.on{background:var(--pink);color:var(--ink);font-weight:bold}
.snack{position:fixed;left:50%;bottom:76px;transform:translateX(-50%);z-index:70;background:rgba(90,70,54,.92);color:#fff;font-size:13px;border-radius:99px;padding:8px 16px;max-width:92vw;text-align:center;pointer-events:none;opacity:0;transition:opacity .2s}
.snack.show{opacity:1}
.sheet{position:fixed;inset:0;z-index:40;display:flex;align-items:flex-end;justify-content:center}
.sheet[hidden]{display:none}
.sheet-bg{position:absolute;inset:0;background:rgba(90,70,54,.35)}
.sheet-box{position:relative;background:var(--bg);width:100%;max-width:520px;max-height:86vh;overflow:auto;border-radius:22px 22px 0 0;padding:12px 16px calc(16px + env(safe-area-inset-bottom));box-shadow:0 -4px 20px rgba(0,0,0,.12);border-top:3px dashed var(--pink)}
@media (min-width:601px){.sheet{align-items:center}.sheet-box{border-radius:22px;border:3px dashed var(--pink)}}
.sh-head{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.sh-head b{font-size:17px}
.sh-head .x{margin-left:auto;background:#fff;color:var(--sub);border:2px solid var(--line)}
.wide{width:100%;padding:10px 14px;font-size:15px}
.wide small{font-weight:normal;font-size:12px;color:var(--sub);display:block;margin-top:2px}
.bml{list-style:none;margin:12px 0 0;padding:0}
.bml li{display:flex;align-items:center;gap:10px;background:#fff;border-radius:14px;padding:6px 8px;margin-bottom:8px;box-shadow:0 2px 0 var(--line);cursor:pointer}
.bml li.main{box-shadow:0 0 0 2px var(--pink2),0 2px 0 var(--pink)}
.bml img{width:54px;height:54px;border-radius:10px;object-fit:cover;object-position:top;background:#fdf2e6;flex:none}
.bml .tx{flex:1;min-width:0;font-size:13px;line-height:1.45}
.bml .tx b{font-size:14px}
.bml .tx small{display:block;color:var(--sub);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bml .tag{display:inline-block;background:var(--pink2);color:#fff;font-size:10px;font-weight:bold;border-radius:99px;padding:1px 7px;margin-left:4px;vertical-align:1px}
.bml .del{flex:none;background:#fff;border:2px solid var(--line);color:var(--sub);border-radius:99px;font:inherit;font-size:12px;padding:5px 10px;cursor:pointer}
.sh-empty{font-size:13px;color:var(--sub);background:#fff;border-radius:14px;padding:12px;margin:12px 0 0;line-height:1.6}
/* 💬 comments */
.cmrow{margin:6px 0 2px}
.cmrow .btn{font-size:14px;padding:6px 14px}
.cmrow .btn .n{background:#fff}
.nojs #cmSheet{display:none}
.cms{z-index:60}
.cm-note{font-size:12px;color:var(--sub);margin:0 0 8px;line-height:1.5}
.cml{list-style:none;margin:0;padding:0}
.cml li{background:#fff;border-radius:14px;padding:9px 12px 7px;margin-bottom:8px;box-shadow:0 2px 0 var(--line)}
.cm-h{font-size:13px;line-height:1.4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cm-h b{font-size:14px}
.cm-h small{color:var(--sub);margin-left:4px}
.cm-t{white-space:pre-wrap;word-break:break-word;font-size:15px;line-height:1.6;margin:4px 0}
.cm-f{display:flex;gap:10px;align-items:center;font-size:12px;color:var(--sub)}
.cm-f .lk{color:var(--pink2);font-weight:bold}
.cm-f a{margin-left:auto;color:var(--blue2);font-weight:bold;text-decoration:none;padding:4px 0}
.cm-p{display:inline-block;background:var(--blue);color:var(--ink);font-size:10px;border-radius:99px;padding:1px 7px;margin-right:4px;vertical-align:1px}
.cm-msg{font-size:14px;color:var(--sub);background:#fff;border-radius:14px;padding:14px;line-height:1.7;text-align:center}
.cm-all{display:block;text-align:center;text-decoration:none;margin-top:10px}
.bk{margin-top:14px;background:var(--yel);border-radius:14px;padding:10px 12px}
.bk-t{font-weight:bold;font-size:14px}
.bk-t small{display:block;font-weight:normal;font-size:11px;color:var(--sub);margin-top:2px;line-height:1.5}
.bk-b{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.bk textarea{width:100%;margin-top:8px;font:12px/1.4 ui-monospace,Menlo,monospace;color:var(--ink);border:2px solid var(--line);border-radius:10px;padding:8px;background:#fff;resize:vertical;word-break:break-all}
.bk-msg{font-size:12px;margin:6px 0 0;color:var(--ink);line-height:1.5}
.bk-msg:empty{display:none}
.sheet [hidden]{display:none}
.sh-foot{display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:12px;color:var(--sub)}
.sh-foot .btn{font-size:12px;padding:4px 10px}
/* safe area (iPhone notch / home indicator, standalone mode) */
.chip[hidden],.btn[hidden],.a2hs[hidden]{display:none}
header{padding-top:max(10px,env(safe-area-inset-top));padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right))}
main{padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(14px + env(safe-area-inset-bottom))}
.toast,.top-btn{bottom:calc(18px + env(safe-area-inset-bottom))}
.snack{bottom:calc(76px + env(safe-area-inset-bottom))}
.mbar{padding-left:max(10px,env(safe-area-inset-left));padding-right:max(10px,env(safe-area-inset-right))}
@media (max-width:600px){main{padding-left:max(10px,env(safe-area-inset-left));padding-right:max(10px,env(safe-area-inset-right))}
  .ctrl input[type=search]{min-width:72px;flex-basis:72px}.ctrl #calBtn{padding:6px 10px}}
/* お気に入り / 新着 */
.tw{position:relative}
.fv{position:absolute;right:6px;bottom:6px;z-index:1;width:36px;height:36px;border-radius:50%;border:0;padding:0;background:rgba(255,255,255,.9);color:#c9b8a8;font-size:20px;line-height:36px;cursor:pointer;box-shadow:0 1px 4px rgba(90,70,54,.22)}
.card.fav .fv{color:#f2b100;background:#fff7d1}
.fv:active{transform:scale(.9)}
.nw{display:none;position:absolute;left:6px;bottom:8px;z-index:1;background:#ff6f91;color:#fff;font:bold 11px/1 system-ui,sans-serif;letter-spacing:.08em;border-radius:99px;padding:4px 8px;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.card.new .nw{display:block}
.chip b{color:var(--pink2)}
.chip.nwc{border-color:#ffb3c6;background:#fff4f7}
.btn.sm{font-size:13px;padding:5px 10px}
#mfav{font-size:16px;padding:5px 11px}
#mfav.on{background:#fff1b3;color:#e0a000}
/* カレンダー（年表） */
.cal-top{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.cal-note{font-size:11px;color:var(--sub);margin:6px 0 0;line-height:1.5}
.cy{margin:14px 0 4px}
.cy-h{display:flex;align-items:baseline;gap:8px;width:100%;background:none;border:0;border-left:6px solid var(--pink2);font:inherit;color:var(--ink);padding:2px 4px 2px 8px;cursor:pointer;text-align:left}
.cy-h b{font-size:17px}.cy-h span{font-size:12px;color:var(--sub)}
.cy-h.on b{color:var(--pink2)}
.cm{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}
.mc{display:flex;flex-direction:column;align-items:center;gap:2px;min-height:76px;background:#fff;border:2px solid var(--line);border-radius:12px;padding:5px 2px 4px;font:inherit;color:var(--ink);cursor:pointer;text-align:center}
.mc b{font-size:14px}
.mc .c{font-size:12px;color:var(--pink2);font-weight:bold}
.mc .pg{display:block;width:78%;height:4px;border-radius:9px;background:var(--line);overflow:hidden}
.mc .pg i{display:block;height:100%;background:#9fd3b4}
.mc small{font-size:10px;color:var(--sub);white-space:nowrap}
.mc .ic{font-size:10px;line-height:1.1;min-height:11px;white-space:nowrap}
.mc.done{background:#f0fbf4;border-color:#9fd3b4}
.mc.on{border-color:var(--pink2);box-shadow:0 0 0 2px var(--pink)}
.mc:disabled{opacity:.3;cursor:default;background:transparent;border-style:dashed}
/* ホーム画面に追加 hint */
.a2hs{position:relative;background:#fff;border:2px dashed var(--blue2);border-radius:var(--r);padding:12px 44px 12px 14px;margin:0 0 14px;font-size:13px;line-height:1.65;box-shadow:0 3px 0 var(--line)}
.a2hs b{font-size:14px}
.a2hs p{margin:4px 0 0}
.a2hs .si{display:inline-block;width:18px;height:18px;vertical-align:-4px;margin:0 2px}
.a2hs small{display:block;color:var(--sub);font-size:12px;margin-top:6px;line-height:1.55}
.a2hs .x{position:absolute;top:8px;right:8px;width:30px;height:30px;padding:0;border-radius:50%;background:#fff;color:var(--sub);border:2px solid var(--line);font:inherit;font-size:14px;cursor:pointer}
.a2hs .bk-b .btn{font-size:13px;padding:5px 12px}
.nojs #calBtn,.nojs .a2hs{display:none}
</style>
</head>
<body>
<header>
  <div class="top"><h1><span class="dot"></span>ちいかわ まんがビューア</h1><span class="count" id="count"></span></div>
  <div class="ctrl">
    <input type="search" id="q" placeholder="ことばでさがす…（例：ハチワレ）" autocomplete="off">
    <button class="btn blue" id="cont" title="メインのしおりの次の未読から">▶ 続きから<span class="m-hide">読む</span></button>
    <button class="btn" id="bmBtn" title="しおり一覧">🔖<span class="m-hide"> しおり</span><span class="n" id="bmN">0</span></button>
    <button class="btn ghost" id="calBtn" title="カレンダー（年表）">📅<span class="m-hide"> 年表</span></button>
    <div class="strip">
    <label class="chip nwc" id="nwChip" hidden><input type="checkbox" id="newOnly">🆕 新着 <b id="nwN">0</b>話</label>
    <button class="btn ghost sm" id="nwClr" hidden title="NEW の印をすべて消す">✓ 確認済み</button>
    <label class="chip"><input type="checkbox" id="favOnly">⭐ お気に入り <b id="favN">0</b></label>
    <label class="chip"><input type="checkbox" id="unread">未読のみ</label>
    <select id="year"><option value="">すべての年</option></select>
    <select id="month"><option value="">すべての月</option></select>
    <select id="sort">
      <option value="new">新しい順</option>
      <option value="old">古い順</option>
      <option value="likes">いいね順</option>
    </select>
    <button class="btn" id="rand">🎲 ランダム</button>
    <button class="btn ghost" id="reset" title="条件をリセット">リセット</button>
    </div>
  </div>
</header>
<main><div class="a2hs" id="a2hs" hidden role="note">
  <button class="x" id="a2hsX" aria-label="閉じる">✕</button>
  <b>📱 ホーム画面に追加すると、アプリのように全画面で読めます</b>
  <p>iPhone の Safari で、下の <svg class="si" viewBox="0 0 24 24" fill="none" stroke="#7dbbe0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="共有"><path d="M12 3v12M7.5 7.5 12 3l4.5 4.5"/><path d="M8 10H6a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1h-2"/></svg><b>共有ボタン</b> →「<b>ホーム画面に追加</b>」をタップ。</p>
  <small>※ホーム画面から開いたアプリと Safari では、しおり・お気に入り・既読の保存場所が別々です。Safari 側で「🔖 しおり → 💾 バックアップ → 📋 コピー」したコードを、アプリ側の「📥 読み込み」に貼ると移せます。</small>
  <div class="bk-b"><button class="btn blue" id="a2hsBk">💾 バックアップを開く</button><button class="btn ghost" id="a2hsOk">わかった（もう表示しない）</button></div>
</div><div class="grid" id="grid"></div><div class="empty" id="empty" hidden>みつかりませんでした…</div><div class="foot" id="foot"></div></main>
<div class="toast" id="toast"><span id="toastTxt">前回のつづき</span><button class="btn blue" id="resume">ひらく</button><button class="ibtn" style="color:var(--sub)" id="toastX">✕</button></div>
<button class="btn top-btn" id="toTop" title="上へ">▲</button>
<div class="snack" id="snack" role="status" aria-live="polite"></div>
<div class="sheet" id="sheet" hidden>
  <div class="sheet-bg" id="sheetBg"></div>
  <div class="sheet-box" role="dialog" aria-modal="true" aria-label="しおり">
    <div class="sh-head"><b>🔖 しおり</b><span class="count" id="readStat"></span><button class="btn x" id="shX" aria-label="閉じる">✕</button></div>
    <button class="btn blue wide" id="cont2">▶ 続きから読む<small id="contInfo"></small></button>
    <ul class="bml" id="bml"></ul>
    <button class="btn ghost wide" id="favGo" style="margin-top:4px">⭐ お気に入り一覧を見る（<span id="favN2">0</span>話）</button>
    <p class="sh-empty" id="bmEmpty" hidden>まだしおりがありません。<br>まんがを開いて上の「🔖」ボタンでしおりをはさめます。<br>ファイルを新しくしてしおりが消えたときは、下の「読み込み」に前にコピーしたコードを貼ってください。</p>
    <div class="bk">
      <div class="bk-t">💾 バックアップ<small>新しいファイルに入れかえたり、ホーム画面のアプリと Safari を行き来すると、しおり・お気に入り・既読が見えないことがあります。「コピー」でコードをメモ等に保存し、もう一方で「読み込み」してください。</small></div>
      <div class="bk-b"><button class="btn" id="exp">📋 コピー</button><button class="btn ghost" id="imp">📥 読み込み</button></div>
      <textarea id="code" rows="3" hidden autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>
      <div class="bk-b" id="impRow" hidden><button class="btn blue" id="impGo">このコードを読み込む</button></div>
      <p class="bk-msg" id="bkMsg"></p>
    </div>
    <div class="sh-foot"><span id="bkLast"></span><button class="btn ghost" id="readReset">既読をリセット</button></div>
  </div>
</div>
<div class="sheet" id="calSheet" hidden>
  <div class="sheet-bg" id="calBg"></div>
  <div class="sheet-box" role="dialog" aria-modal="true" aria-label="カレンダー（年表）">
    <div class="sh-head"><b>📅 年表</b><span class="count" id="calStat"></span><button class="btn x" id="calX" aria-label="閉じる">✕</button></div>
    <div class="cal-top"><button class="btn ghost sm" id="calAll">すべての年月を表示</button></div>
    <p class="cal-note">月をタップするとその月の話だけを表示します（年をタップでその年ぜんぶ）。バーと「既読 x/y」は読んだ割合、🆕 新着・🔖 しおり・⭐ お気に入りがある月には印がつきます。</p>
    <div id="calBody"></div>
  </div>
</div>

<!--__STATIC__-->
<div class="sheet cms" id="cmSheet" hidden>
  <div class="sheet-bg" id="cmBg"></div>
  <div class="sheet-box" role="dialog" aria-modal="true" aria-label="コメント">
    <div class="sh-head"><b>💬 コメント</b><span class="count" id="cmStat"></span><button class="btn x" id="cmX" aria-label="閉じる">✕</button></div>
    <p class="cm-note" id="cmNote"></p>
    <div id="cmBody"></div>
    <a class="btn blue wide cm-all" id="cmAll" target="_blank" rel="noopener noreferrer">Xで全部のコメントを見る ↗</a>
  </div>
</div>

<div class="modal" id="modal" role="dialog" aria-modal="true">
  <div class="mbar">
    <span class="grow" id="mpos"></span>
    <button class="ibtn" id="mfav" aria-pressed="false" aria-label="お気に入り" title="お気に入り (F)">☆</button>
    <button class="ibtn" id="mbm" aria-pressed="false">🔖 しおり</button>
    <a class="ibtn" id="mx" target="_blank" rel="noopener noreferrer">Xで見る ↗</a>
    <button class="ibtn" id="mclose" aria-label="閉じる">✕ 閉じる</button>
  </div>
  <div class="stage" id="stage">
    <button class="nav l" id="ml" aria-label="前">‹</button>
    <span class="spin" id="spin">よみこみ中…</span>
    <img id="mimg" alt="" referrerpolicy="no-referrer">
    <button class="nav r" id="mr" aria-label="次">›</button>
  </div>
  <div class="dots" id="dots"></div>
  <div class="info">
    <div class="row"><span id="mdate"></span><b id="mlike"></b><span id="mthread"></span>
      <div class="pnav"><button class="btn ghost" id="pprev">◀ 前の話</button><button class="btn ghost" id="pnext">次の話 ▶</button><button class="btn ghost" id="prand">🎲</button></div></div>
    <p id="mtext"></p>
    <div class="cmrow"><button class="btn ghost" id="mcm" aria-haspopup="dialog">💬 コメント <span class="n" id="mcmN"></span></button></div>
    <div class="hint">← → 画像/話の移動 ・ ↑ ↓ 前後の話 ・ B しおり ・ F お気に入り ・ R ランダム ・ Esc 閉じる</div>
  </div>
</div>

<script>
(function(){
"use strict";
const $ = id => document.getElementById(id);
// data source: the static fallback list (#static), parsed then removed
const S = $("static");
const META = {built: S.dataset.built, posts: +S.dataset.posts, works: +S.dataset.works};
const W = Array.from(S.getElementsByTagName("article"), a => {
  const p = a.querySelector("p"), ds = a.dataset;
  return {id: a.id.slice(1), d: a.querySelector("time").textContent, t: p ? p.textContent : "",
    i: Array.from(a.querySelectorAll("img,a.ia"), e => e.getAttribute(e.tagName === "IMG" ? "src" : "href")),
    l: +ds.l || 0, r: +ds.r || 0, n: +ds.n || 1, u: a.querySelector("h3 a").href, c: ds.c === undefined ? -1 : +ds.c};
});
S.remove();
const LS = "chiikawaViewer.v1";
const fmtN = n => n >= 10000 ? (n/10000).toFixed(n>=100000?0:1).replace(/\.0$/,"") + "万" : n.toLocaleString("ja-JP");
function imgUrl(k, size){
  if (/^https?:/.test(k)) { try{const u=new URL(k);u.searchParams.set("name",size);return u.href;}catch(e){return k;} }
  const i = k.lastIndexOf(".");
  return "https://pbs.twimg.com/media/" + k.slice(0,i) + "?format=" + k.slice(i+1) + "&name=" + size;
}
W.forEach((w,idx) => { w.y = w.d.slice(0,4); w.m = w.d.slice(5,7); w.s = w.t.toLowerCase(); w.o = idx; });
const byId = new Map(W.map(w => [w.id, w]));
const chrono = W.slice().reverse();                 // old -> new
const cpos = new Map(chrono.map((w, i) => [w.id, i]));
const fmtD = w => w.d.slice(0,10).replace(/-/g,"/");
let snT; function snack(msg){ const s = $("snack"); s.textContent = msg; s.classList.add("show"); clearTimeout(snT); snT = setTimeout(() => s.classList.remove("show"), 2600); }

// ---- しおり / 既読 (separate key; b = bookmarks [{id,t,i}], r = read work ids) ----
const MK = "chiikawaViewer.marks.v1";
let mk = {b: [], r: [], x: 0, dirty: 0};
try { const o = JSON.parse(localStorage.getItem(MK) || "null");
  if (o && typeof o === "object") mk = {b: Array.isArray(o.b) ? o.b.filter(b => b && b.id) : [], r: Array.isArray(o.r) ? o.r : [], x: +o.x || 0, dirty: +o.dirty || 0};
} catch(e) {}
const readSet = new Set(mk.r.map(String));
const saveMk = () => { mk.r = [...readSet]; try { localStorage.setItem(MK, JSON.stringify(mk)); } catch(e) {} };
const bmSorted = () => mk.b.filter(b => byId.has(b.id)).sort((a, b) => b.t - a.t);
const mainBm = () => bmSorted()[0] || null;
const isBm = id => mk.b.some(b => b.id === id);
// ---- お気に入り (own key so older viewer files never drop it: {f: [{id, t}]}) ----
const FK = "chiikawaViewer.fav.v1";
const favMap = new Map();
try { const o = JSON.parse(localStorage.getItem(FK) || "null");
  if (o && Array.isArray(o.f)) o.f.forEach(x => { if (x && x.id) favMap.set(String(x.id), +x.t || 0); });
} catch(e) {}
const saveFav = () => { try { localStorage.setItem(FK, JSON.stringify({f: [...favMap].map(([id, t]) => ({id, t}))})); } catch(e) {} };
const favCount = () => W.reduce((n, w) => n + (favMap.has(w.id) ? 1 : 0), 0);
function toggleFav(w){
  if (favMap.has(w.id)) { favMap.delete(w.id); snack("お気に入りからはずしました"); }
  else { favMap.set(w.id, Date.now()); snack("⭐ お気に入りに追加しました（" + fmtD(w) + "）"); }
  saveFav(); mk.dirty = 1; saveMk(); refreshMarks();
}

// ---- 新着 NEW (key chiikawaViewer.new.v1: {b: newest id at last visit, bd: its date, p: {id: firstSeenMs} still-unread NEW, at}) ----
// First visit: just record the baseline. Later visits: works newer than the baseline are NEW; the baseline
// moves to the current newest right away, but unread NEW works stay NEW (up to 14 days) until opened or "✓ 確認済み".
const NK = "chiikawaViewer.new.v1", NEW_KEEP = 14 * 864e5;
const cmpW = (d1, id1, d2, id2) => d1 !== d2 ? (d1 < d2 ? -1 : 1) : id1.length !== id2.length ? id1.length - id2.length : (id1 < id2 ? -1 : id1 > id2 ? 1 : 0);
const newSet = new Set();
let nk = null;
function saveNk(){ try { localStorage.setItem(NK, JSON.stringify(nk)); } catch(e) {} }
(function(){
  try { nk = JSON.parse(localStorage.getItem(NK) || "null"); } catch(e) { nk = null; }
  const top = W[0], now = Date.now(), p = {};
  if (!top) return;
  if (nk && typeof nk === "object" && nk.b) {
    const b = String(nk.b), bw = byId.get(b), bd = bw ? bw.d : String(nk.bd || "");
    const op = nk.p && typeof nk.p === "object" ? nk.p : {};
    W.forEach(w => { if (cmpW(w.d, w.id, bd, b) > 0 && !readSet.has(w.id)) { newSet.add(w.id); p[w.id] = +op[w.id] || now; } });
    for (const id in op) if (byId.has(id) && !readSet.has(id) && now - (+op[id] || 0) < NEW_KEEP) { newSet.add(id); p[id] = +op[id]; }
  }
  nk = {b: top.id, bd: top.d, p, at: now}; saveNk();
})();
function dropNew(id){ if (!newSet.delete(id)) return false; delete nk.p[id]; saveNk(); return true; }
function updNew(){
  const n = newSet.size;
  $("nwN").textContent = n; $("nwChip").hidden = !n; $("nwClr").hidden = !n;
}

function decorate(c, w, mainId){
  c.classList.toggle("read", readSet.has(w.id));
  const fo = favMap.has(w.id), fv = c.querySelector(".fv");
  c.classList.toggle("fav", fo); c.classList.toggle("new", newSet.has(w.id));
  if (fv) { fv.textContent = fo ? "★" : "☆"; fv.setAttribute("aria-pressed", fo); fv.setAttribute("aria-label", fo ? "お気に入りからはずす" : "お気に入りに追加"); }
  const bm = isBm(w.id), main = bm && w.id === mainId;
  c.classList.toggle("bm", bm); c.classList.toggle("bm-main", main);
  let r = c.querySelector(".rib");
  if (!bm) { if (r) r.remove(); return; }
  if (!r) { r = document.createElement("span"); c.appendChild(r); }
  r.className = "rib" + (main ? " main" : ""); r.textContent = main ? "🔖しおり" : "🔖";
}
function refreshMarks(){
  const m = mainBm(), mid = m && m.id;
  document.querySelectorAll("#grid .card").forEach(c => { const w = byId.get(c.dataset.id); if (w) decorate(c, w, mid); });
  $("bmN").textContent = bmSorted().length;
  $("favN").textContent = favCount(); updNew();
  if (cur >= 0 && modal.classList.contains("open")) updMbm();
  if (!$("sheet").hidden) renderSheet();
  if (!$("calSheet").hidden) renderCal();
}
function markRead(w){
  if (dropNew(w.id)) { updNew(); const c = document.querySelector(`#grid .card[data-id="${w.id}"]`); if (c) c.classList.remove("new"); }
  if (readSet.has(w.id)) return;
  readSet.add(w.id); saveMk();
  const c = document.querySelector(`#grid .card[data-id="${w.id}"]`); if (c) c.classList.add("read");
}
function toggleBm(w){
  const k = mk.b.findIndex(b => b.id === w.id);
  if (k >= 0) { mk.b.splice(k, 1); snack("しおりをはずしました"); }
  else { mk.b.push({id: w.id, t: Date.now(), i: ci}); snack("🔖 しおりをはさみました（" + fmtD(w) + "）"); }
  mk.dirty = 1; saveMk(); refreshMarks();
}
function nextUnread(){
  const m = mainBm(); let p = m ? cpos.get(m.id) : -1; if (p == null) p = -1;
  for (let i = p + 1; i < chrono.length; i++) if (!readSet.has(chrono[i].id)) return chrono[i];
  return null;
}

// ---- state ----
let st = {}; try { st = JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch(e) { st = {}; }
const save = () => { try { localStorage.setItem(LS, JSON.stringify(st)); } catch(e) {} };

// ---- controls ----
const years = [...new Set(W.map(w => w.y))].sort().reverse();
years.forEach(y => $("year").add(new Option(y + "年", y)));
for (let m = 1; m <= 12; m++) { const v = String(m).padStart(2,"0"); $("month").add(new Option(m + "月", v)); }
$("q").value = st.q || ""; $("year").value = st.y || ""; $("month").value = st.m || ""; $("sort").value = st.sort || "new"; $("unread").checked = !!st.unread;
$("favOnly").checked = !!st.fav; $("newOnly").checked = !!st.nw && newSet.size > 0;

let list = [];
function filtered(){
  const q = $("q").value.trim().toLowerCase(), y = $("year").value, m = $("month").value, s = $("sort").value;
  const terms = q ? q.split(/\s+/) : [];
  const uo = $("unread").checked, fo = $("favOnly").checked, no = $("newOnly").checked;
  let r = W.filter(w => (!y || w.y === y) && (!m || w.m === m) && (!uo || !readSet.has(w.id)) && (!fo || favMap.has(w.id)) && (!no || newSet.has(w.id)) && terms.every(t => w.s.includes(t)));
  if (s === "old") r = r.slice().reverse();
  else if (s === "likes") r = r.slice().sort((a,b) => b.l - a.l);
  return r;
}
function render(){
  list = filtered();
  st.q = $("q").value; st.y = $("year").value; st.m = $("month").value; st.sort = $("sort").value; st.unread = $("unread").checked; st.fav = $("favOnly").checked; st.nw = $("newOnly").checked; save();
  const mb = mainBm(), mid = mb && mb.id;
  $("count").textContent = list.length === W.length ? `全 ${W.length.toLocaleString()} 話` : `${list.length.toLocaleString()} / ${W.length.toLocaleString()} 話`;
  const g = $("grid"), frag = document.createDocumentFragment(), bySec = $("sort").value !== "likes";
  let sec = "";
  list.forEach((w, i) => {
    if (bySec) { const k = w.y + "年" + (+w.m) + "月"; if (k !== sec) { sec = k; const h = document.createElement("div"); h.className = "sec"; h.textContent = k; frag.appendChild(h); } }
    const c = document.createElement("div"); c.className = "card"; c.dataset.i = i; c.dataset.id = w.id;
    if (w.id === st.lastId) c.classList.add("last");
    const im = document.createElement("img"); im.className = "thumb"; im.loading = "lazy"; im.decoding = "async";
    im.referrerPolicy = "no-referrer"; im.alt = w.t.slice(0,40); im.src = imgUrl(w.i[0], "medium");
    const tw = document.createElement("div"); tw.className = "tw";
    const nw = document.createElement("span"); nw.className = "nw"; nw.textContent = "NEW";
    const fv = document.createElement("button"); fv.className = "fv"; fv.type = "button";
    tw.append(im, nw, fv); c.appendChild(tw);
    if (w.i.length > 1) { const b = document.createElement("span"); b.className = "badge"; b.textContent = "🖼 " + w.i.length; c.appendChild(b); }
    const mt = document.createElement("div"); mt.className = "meta";
    mt.innerHTML = `<span>${w.d.slice(0,10).replace(/-/g,"/")}<i class="rd" title="既読">✓既読</i></span><span class="lk">♥ ${fmtN(w.l)}</span>`;
    c.appendChild(mt); decorate(c, w, mid); frag.appendChild(c);
  });
  g.replaceChildren(frag);
  $("empty").hidden = list.length > 0;
  $("empty").textContent = list.length ? "" : $("favOnly").checked && !favCount() ? "お気に入りはまだありません。カードの ☆ をタップすると追加できます。"
    : $("newOnly").checked ? "新着はありません" : $("unread").checked ? "未読はありません 🎉" : "みつかりませんでした…";
}
let tmr; $("q").addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(() => { render(); scrollTo(0,0); }, 200); });
["year","month","sort","unread","favOnly","newOnly"].forEach(id => $(id).addEventListener("change", () => { render(); scrollTo(0,0); }));
$("reset").onclick = () => { $("q").value = ""; $("year").value = ""; $("month").value = ""; $("sort").value = "new"; $("unread").checked = false; $("favOnly").checked = false; $("newOnly").checked = false; render(); scrollTo(0,0); };
$("grid").addEventListener("click", e => { const f = e.target.closest(".fv"); if (f) { e.stopPropagation(); const w = byId.get(f.closest(".card").dataset.id); if (w) toggleFav(w); return; } const c = e.target.closest(".card"); if (c) openAt(+c.dataset.i, 0); });
$("rand").onclick = () => list.length && openAt(Math.floor(Math.random() * list.length), 0);
$("toTop").onclick = () => scrollTo({top:0, behavior:"smooth"});
let stmr; addEventListener("scroll", () => { $("toTop").style.display = scrollY > 800 ? "block" : "none"; clearTimeout(stmr); stmr = setTimeout(() => { st.scroll = scrollY; save(); }, 300); }, {passive:true});

// ---- modal ----
let cur = -1, ci = 0;
const modal = $("modal"), mimg = $("mimg");
function openAt(i, imgIdx){
  if (i < 0 || i >= list.length) return;
  cur = i; const w = list[i]; ci = imgIdx < 0 ? w.i.length - 1 : Math.min(imgIdx, w.i.length - 1);
  modal.classList.add("open"); document.body.style.overflow = "hidden";
  $("mpos").textContent = `${i + 1} / ${list.length} 話目`;
  $("mx").href = w.u;
  $("mdate").textContent = w.d.replace(/-/g,"/");
  $("mlike").textContent = "♥ " + w.l.toLocaleString("ja-JP");
  $("mthread").textContent = w.n > 1 ? `🧵 ${w.n}ポストのスレッド` : "";
  $("mtext").textContent = w.t || "";
  $("mcmN").textContent = w.c >= 0 ? w.c.toLocaleString("ja-JP") : "未";
  $("pprev").disabled = i === 0; $("pnext").disabled = i === list.length - 1;
  document.querySelectorAll(".card.last").forEach(c => c.classList.remove("last"));
  const card = document.querySelector(`.card[data-id="${w.id}"]`); if (card) card.classList.add("last");
  st.lastId = w.id; save();
  markRead(w); updMbm();
  if (location.hash !== "#" + w.id) try { history.replaceState(null, "", "#" + w.id); } catch(e) {}
  showImg();
}
function updMbm(){
  const w = list[cur]; if (!w) return;
  const on = isBm(w.id), m = mainBm(), b = $("mbm");
  b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
  b.textContent = on ? (m && m.id === w.id ? "🔖 しおり中" : "🔖 しおり済") : "🔖 しおり";
  const f = $("mfav"), fo = favMap.has(w.id);
  f.classList.toggle("on", fo); f.setAttribute("aria-pressed", fo); f.textContent = fo ? "★" : "☆";
}
function showImg(){
  const w = list[cur], k = w.i[ci];
  const d = $("dots"); d.replaceChildren(...(w.i.length > 1 ? w.i.map((_, j) => { const s = document.createElement("span"); if (j === ci) s.className = "on"; s.onclick = () => { ci = j; showImg(); }; return s; }) : []));
  $("spin").style.display = "block";
  zReset();
  mimg.style.opacity = .35;
  mimg.src = imgUrl(k, "medium");      // fast preview
  const full = new Image(); full.referrerPolicy = "no-referrer";
  full.onload = () => { if (list[cur] === w && w.i[ci] === k) { mimg.src = full.src; mimg.style.opacity = 1; $("spin").style.display = "none"; } };
  full.onerror = () => { if (list[cur] === w && w.i[ci] === k) { mimg.style.opacity = 1; $("spin").textContent = "画像を読み込めませんでした"; } };
  full.src = imgUrl(k, "orig");
  mimg.onload = () => { if (mimg.src.includes("name=medium")) mimg.style.opacity = .85; };
  $("ml").disabled = cur === 0 && ci === 0; $("mr").disabled = cur === list.length - 1 && ci === w.i.length - 1;
  // preload neighbours
  const nx = ci + 1 < w.i.length ? w.i[ci + 1] : (list[cur + 1] && list[cur + 1].i[0]);
  if (nx) { const p = new Image(); p.referrerPolicy = "no-referrer"; p.src = imgUrl(nx, "orig"); }
  st.img = ci; save();
}
function step(dir){
  const w = list[cur];
  if (dir > 0) { if (ci < w.i.length - 1) { ci++; showImg(); } else if (cur < list.length - 1) openAt(cur + 1, 0); }
  else { if (ci > 0) { ci--; showImg(); } else if (cur > 0) openAt(cur - 1, -1); }
}
function close(){
  modal.classList.remove("open"); document.body.style.overflow = "";
  try { history.replaceState(null, "", location.href.split("#")[0]); } catch(e) {}
  const card = document.querySelector(`.card[data-id="${st.lastId}"]`);
  if (card) { const r = card.getBoundingClientRect(); if (r.top < 120 || r.bottom > innerHeight) card.scrollIntoView({block:"center"}); }
}
$("ml").onclick = e => { e.stopPropagation(); step(-1); };
$("mr").onclick = e => { e.stopPropagation(); step(1); };
$("pprev").onclick = () => openAt(cur - 1, 0);
$("pnext").onclick = () => openAt(cur + 1, 0);
$("prand").onclick = () => openAt(Math.floor(Math.random() * list.length), 0);
$("mclose").onclick = close;
$("mbm").onclick = () => { if (list[cur]) toggleBm(list[cur]); };
$("mfav").onclick = () => { if (list[cur]) toggleFav(list[cur]); };
$("stage").addEventListener("click", e => { if (e.target === $("stage") && !zNoClick()) close(); });
mimg.addEventListener("pointerdown", e => { lastPT = e.pointerType; });
mimg.addEventListener("click", e => {
  if (zNoClick()) return;
  const r = mimg.getBoundingClientRect(), dir = e.clientX - r.left < r.width / 3 ? -1 : 1;
  if (lastPT === "touch") { clearTimeout(tapT); tapT = setTimeout(() => { if (!zNoClick()) step(dir); }, 300); }   // wait: may be a double-tap zoom
  else step(dir);
});
addEventListener("keydown", e => {
  if (!$("cmSheet").hidden) { if (e.key === "Escape") closeCm(); return; }
  if (!$("sheet").hidden) { if (e.key === "Escape") closeSheet(); return; }
  if (!$("calSheet").hidden) { if (e.key === "Escape") closeCal(); return; }
  if (!modal.classList.contains("open")) { if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); } return; }
  if (e.key === "ArrowRight" || e.key === "l") step(1);
  else if (e.key === "ArrowLeft" || e.key === "h") step(-1);
  else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); openAt(cur + 1, 0); }
  else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); openAt(cur - 1, 0); }
  else if (e.key === "r" || e.key === "R") $("prand").onclick();
  else if (e.key === "b" || e.key === "B") $("mbm").onclick();
  else if (e.key === "f" || e.key === "F") $("mfav").onclick();
  else if (e.key === "Escape") close();
});
// pinch zoom + pan (custom, so the zoomed image can be dragged all the way to its edges)
const stage = $("stage");
let zs = 1, zx = 0, zy = 0, zLast = 0;
function zApply(){ mimg.style.transform = zs === 1 && !zx && !zy ? "" : `translate(${zx}px,${zy}px) scale(${zs})`; stage.classList.toggle("zoomed", zs > 1.01); }
function zClamp(){
  const sw = stage.clientWidth, sh = stage.clientHeight, w = mimg.offsetWidth * zs, h = mimg.offsetHeight * zs;
  const mx = Math.max(0, (w - sw) / 2), my = Math.max(0, (h - sh) / 2);
  zx = Math.min(mx, Math.max(-mx, zx)); zy = Math.min(my, Math.max(-my, zy));
}
function zReset(){ zs = 1; zx = 0; zy = 0; zApply(); }
function zNoClick(){ return zs > 1.01 || Date.now() - zLast < 350; }
function zCenter(){ const r = stage.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }
// zoom to scale ns keeping screen point (px,py) fixed
function zTo(ns, px, py){
  ns = Math.min(6, Math.max(1, ns));
  const [cx, cy] = zCenter(), qx = (px - cx - zx) / zs, qy = (py - cy - zy) / zs;
  zs = ns; zx = px - cx - zs * qx; zy = py - cy - zs * qy;
  if (zs <= 1.01) { zs = 1; zx = 0; zy = 0; }
  zClamp(); zApply();
}
let tapT = 0, lastPT = "";
let g = null, sx = 0, sy = 0, tracking = false, lastTap = 0;
const tdist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
stage.addEventListener("touchstart", e => {
  const t = e.touches;
  if (t.length === 2) {
    tracking = false;
    const [cx, cy] = zCenter(), mx = (t[0].clientX + t[1].clientX) / 2, my = (t[0].clientY + t[1].clientY) / 2;
    g = {m:"pinch", d0:tdist(t[0], t[1]) || 1, s0:zs, qx:(mx - cx - zx) / zs, qy:(my - cy - zy) / zs};
  } else if (t.length === 1) {
    sx = t[0].clientX; sy = t[0].clientY;
    if (zs > 1.01) { g = {m:"pan", x0:zx, y0:zy, moved:false}; tracking = false; }
    else { g = null; tracking = e.target === mimg || e.target === stage; }
  }
}, {passive:true});
stage.addEventListener("touchmove", e => {
  const t = e.touches;
  if (g && g.m === "pinch" && t.length >= 2) {
    e.preventDefault();
    const [cx, cy] = zCenter(), mx = (t[0].clientX + t[1].clientX) / 2, my = (t[0].clientY + t[1].clientY) / 2;
    zs = Math.min(6, Math.max(1, g.s0 * tdist(t[0], t[1]) / g.d0));
    zx = mx - cx - zs * g.qx; zy = my - cy - zs * g.qy;
    zClamp(); zApply(); zLast = Date.now();
  } else if (g && g.m === "pan" && t.length === 1) {
    e.preventDefault();
    const dx = t[0].clientX - sx, dy = t[0].clientY - sy;
    if (Math.abs(dx) + Math.abs(dy) > 6) g.moved = true;
    zx = g.x0 + dx; zy = g.y0 + dy; zClamp(); zApply(); zLast = Date.now();
  }
}, {passive:false});
stage.addEventListener("touchend", e => {
  if (g && g.m === "pinch") {
    if (e.touches.length === 1) { sx = e.touches[0].clientX; sy = e.touches[0].clientY; g = zs > 1.01 ? {m:"pan", x0:zx, y0:zy, moved:true} : null; }
    else if (!e.touches.length) { if (zs <= 1.05) zReset(); g = null; }
    zLast = Date.now(); return;
  }
  if (g && g.m === "pan") { if (!e.touches.length) g = null; return; }
  if (!tracking) return; tracking = false;
  const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.3) { zLast = Date.now(); step(dx < 0 ? 1 : -1); }
  else if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.5) { zLast = Date.now(); close(); }
}, {passive:true});
// double-tap: zoom in at that spot / back to fit
stage.addEventListener("touchend", e => {
  if (e.touches.length || e.changedTouches.length !== 1) return;
  const c = e.changedTouches[0], now = Date.now();
  if (Math.abs(c.clientX - sx) > 10 || Math.abs(c.clientY - sy) > 10) { lastTap = 0; return; }
  if (now - lastTap < 300 && (e.target === mimg || e.target === stage)) {
    lastTap = 0; e.preventDefault();
    if (zs > 1.01) zReset(); else zTo(2.5, c.clientX, c.clientY);
    zLast = now; clearTimeout(tapT);
  } else lastTap = now;
}, {passive:false});
["gesturestart", "gesturechange"].forEach(n => stage.addEventListener(n, e => e.preventDefault()));
// desktop: ctrl/trackpad-pinch wheel zooms, plain wheel pans when zoomed
stage.addEventListener("wheel", e => {
  if (e.ctrlKey) { e.preventDefault(); zTo(zs * Math.exp(-e.deltaY / 100), e.clientX, e.clientY); zLast = Date.now(); }
  else if (zs > 1.01) { e.preventDefault(); zx -= e.deltaX; zy -= e.deltaY; zClamp(); zApply(); }
}, {passive:false});
addEventListener("resize", () => { if (zs > 1) { zClamp(); zApply(); } });
// ---- 💬 comments (X replies), loaded on demand from comments/YYYY-MM.json ----
const cmCache = new Map();   // "YYYY-MM" -> Promise<object>
function loadShard(ym){
  if (!cmCache.has(ym)) {
    const pr = fetch("comments/" + ym + ".json", {cache: "no-cache"})
      .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    pr.catch(() => cmCache.delete(ym));   // retry next time
    cmCache.set(ym, pr);
  }
  return cmCache.get(ym);
}
function cmMsg(html){ const d = document.createElement("div"); d.className = "cm-msg"; d.innerHTML = html; return d; }
function renderCm(w, data){
  const body = $("cmBody"), e = data && data[w.id];
  const cs = e ? e.c.slice() : [];
  cs.sort((a, b) => (b.l - a.l) || (a.d < b.d ? -1 : a.d > b.d ? 1 : 0));
  $("cmStat").textContent = cs.length ? `${cs.length.toLocaleString("ja-JP")}件` : "";
  $("cmNote").textContent = e ? `いいねが多い順。Xにある返信 ${e.x.toLocaleString("ja-JP")}件のうち、集めた${cs.length.toLocaleString("ja-JP")}件だけを表示しています。` : "";
  if (!cs.length) { body.replaceChildren(cmMsg("このお話へのコメントは見つかりませんでした。<br>Xで見てみてください。")); return; }
  const ul = document.createElement("ul"); ul.className = "cml";
  for (const c of cs) {
    const li = document.createElement("li"); li.dataset.l = c.l; li.dataset.id = c.id;
    const h = document.createElement("div"); h.className = "cm-h";
    if (c.p) { const t = document.createElement("span"); t.className = "cm-p"; t.textContent = `🧵 ${c.p}枚目へ`; h.append(t); }
    const nb = document.createElement("b"); nb.textContent = c.n || c.u;
    const ub = document.createElement("small"); ub.textContent = "@" + c.u;
    h.append(nb, ub);
    const p = document.createElement("p"); p.className = "cm-t"; p.textContent = c.t || "（画像・リンクのみ）";
    const f = document.createElement("div"); f.className = "cm-f";
    const dd = document.createElement("span"); dd.textContent = c.d.replace(/-/g, "/");
    const lk = document.createElement("span"); lk.className = "lk"; lk.textContent = "♡ " + c.l.toLocaleString("ja-JP");
    const a = document.createElement("a"); a.target = "_blank"; a.rel = "noopener noreferrer";
    a.href = `https://x.com/${encodeURIComponent(c.u || "i")}/status/${encodeURIComponent(c.id)}`; a.textContent = "Xで開く ↗";
    f.append(dd, lk, a);
    li.append(h, p, f); ul.append(li);
  }
  body.replaceChildren(ul);
}
function openCm(){
  const w = list[cur]; if (!w) return;
  $("cmAll").href = w.u; $("cmStat").textContent = ""; $("cmNote").textContent = "";
  $("cmSheet").hidden = false; $("cmSheet").dataset.id = w.id;
  const body = $("cmBody");
  if (w.c < 0) { body.replaceChildren(cmMsg("このお話のコメントは<b>まだ集めていません</b>。<br>下のボタンからXで見られます。")); return; }
  body.replaceChildren(cmMsg("よみこみ中…"));
  loadShard(w.d.slice(0, 7)).then(data => { if ($("cmSheet").dataset.id === w.id && !$("cmSheet").hidden) renderCm(w, data); },
    () => { if ($("cmSheet").dataset.id === w.id) body.replaceChildren(cmMsg(location.protocol === "file:"
      ? "ファイルを直接ひらいているため、コメントを読み込めませんでした。<br>Webページ版（https://…）でひらくと見られます。"
      : "コメントを読み込めませんでした 🙇<br>通信のよいところで、もう一度ためしてください。")); });
}
function closeCm(){ $("cmSheet").hidden = true; }
$("mcm").onclick = openCm;
$("cmX").onclick = closeCm; $("cmBg").onclick = closeCm;

// ---- open by id (clears filters if needed) ----
function openWork(id, img){
  let i = list.findIndex(w => w.id === id);
  if (i < 0) { $("q").value = ""; $("year").value = ""; $("month").value = ""; $("unread").checked = false; $("favOnly").checked = false; $("newOnly").checked = false; render(); i = list.findIndex(w => w.id === id); }
  if (i >= 0) openAt(i, img || 0);
}
function cont(){
  const w = nextUnread();
  if (!w) { snack(mainBm() ? "しおりより後はぜんぶ読みました 🎉" : "未読はありません 🎉"); return; }
  closeSheet();
  if ($("sort").value !== "old") { $("sort").value = "old"; render(); snack("古い順にならべかえました（次の話 ▶ で続きへ）"); }
  openWork(w.id, 0);
}
$("cont").onclick = cont; $("cont2").onclick = cont;

// ---- しおり panel ----
const fmtT = t => { const d = new Date(t); return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; };
function renderSheet(){
  const bs = bmSorted(), ul = $("bml");
  $("readStat").textContent = `既読 ${W.filter(w => readSet.has(w.id)).length.toLocaleString()} / ${W.length.toLocaleString()} 話`;
  const nx = nextUnread(), m = bs[0];
  $("contInfo").textContent = nx ? `${m ? "しおり（" + fmtD(byId.get(m.id)) + "）の次の未読" : "いちばん古い未読"}：${fmtD(nx)}` : (m ? "しおりより後はぜんぶ読みました 🎉" : "未読はありません 🎉");
  ul.replaceChildren(...bs.map((b, k) => {
    const w = byId.get(b.id), li = document.createElement("li");
    li.dataset.id = b.id; if (k === 0) li.className = "main";
    const im = document.createElement("img"); im.loading = "lazy"; im.referrerPolicy = "no-referrer"; im.alt = ""; im.src = imgUrl(w.i[Math.min(b.i || 0, w.i.length - 1)], "small");
    const tx = document.createElement("div"); tx.className = "tx";
    tx.innerHTML = `<b></b>${k === 0 ? '<span class="tag">メイン</span>' : ""}<small></small><small></small>`;
    tx.children[0].textContent = "📖 " + fmtD(w) + (w.i.length > 1 && b.i ? `（${b.i + 1}枚目）` : "");
    const sm = tx.querySelectorAll("small");
    sm[0].textContent = `🔖 ${fmtT(b.t)} にはさんだ`; sm[1].textContent = w.t.replace(/\s+/g, " ").slice(0, 40);
    const del = document.createElement("button"); del.className = "del"; del.textContent = "けす"; del.dataset.del = b.id; del.setAttribute("aria-label", "しおりを削除");
    li.append(im, tx, del); return li;
  }));
  $("bmEmpty").hidden = bs.length > 0;
  $("favN2").textContent = favCount();
  $("bkLast").textContent = mk.dirty && (bs.length || favMap.size) ? "⚠️ しおり・お気に入りを変えたあと、まだコピーしていません" : (mk.x ? `最後のコピー：${fmtT(mk.x)}` : "");
}
function openSheet(){ renderSheet(); $("sheet").hidden = false; document.body.style.overflow = "hidden"; }
function closeSheet(){ if ($("sheet").hidden) return; $("sheet").hidden = true; document.body.style.overflow = ""; $("code").hidden = true; $("impRow").hidden = true; $("bkMsg").textContent = ""; }
$("bmBtn").onclick = openSheet; $("shX").onclick = closeSheet; $("sheetBg").onclick = closeSheet;
$("bml").addEventListener("click", e => {
  const d = e.target.closest("[data-del]");
  if (d) { e.stopPropagation(); mk.b = mk.b.filter(b => b.id !== d.dataset.del); mk.dirty = 1; saveMk(); refreshMarks(); snack("しおりをけしました"); return; }
  const li = e.target.closest("li"); if (!li) return;
  const b = mk.b.find(x => x.id === li.dataset.id); closeSheet(); if (b) openWork(b.id, b.i || 0);
});
$("favGo").onclick = () => { closeSheet(); $("q").value = ""; $("year").value = ""; $("month").value = ""; $("unread").checked = false; $("newOnly").checked = false; $("favOnly").checked = true; render(); scrollTo(0,0); };
$("readReset").onclick = () => { if (!confirm("既読の記録をすべてけしますか？（しおりは残ります）")) return; readSet.clear(); saveMk(); refreshMarks(); render(); snack("既読をリセットしました"); };

// ---- backup code: "CK1." / "CK2." + base64url(JSON) ; ids in base36, read = ranges over old->new order ----
// CK2 = CK1 + f: [[id36, addedMs36], ...] (favorites). Without favorites we still emit CK1 so older viewer files can import it.
const B36 = typeof BigInt === "function";
const enc36 = id => { if (B36 && /^\d+$/.test(id)) return BigInt(id).toString(36); return "~" + id; };
const dec36 = s => { if (s[0] === "~" || !B36) return s.replace(/^~/, ""); let n = BigInt(0); const b = BigInt(36); for (const ch of s) { const d = parseInt(ch, 36); if (isNaN(d)) throw new Error("bad id"); n = n * b + BigInt(d); } return n.toString(); };
const CODE_MAX_READ = 6000;
function makeCode(){
  const o = {v: 1, b: mk.b.map(b => [enc36(b.id), Math.round(b.t).toString(36), b.i || 0])};
  const parts = []; let s = -1;
  for (let i = 0; i <= chrono.length; i++) {
    const rd = i < chrono.length && readSet.has(chrono[i].id);
    if (rd && s < 0) s = i;
    if (!rd && s >= 0) { parts.push(enc36(chrono[s].id) + (i - 1 > s ? "-" + enc36(chrono[i - 1].id) : "")); s = -1; }
  }
  readSet.forEach(id => { if (!cpos.has(id)) parts.push(enc36(id)); });   // ids from other builds
  const r = parts.join(",");
  if (r.length <= CODE_MAX_READ) o.r = r; else o.ro = 1;                  // too long -> bookmarks only
  if (st.lastId) o.l = enc36(st.lastId);
  const fl = [...favMap].map(([id, t]) => [enc36(id), Math.round(t).toString(36)]);
  if (fl.length) { o.v = 2; o.f = fl; }
  return {code: "CK" + o.v + "." + btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""), omitted: !!o.ro};
}
function applyCode(txt){
  const m = /CK([12])\.([A-Za-z0-9_-]+)/.exec(String(txt || "").replace(/\s+/g, ""));
  if (!m) throw new Error("format");
  let b64 = m[2].replace(/-/g, "+").replace(/_/g, "/"); while (b64.length % 4) b64 += "=";
  const o = JSON.parse(atob(b64));
  if (!o || (o.v !== 1 && o.v !== 2) || !Array.isArray(o.b)) throw new Error("format");
  let nb = 0, nr = 0, nf = 0;
  if (Array.isArray(o.f)) o.f.forEach(([id36, t36]) => { const id = dec36(String(id36)); if (!favMap.has(id)) { favMap.set(id, parseInt(t36, 36) || 0); nf++; } });
  o.b.forEach(([id36, t36, i]) => {
    const id = dec36(String(id36)), t = parseInt(t36, 36) || 0, ex = mk.b.find(x => x.id === id);
    if (ex) { if (t > ex.t) { ex.t = t; ex.i = +i || 0; } } else { mk.b.push({id, t, i: +i || 0}); nb++; }
  });
  const add = id => { if (!readSet.has(id)) { readSet.add(id); nr++; } };
  if (typeof o.r === "string" && o.r) o.r.split(",").forEach(p => {
    const [a, z] = p.split("-").map(dec36);
    if (z === undefined) return add(a);
    const pa = cpos.get(a), pz = cpos.get(z);
    if (pa != null && pz != null) for (let i = Math.min(pa, pz); i <= Math.max(pa, pz); i++) add(chrono[i].id);
    else { add(a); add(z); }
  });
  if (o.l && !st.lastId) { st.lastId = dec36(o.l); save(); }
  [...newSet].forEach(id => { if (readSet.has(id)) dropNew(id); });
  saveFav(); saveMk(); refreshMarks(); render();
  return {nb, nr, nf, ro: !!o.ro};
}
let codeMode = "";
$("exp").onclick = () => {
  const {code, omitted} = makeCode(), ta = $("code"), msg = $("bkMsg");
  codeMode = "exp"; ta.value = code; ta.readOnly = true; $("impRow").hidden = true;
  const done = ok => {
    if (ok) { mk.x = Date.now(); mk.dirty = 0; saveMk(); renderSheet(); }
    msg.textContent = (ok ? "✅ コードをコピーしました。メモ帳などに貼って保存してね。" : "下のコードを長押し →「すべてを選択」→「コピー」してください。")
      + (omitted ? "（既読の記録が多すぎるため、しおりだけ入っています）" : "") + `（${code.length}文字）`;
    ta.hidden = false; if (!ok) { ta.focus(); ta.select(); try { ta.setSelectionRange(0, code.length); } catch(e) {} }
  };
  const legacy = () => { ta.hidden = false; ta.focus(); ta.select(); try { ta.setSelectionRange(0, code.length); return document.execCommand("copy"); } catch(e) { return false; } };
  if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext !== false)
    navigator.clipboard.writeText(code).then(() => done(true), () => done(legacy()));
  else done(legacy());
};
$("imp").onclick = () => {
  const ta = $("code");
  codeMode = "imp"; ta.readOnly = false; ta.value = ""; ta.placeholder = "ここにコード（CK2. または CK1. からはじまる文字）を貼りつけ"; ta.hidden = false; $("impRow").hidden = false;
  $("bkMsg").textContent = "コピーしておいたコードを貼りつけて「このコードを読み込む」をおしてください。今のしおり・お気に入りは消えず、合わせて追加されます。";
  ta.focus();
};
$("impGo").onclick = () => {
  try {
    const {nb, nr, nf, ro} = applyCode($("code").value);
    $("bkMsg").textContent = `✅ 読み込みました：しおり +${nb}、お気に入り +${nf}、既読 +${nr}` + (ro ? "（このコードには既読が入っていません）" : "");
    $("code").hidden = true; $("impRow").hidden = true; renderSheet(); snack("🔖 しおりを読み込みました");
  } catch(e) { $("bkMsg").textContent = "⚠️ コードを読み込めませんでした。CK2.（または CK1.）からはじまるコードをまるごと貼りつけてください。"; }
};

// ---- カレンダー（年表）: years -> months with counts and 既読 progress (JST dates) ----
const pad2 = n => String(n).padStart(2, "0");
function renderCal(){
  const by = new Map();
  for (const w of W) {
    const k = w.y + "-" + w.m; let o = by.get(k);
    if (!o) by.set(k, o = {n: 0, r: 0, b: 0, f: 0, nw: 0});
    o.n++; if (readSet.has(w.id)) o.r++; if (isBm(w.id)) o.b++; if (favMap.has(w.id)) o.f++; if (newSet.has(w.id)) o.nw++;
  }
  const sy = $("year").value, sm = $("month").value, rd = W.filter(w => readSet.has(w.id)).length;
  $("calStat").textContent = `既読 ${rd.toLocaleString()} / ${W.length.toLocaleString()} 話`;
  $("calBody").innerHTML = years.map(y => {
    let yn = 0, yr = 0;
    const cells = Array.from({length: 12}, (_, k) => {
      const m = pad2(k + 1), o = by.get(y + "-" + m);
      if (!o) return `<button class="mc" disabled><b>${k + 1}月</b><span class="c">—</span></button>`;
      yn += o.n; yr += o.r;
      const pct = Math.round(o.r / o.n * 100), on = sy === y && sm === m;
      return `<button class="mc${o.r === o.n ? " done" : ""}${on ? " on" : ""}" data-ym="${y}-${m}" aria-label="${y}年${k + 1}月 ${o.n}話 既読${o.r}">`
        + `<b>${k + 1}月</b><span class="c">${o.n}話</span><i class="pg"><i style="width:${pct}%"></i></i>`
        + `<small>既読 ${o.r}/${o.n}</small><span class="ic">${o.nw ? "🆕" : ""}${o.b ? "🔖" : ""}${o.f ? "⭐" + o.f : ""}</span></button>`;
    }).join("");
    return `<section class="cy"><button class="cy-h${sy === y && !sm ? " on" : ""}" data-y="${y}"><b>${y}年</b><span>${yn}話 ・ 既読 ${yr}/${yn}</span></button><div class="cm">${cells}</div></section>`;
  }).join("");
}
function openCal(){ renderCal(); $("calSheet").hidden = false; document.body.style.overflow = "hidden";
  const on = $("calBody").querySelector(".mc.on,.cy-h.on"); if (on) on.scrollIntoView({block: "center"}); }
function closeCal(){ if ($("calSheet").hidden) return; $("calSheet").hidden = true; document.body.style.overflow = ""; }
function jumpTo(y, m){
  $("q").value = ""; $("year").value = y; $("month").value = m;
  $("unread").checked = false; $("favOnly").checked = false; $("newOnly").checked = false;
  if ($("sort").value === "likes") $("sort").value = "new";
  render(); closeCal(); scrollTo(0, 0);
  snack(y ? `📅 ${y}年${m ? +m + "月" : ""}：${list.length}話` : `📅 すべて：${list.length}話`);
}
$("calBtn").onclick = openCal; $("calX").onclick = closeCal; $("calBg").onclick = closeCal;
$("calAll").onclick = () => jumpTo("", "");
$("calBody").addEventListener("click", e => {
  const mc = e.target.closest("[data-ym]"), yh = e.target.closest("[data-y]");
  if (mc) jumpTo(mc.dataset.ym.slice(0, 4), mc.dataset.ym.slice(5)); else if (yh) jumpTo(yh.dataset.y, "");
});

// ---- 新着 ----
$("nwClr").onclick = () => {
  newSet.clear(); nk.p = {}; saveNk();
  if ($("newOnly").checked) { $("newOnly").checked = false; render(); }
  refreshMarks(); snack("新着をすべて確認済みにしました");
};

// ---- ホーム画面に追加 hint (iPhone Safari etc.; hidden in standalone, once dismissed, or for file://) ----
const A2K = "chiikawaViewer.a2hs.v1";
const standalone = navigator.standalone === true || !!(window.matchMedia && matchMedia("(display-mode: standalone)").matches);
(function(){
  let dis = false; try { dis = !!localStorage.getItem(A2K); } catch(e) {}
  const touch = !!(window.matchMedia && matchMedia("(pointer: coarse)").matches) || /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (!standalone && !dis && touch && /^https?:$/.test(location.protocol)) $("a2hs").hidden = false;
})();
const a2hsOff = () => { $("a2hs").hidden = true; try { localStorage.setItem(A2K, String(Date.now())); } catch(e) {} };
$("a2hsX").onclick = a2hsOff; $("a2hsOk").onclick = a2hsOff;
$("a2hsBk").onclick = () => openSheet();

addEventListener("hashchange", () => { const h = location.hash.slice(1); if (byId.has(h) && !(modal.classList.contains("open") && list[cur] && list[cur].id === h)) { closeCm(); closeSheet(); closeCal(); openWork(h, 0); } });

// ---- init ----
render();
$("bmN").textContent = bmSorted().length; $("favN").textContent = favCount(); updNew();
$("foot").textContent = `データ更新: ${META.built} ・ ${META.posts.toLocaleString()} ポスト / ${META.works.toLocaleString()} 話 ・ 画像は X (pbs.twimg.com) から読み込みます`;
const hashId = location.hash.slice(1);
if (hashId && byId.has(hashId)) {
  let i = list.findIndex(w => w.id === hashId);
  if (i < 0) { $("q").value = ""; $("year").value = ""; $("month").value = ""; $("favOnly").checked = false; $("newOnly").checked = false; $("unread").checked = false; render(); i = list.findIndex(w => w.id === hashId); }
  if (i >= 0) openAt(i, 0);
} else {
  if (st.scroll) requestAnimationFrame(() => scrollTo(0, st.scroll));
  const lw = st.lastId && byId.get(st.lastId);
  if (lw) {
    $("toastTxt").textContent = `前回のつづき：${lw.d.slice(0,10).replace(/-/g,"/")}`;
    $("toast").classList.add("show");
    $("resume").onclick = () => { $("toast").classList.remove("show"); let i = list.findIndex(w => w.id === lw.id); if (i < 0) { $("reset").onclick(); i = list.findIndex(w => w.id === lw.id); } openAt(i, st.img || 0); };
    $("toastX").onclick = () => $("toast").classList.remove("show");
    setTimeout(() => $("toast").classList.remove("show"), 12000);
  }
}
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
