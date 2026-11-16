#!/usr/bin/env python3
"""listen.py: get the words out of podcasts and audio, free, on this computer.

    listen.py status                                   what is ready, the library, how fast this computer is
    listen.py find "<show name>" [--n 10]              podcast shows and their public feeds (Apple's directory)
    listen.py plan <links or files...> [--latest N | --all] [--match "words"] [--again]
                                                       build the queue and show its length and time; downloads no audio
    listen.py get [--limit N] [--model small]          get the words for everything in the queue

Each source's words land in <library>/_working/<id>/source.md with a meta.json beside it. library.py turns
them into notes and deletes them. Captions come first (YouTube and other video sites); anything without
captions is transcribed on this computer with faster-whisper. Nothing is uploaded anywhere.

The tools live in a private environment under ~/.cache/learning-skills, made on first use, so nothing is
installed system wide. That needs Python 3.10 to 3.14 somewhere on the computer.
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from library import (fetch_url, is_local, learned_sources, library, log, make_id, working)  # noqa: E402
from media import (DEFAULT_MODEL, PY_OK, SPEED_FILE, candidate_pythons, captions, ensure_venv,  # noqa: E402
                   estimate_text, has_module, hours, python_version, stamp, transcribe, venv_python, ytdlp)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".wav", ".ogg", ".oga", ".flac", ".aac", ".opus", ".webm", ".mp4", ".mkv", ".mov"}
LOCKED_EXT = {".aax": "an Audible book", ".aaxc": "an Audible book", ".azw": "a Kindle book",
              ".azw3": "a Kindle book", ".kfx": "a Kindle book", ".acsm": "an Adobe library loan"}
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


# ---------------------------------------------------------------- podcast directory

def itunes_base() -> str:
    """Tests point this at a stub on this computer. Anything else is ignored."""
    alt = os.environ.get("LISTEN_TEST_ITUNES_URL", "")
    if alt and (urllib.parse.urlsplit(alt).hostname or "") in ("127.0.0.1", "localhost"):
        return alt.rstrip("/")
    return "https://itunes.apple.com"


def itunes(path: str, params: dict) -> dict:
    base = itunes_base()
    url = f"{base}/{path}?{urllib.parse.urlencode(params)}"
    return json.loads(fetch_url(url, trusted_local=base != "https://itunes.apple.com", limit=20 * 1024 ** 2))


# ---------------------------------------------------------------- an input becomes a list of sources

def parse_duration(text: str) -> float | None:
    try:
        parts = [float(p) for p in (text or "").strip().split(":")]
    except ValueError:
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs or None


def looks_like_feed(data: bytes) -> bool:
    head = data[:3000].lower()
    return b"<rss" in head or b"<feed" in head


def feed_link_in_page(data: bytes, page_url: str) -> str | None:
    """A show's web page usually names its feed in a <link rel="alternate"> tag."""
    text = data[:200_000].decode("utf-8", "replace")
    for tag in re.findall(r"<link\b[^>]*>", text, re.I):
        if re.search(r"rss\+xml|atom\+xml", tag, re.I):
            m = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if m:
                return urllib.parse.urljoin(page_url, html.unescape(m.group(1)))
    return None


def words_match(text: str, match: str | None) -> bool:
    if not match:
        return True
    low = text.lower()
    return any(w in low for w in match.lower().split())


def expand_feed(data: bytes, latest, take_all: bool, match: str | None, typed_local: bool, feed: str) -> list:
    root = ET.fromstring(data)
    show = (root.findtext("channel/title") or "podcast").strip()
    every = []
    for item in root.iterfind("channel/item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        title = (item.findtext("title") or "episode").strip()
        if not words_match(title + " " + (item.findtext("description") or ""), match):
            continue
        length = enc.get("length") or ""
        every.append({"kind": "audio", "url": enc.get("url"), "title": title, "show": show,
                    "date": (item.findtext("pubDate") or "").strip(),
                    "page": (item.findtext("link") or "").strip() or None,
                    "seconds": parse_duration(item.findtext(f"{ITUNES_NS}duration") or ""),
                    "bytes": int(length) if length.isdigit() else 0, "typed_local": typed_local, "feed": feed})
    every = newest_first(every)
    out = every if take_all else every[:latest or 1]
    if not out:
        raise RuntimeError("that feed has no episodes" + (" that match" if match else ""))
    return out


def newest_first(items: list) -> list:
    """Feeds are usually newest first, but not always; sort by publish date when every date reads."""
    try:
        keyed = [(email.utils.parsedate_to_datetime(i["date"]).timestamp(), i) for i in items]
    except (TypeError, ValueError, IndexError, KeyError):
        return items
    return [i for _, i in sorted(keyed, key=lambda k: -k[0])]


def expand(source: str, latest=None, take_all: bool = False, match: str | None = None) -> list:
    """One input becomes a list of concrete sources."""
    p = Path(source).expanduser()
    if p.exists():
        files = sorted(f for f in p.iterdir() if f.is_file()) if p.is_dir() else [p]
        out = []
        for f in files:
            ext = f.suffix.lower()
            if ext in LOCKED_EXT:
                raise RuntimeError(f"{f.name} is {LOCKED_EXT[ext]}, locked so only its own app can play it. "
                                   "This skill does not unlock files; a DRM free mp3 or m4b you own works")
            if ext in (".xml", ".rss"):
                out += expand_feed(f.read_bytes(), latest, take_all, match, True, str(f))
            elif ext in AUDIO_EXT:
                out.append({"kind": "file", "path": str(f.resolve()), "title": f.stem})
        if not out:
            raise RuntimeError(f"no audio or video files in {p}")
        return out
    if not re.match(r"https?://", source):
        raise RuntimeError(f"not a link and not a file that exists: {source}")
    host = (urllib.parse.urlsplit(source).hostname or "").lower()
    path = urllib.parse.urlsplit(source).path.lower()
    typed_local = is_local(source)  # only a link the person typed may point at this computer
    if "spotify.com" in host:
        raise RuntimeError("Spotify locks its audio so no other tool can read it. Most shows also publish a public "
                           "feed: run `listen.py find \"<show name>\"` and give me that feed instead")
    if host.endswith("podcasts.apple.com"):
        m = re.search(r"/id(\d+)", source)
        if not m:
            raise RuntimeError("could not find the show's number in that Apple Podcasts link")
        ep_id = urllib.parse.parse_qs(urllib.parse.urlsplit(source).query).get("i", [None])[0]
        found = itunes("lookup", {"id": m.group(1), "entity": "podcastEpisode", "limit": 200}).get("results", [])
        if ep_id:
            ep = next((r for r in found if str(r.get("trackId")) == ep_id and r.get("episodeUrl")), None)
            if ep:
                return [{"kind": "audio", "url": ep["episodeUrl"], "title": ep.get("trackName") or "episode",
                         "show": ep.get("collectionName"), "date": ep.get("releaseDate", ""),
                         "seconds": (ep.get("trackTimeMillis") or 0) / 1000 or None,
                         "typed_local": itunes_base() != "https://itunes.apple.com"}]
        feed = next((r.get("feedUrl") for r in found if r.get("feedUrl")), None)
        if not feed:
            raise RuntimeError("Apple does not list a public feed for that show")
        # A feed from Apple's directory never counts as typed by the person, except in a test.
        trusted = itunes_base() != "https://itunes.apple.com"
        return expand_feed(fetch_url(feed, trusted, limit=50 * 1024 ** 2), latest, take_all, match, trusted, feed)
    suffix = Path(path).suffix
    if suffix in LOCKED_EXT:
        raise RuntimeError("that is a locked audiobook file. This skill does not unlock files")
    if suffix in AUDIO_EXT and "youtube" not in host:
        return [{"kind": "audio", "url": source, "title": Path(path).stem, "typed_local": typed_local}]
    if any(h in host for h in ("youtube.com", "youtu.be")):
        return expand_youtube(source, path, latest, take_all, match)
    data = fetch_url(source, typed_local, limit=50 * 1024 ** 2)
    if looks_like_feed(data):
        return expand_feed(data, latest, take_all, match, typed_local, source)
    feed = feed_link_in_page(data, source)
    if feed and (latest or take_all or match):
        return expand_feed(fetch_url(feed, typed_local, limit=50 * 1024 ** 2), latest, take_all, match,
                           typed_local, feed)
    # Any other page: yt-dlp knows hundreds of sites (SoundCloud, Vimeo, many podcast hosts).
    return [{"kind": "media", "url": source, "title": None, "feed": feed}]


def expand_youtube(source: str, path: str, latest, take_all: bool, match: str | None) -> list:
    is_list = "list=" in source and "/watch" not in path or any(
        k in path for k in ("/playlist", "/@", "/channel/", "/c/", "/user/"))
    if not is_list:
        return [{"kind": "media", "url": source, "title": None}]
    ensure_venv()
    args = ["--flat-playlist", "--dump-single-json"]
    if not take_all:
        args += ["--playlist-end", str(latest or 10)]
    r = ytdlp(*args, "--", source)
    if r.returncode:
        raise RuntimeError("could not list that playlist or channel: " +
                           (r.stderr.strip().splitlines() or ["no reason given"])[-1])
    entries = json.loads(r.stdout).get("entries") or []
    if entries and all(e.get("_type") == "playlist" for e in entries):  # a channel lists its tabs first
        r = ytdlp(*args, "--", entries[0].get("url"))
        entries = (json.loads(r.stdout).get("entries") or []) if r.returncode == 0 else []
    out = [{"kind": "media", "url": e.get("url") or e.get("webpage_url"), "title": e.get("title") or e.get("id"),
            "show": e.get("channel") or e.get("uploader"), "seconds": e.get("duration")}
           for e in entries if words_match(e.get("title") or "", match)]
    if not out:
        raise RuntimeError("that playlist or channel has no videos" + (" that match" if match else ""))
    return out


def item_seconds(it: dict) -> tuple[float, bool]:
    """Length in seconds and whether it is known. An unknown length counts as one hour."""
    if it.get("seconds"):
        return float(it["seconds"]), True
    if it.get("bytes"):
        return it["bytes"] * 8 / 96000, False  # a 96 kbps guess
    return 3600.0, False


# ---------------------------------------------------------------- one source

def write_source(it: dict, rows: list, how: str) -> dict:
    folder = working() / it["id"]
    folder.mkdir(parents=True, exist_ok=True)
    source = it.get("url") or it.get("path")
    meta = {"id": it["id"], "kind": "video" if it["kind"] == "media" and it.get("is_video") else "episode",
            "medium": "audio", "made_by": "listen", "title": it.get("title"), "show": it.get("show"),
            "date": it.get("date"), "page": it.get("page"), "source": source, "how": how}
    lines = [f"# {it.get('title')}", "", f"Source: {source}", f"Words from: {how}"]
    for key in ("page", "show", "date"):
        if it.get(key):
            lines.append(f"{key.title()}: {it[key]}")
    lines += ["", "Working copy for reading only. It is deleted when the notes are saved.", "", "## Transcript", ""]
    bucket, last = [], None
    for start, text in rows:
        if last is None or start - last >= 30:
            if bucket:
                lines += [" ".join(bucket), ""]
            bucket, last = [f"[{stamp(start)}]"], start
        bucket.append(text)
    if bucket:
        lines.append(" ".join(bucket))
    (folder / "source.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta["words"] = sum(len(t.split()) for _, t in rows)
    (folder / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"id": it["id"], "title": it.get("title"), "how": how, "words": meta["words"],
            "source_file": str(folder / "source.md")}


def process(it: dict, model: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        rows, how = None, ""
        if it["kind"] == "media":
            rows, info, how = captions(it["url"], work)
            it["title"] = it.get("title") or info.get("title")
            it["show"] = it.get("show") or info.get("channel") or info.get("uploader")
            it["date"] = it.get("date") or info.get("upload_date")
            it["is_video"] = bool(info.get("vcodec") and info.get("vcodec") != "none")
            if not rows:
                log("  No captions, so transcribing on this computer.")
                r = ytdlp("-f", "bestaudio/best", "-o", str(work / "audio.%(ext)s"), "--", it["url"])
                files = list(work.glob("audio.*"))
                if r.returncode or not files:
                    raise RuntimeError("no captions, and the sound would not download: " + r.stderr.strip()[-300:])
                rows, _ = transcribe(files[0], model)
                how = f"transcribed on this computer ({model} model)"
        elif it["kind"] == "audio":
            suffix = Path(urllib.parse.urlsplit(it["url"]).path).suffix.lower()
            f = work / f"audio{suffix if suffix in AUDIO_EXT else '.mp3'}"
            fetch_url(it["url"], it.get("typed_local", False), to=f)
            rows, _ = transcribe(f, model)
            how = f"transcribed on this computer ({model} model)"
        elif it["kind"] == "file":
            rows, _ = transcribe(Path(it["path"]), model)
            how = f"transcribed on this computer ({model} model)"
        else:
            raise RuntimeError(f"unknown kind of source: {it['kind']}")
    if not rows:
        raise RuntimeError("found no spoken words in this one")
    return write_source(it, rows, how)


# ---------------------------------------------------------------- the queue

def queue_path() -> Path:
    return working() / "listen-queue.json"


def load_queue() -> list:
    try:
        return json.loads(queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_queue(items: list) -> None:
    working().mkdir(parents=True, exist_ok=True)
    queue_path().write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")


def cmd_plan(a) -> int:
    items, failed = [], []
    for raw in a.sources:
        try:
            items += expand(raw, a.latest, a.all, a.match)
        except Exception as exc:
            failed.append({"source": raw, "why": str(exc)})
            log(f"FAILED {raw}: {exc}")
    lookup = [i for i in items if i["kind"] == "media" and not i.get("title")][:20]
    if lookup:
        ensure_venv()
    for i in lookup:  # a single video or page link: get its real title and length, without downloading
        r = ytdlp("--dump-single-json", "--skip-download", "--no-playlist", "--", i["url"])
        if r.returncode == 0:
            info = json.loads(r.stdout)
            i.update(title=info.get("title"), show=info.get("channel") or info.get("uploader"),
                     seconds=info.get("duration"))
    learned, seen, queue = learned_sources(), set(), []
    for i in items:
        src = i.get("url") or i.get("path")
        i["id"] = make_id(i.get("title") or re.sub(r"^https?://(www\.)?", "", src), src)
        if i["id"] in seen:
            continue
        seen.add(i["id"])
        if src in learned and not a.again:
            i["status"] = "already learned"
        elif (working() / i["id"] / "source.md").exists():
            i["status"] = "words ready"
        else:
            i["status"] = "to do"
        queue.append(i)
    save_queue(queue)
    todo = [i for i in queue if i["status"] == "to do"]
    secs = sum(item_seconds(i)[0] for i in todo)
    unknown = sum(1 for i in todo if not item_seconds(i)[1])
    audio = sum(item_seconds(i)[0] for i in todo if i["kind"] != "media")
    summary = {
        "to_do": len(todo),
        "already_learned": sum(1 for i in queue if i["status"] == "already learned"),
        "words_ready": sum(1 for i in queue if i["status"] == "words ready"),
        "length": hours(secs) + (f" ({unknown} of unknown length, counted as an hour each)" if unknown else ""),
        "transcription_time": (f"{estimate_text(audio)} for the podcast audio; videos with captions take seconds"
                               if audio else "seconds: these are videos, which usually have captions"),
        "reading": f"about {int(secs / 60 * 150):,} words to read",
        "cost": "free: captions or the speech tool on this computer",
        "failed": failed,
        "items": [{"id": i["id"], "title": i.get("title") or i.get("url"), "show": i.get("show"),
                   "length": hours(item_seconds(i)[0]) + ("" if item_seconds(i)[1] else " (guess)"),
                   "status": i["status"]} for i in queue[:200]],
    }
    if len(queue) > 200:
        summary["items_not_shown"] = len(queue) - 200
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if queue else 1


def cmd_get(a) -> int:
    queue = load_queue()
    if not queue:
        log("FAILED: the queue is empty. Run `listen.py plan <link>` first.")
        return 1
    todo = [i for i in queue if i.get("status") == "to do"]
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        log("Nothing left to get. Every item already has its words or is in the library.")
        print(json.dumps({"done": [], "failed": [], "left": 0}))
        return 0
    try:
        ensure_venv()
    except RuntimeError as exc:
        log(f"FAILED setup: {exc}")
        return 2
    log(f"Getting the words for {len(todo)} source{'s' if len(todo) != 1 else ''}, one at a time.")
    done, failed = [], []
    for n, it in enumerate(todo, 1):
        label = it.get("title") or it.get("url") or it.get("path")
        log(f"[{n}/{len(todo)}] {label}")
        try:
            res = process(it, a.model)
            it["status"] = "words ready"
            it.pop("last_error", None)
            done.append(res)
            log(f"OK {res['title']} ({res['how']}, {res['words']:,} words)")
        except Exception as exc:
            it["last_error"] = str(exc)[:300]
            failed.append({"source": label, "why": str(exc)})
            log(f"FAILED {label}: {exc}")
        save_queue(queue)
    left = sum(1 for i in queue if i.get("status") == "to do")
    print(json.dumps({"done": done, "failed": failed, "left": left, "working_folder": str(working())},
                     indent=1, ensure_ascii=False))
    return 0 if done else 1


# ---------------------------------------------------------------- setup and search

def cmd_status(a) -> int:
    try:
        speeds = json.loads(SPEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        speeds = {}
    lib = library()
    try:
        sources = len(json.loads((lib / "library.json").read_text(encoding="utf-8")).get("items", {}))
    except (OSError, ValueError):
        sources = 0
    usable = [".".join(map(str, v)) for c in candidate_pythons() if (v := python_version(c)) in PY_OK]
    print(json.dumps({
        "python_running_this": ".".join(map(str, sys.version_info[:3])),
        "python_for_the_tools": max(usable, key=lambda v: tuple(map(int, v.split(".")))) if usable else "none found: install Python 3.13",
        "private_environment": "ready" if venv_python().exists() else "made on first use",
        "speech_tool": "installed" if has_module("faster_whisper") else "installed when first needed",
        "library": str(lib),
        "sources_in_library": sources,
        "speed_on_this_computer": speeds or "measured on the first transcription",
        "queue": len(load_queue()),
    }, indent=1, ensure_ascii=False))
    return 0


def cmd_find(a) -> int:
    try:
        res = itunes("search", {"term": a.words, "media": "podcast", "entity": "podcast", "limit": a.n}).get("results", [])
    except Exception as exc:
        log(f"FAILED: the search did not work ({exc}). Try again in a minute, or search the web for the show's RSS feed.")
        return 2
    out, seen = [], set()
    for r in res:
        if r.get("feedUrl") and r["feedUrl"] not in seen:  # Apple lists some shows twice
            seen.add(r["feedUrl"])
            out.append({"show": r.get("collectionName"), "by": r.get("artistName"), "episodes": r.get("trackCount"),
                        "feed": r["feedUrl"]})
    if not out:
        log("No shows found. Try fewer or different words.")
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Get the words out of podcasts and audio, free, on this computer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("find")
    p.add_argument("words")
    p.add_argument("--n", type=int, default=10)
    p = sub.add_parser("plan")
    p.add_argument("sources", nargs="+")
    p.add_argument("--latest", type=int, help="newest N from a feed, playlist or channel")
    p.add_argument("--all", action="store_true", help="every episode")
    p.add_argument("--match", help="only episodes whose title or description has any of these words")
    p.add_argument("--again", action="store_true", help="include sources already in the library")
    p = sub.add_parser("get")
    p.add_argument("--limit", type=int, help="only the first N still to do")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="speech model: base is faster and rougher, medium slower and better (default small)")
    a = ap.parse_args(argv)
    handlers = {"status": cmd_status, "find": cmd_find, "plan": cmd_plan, "get": cmd_get}
    try:
        return handlers[a.cmd](a)
    except KeyboardInterrupt:
        log("Stopped. Run the same command again to carry on.")
        return 130
    except Exception as exc:  # a plain sentence, never a stack trace
        log(f"FAILED: something unexpected went wrong ({type(exc).__name__}: {str(exc)[:300]}).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
