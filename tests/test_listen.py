"""Offline tests for listen.py and library.py. Run: python3 -m unittest discover -s tests"""
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "listen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Made Up Show</title>
<item><title>Pricing your work</title><link>https://example.com/ep3</link><pubDate>Mon, 02 Nov 2026</pubDate>
<itunes:duration>1:02:03</itunes:duration><enclosure url="https://example.com/3.mp3" length="100"/></item>
<item><title>Hiring your first person</title><itunes:duration>1800</itunes:duration>
<enclosure url="https://example.com/2.mp3" length="200"/></item>
<item><title>No audio here</title></item>
<item><title>Pricing again</title><description>more on offers</description>
<enclosure url="https://example.com/1.mp3"/></item>
</channel></rss>"""

SOURCE = ("The first thing you do is send the quote on the same day you see the job because customers decide "
          "fast and the second quote they get usually wins the work when the first one is slow to arrive at all. ") * 4


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(*args)
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {"LEARNING_LIBRARY": str(self.root / "lib"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                    "CODEX_HOME": str(self.root / "codex"), "LEARNING_SKILLS_CACHE": str(self.root / "cache")}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        import library
        import media
        import listen
        self.library = importlib.reload(library)
        self.media = importlib.reload(media)
        self.listen = importlib.reload(listen)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def working_source(self, sid="pricing-abc123", text=SOURCE):
        folder = self.library.working() / sid
        folder.mkdir(parents=True)
        (folder / "source.md").write_text(text)
        (folder / "meta.json").write_text(json.dumps({"id": sid, "source": "https://example.com/3.mp3",
                                                      "show": "Made Up Show"}))
        return folder


class Feeds(Base):
    def test_newest_episode_by_default(self):
        items = self.listen.expand_feed(FEED, None, False, None, False, "f")
        self.assertEqual([i["title"] for i in items], ["Pricing your work"])
        self.assertEqual(items[0]["seconds"], 3723)
        self.assertEqual(items[0]["page"], "https://example.com/ep3")
        self.assertEqual(items[0]["show"], "Made Up Show")

    def test_all_skips_items_without_audio(self):
        items = self.listen.expand_feed(FEED, None, True, None, False, "f")
        self.assertEqual(len(items), 3)

    def test_match_reads_titles_and_descriptions(self):
        items = self.listen.expand_feed(FEED, None, True, "offers hiring", False, "f")
        self.assertEqual([i["title"] for i in items], ["Hiring your first person", "Pricing again"])

    def test_no_match_says_so(self):
        with self.assertRaisesRegex(RuntimeError, "that match"):
            self.listen.expand_feed(FEED, None, True, "gardening", False, "f")

    def test_local_feed_file(self):
        f = self.root / "feed.xml"
        f.write_bytes(FEED)
        self.assertEqual(len(self.listen.expand(str(f), 2)), 2)

    def test_feed_link_found_in_page(self):
        page = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.rss"></head></html>'
        self.assertEqual(self.listen.feed_link_in_page(page, "https://show.example/about"),
                         "https://show.example/feed.rss")

    def test_durations(self):
        self.assertEqual(self.listen.parse_duration("1:00:00"), 3600)
        self.assertEqual(self.listen.parse_duration("95"), 95)
        self.assertIsNone(self.listen.parse_duration("soon"))


class Order(Base):
    def test_newest_by_publish_date_not_file_order(self):
        feed = FEED.replace(b"<item><title>Hiring", b"<item><pubDate>Tue, 10 Nov 2026 08:00:00 +0000</pubDate><title>Hiring")
        feed = feed.replace(b"Mon, 02 Nov 2026", b"Mon, 02 Nov 2026 08:00:00 +0000")
        feed = feed.replace(b"<item><title>Pricing again</title>",
                            b"<item><title>Pricing again</title><pubDate>Sun, 01 Nov 2026 08:00:00 +0000</pubDate>")
        items = self.listen.expand_feed(feed, None, False, None, False, "f")
        self.assertEqual(items[0]["title"], "Hiring your first person")

    def test_speed_uses_the_middle_of_recent_runs(self):
        for took in (100, 100, 900):  # one run slowed by something else
            self.media.save_speed(400, took, "small")
        self.assertIn("roughly 4 minutes", self.media.estimate_text(1000, "small"))


class Refusals(Base):
    def test_spotify_music_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "not a podcast"):
            self.listen.expand("https://open.spotify.com/track/abcdefghijklmnopqrstuv")

    def test_locked_audiobook_file(self):
        f = self.root / "book.aax"
        f.write_bytes(b"x")
        with self.assertRaisesRegex(RuntimeError, "does not unlock"):
            self.listen.expand(str(f))

    def test_only_web_links(self):
        with self.assertRaisesRegex(RuntimeError, "not a web link"):
            self.library.check_url("file:///etc/passwd")

    def test_private_address_from_a_feed(self):
        with self.assertRaisesRegex(RuntimeError, "private address"):
            self.library.check_url("http://127.0.0.1:8080/x.mp3")
        self.library.check_url("http://127.0.0.1:8080/x.mp3", trusted_local=True)

    def test_not_a_link_or_file(self):
        with self.assertRaisesRegex(RuntimeError, "not a link"):
            self.listen.expand("some words")


class Captions(Base):
    def test_rolling_auto_captions_are_deduplicated(self):
        vtt = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000
hello <c>there</c>

00:00:03.000 --> 00:00:05.000
hello there

00:01:02.500 --> 00:01:04.000
price &amp; value
"""
        self.assertEqual(self.media.parse_vtt(vtt), [(1, "hello there"), (62, "price & value")])

    def test_source_file_groups_by_thirty_seconds(self):
        it = {"id": "x-1", "kind": "audio", "title": "T", "url": "https://e.com/a.mp3", "show": "S"}
        res = self.listen.write_source(it, [(0, "a"), (10, "b"), (40, "c")], "captions")
        text = Path(res["source_file"]).read_text()
        self.assertIn("[0:00:00] a b", text)
        self.assertIn("[0:00:40] c", text)
        meta = json.loads((self.library.working() / "x-1" / "meta.json").read_text())
        self.assertEqual(meta["source"], "https://e.com/a.mp3")
        self.assertEqual(meta["kind"], "episode")


class Library(Base):
    NOTES = """---
title: Pricing your work
source: placeholder
kind: episode
topics: pricing, quotes
one_line: Quote the same day.
---

## The Method
1. Send the quote within a day of seeing the job, since speed wins work.
"""

    def save(self, sid="pricing-abc123", notes=None):
        (self.library.working() / sid / "notes.md").write_text(notes or self.NOTES)
        return quiet(self.library.main, ["save", sid])

    def test_save_files_notes_indexes_and_deletes_the_text(self):
        folder = self.working_source()
        code, out = self.save()
        self.assertEqual(code, 0, out)
        self.assertFalse(folder.exists())
        saved = (self.library.library() / "notes" / "pricing-abc123.md").read_text()
        self.assertIn("source: https://example.com/3.mp3", saved)
        self.assertNotIn("placeholder", saved)
        self.assertIn("show: Made Up Show", saved)
        self.assertIn("learned: ", saved)
        index = (self.library.library() / "INDEX.md").read_text()
        self.assertIn("**pricing**", index)
        self.assertIn("https://example.com/3.mp3", self.library.learned_sources())

    def test_save_keeps_publish_date_and_page_and_drops_finished_queue(self):
        folder = self.working_source()
        meta = json.loads((folder / "meta.json").read_text())
        meta.update(date="Mon, 02 Nov 2026", page="https://example.com/ep3")
        (folder / "meta.json").write_text(json.dumps(meta))
        queue = self.library.working() / "listen-queue.json"
        queue.write_text(json.dumps([{"url": "https://example.com/3.mp3"}]))
        self.assertEqual(self.save()[0], 0)
        saved = (self.library.library() / "notes" / "pricing-abc123.md").read_text()
        self.assertIn("published: Mon, 02 Nov 2026", saved)
        self.assertIn("page: https://example.com/ep3", saved)
        self.assertFalse(queue.exists())

    def test_save_refuses_copied_text(self):
        self.working_source()
        code, out = self.save(notes=self.NOTES + SOURCE)
        self.assertEqual(code, 1)
        self.assertIn("word for word", out)
        self.assertFalse((self.library.library() / "notes").exists())

    def test_save_needs_the_front_lines(self):
        self.working_source()
        code, out = self.save(notes="just some notes")
        self.assertEqual(code, 1)
        self.assertIn("title", out)

    def test_save_rejects_odd_ids(self):
        code, out = quiet(self.library.main, ["save", "../../etc"])
        self.assertEqual(code, 1)

    def test_search_and_link(self):
        self.working_source()
        self.save()
        code, out = quiet(self.library.main, ["search", "quote"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["id"], "pricing-abc123")
        where = str(self.root / "claude" / "skills" / "quoting" / "SKILL.md")
        code, _ = quiet(self.library.main, ["link", "quoting", "pricing-abc123", "--path", where])
        self.assertEqual(code, 0)
        index = (self.library.library() / "INDEX.md").read_text()
        self.assertIn("quoting", index)
        self.assertIn("SKILL.md", index)
        code, out = quiet(self.library.main, ["link", "quoting", "missing-id"])
        self.assertEqual(code, 1)

    def test_names(self):
        self.assertEqual(quiet(self.library.main, ["name", "quoting"])[0], 0)
        self.assertEqual(quiet(self.library.main, ["name", "review"])[0], 1)
        self.assertEqual(quiet(self.library.main, ["name", "Bad Name"])[0], 1)
        (self.root / "codex" / "skills" / "quoting").mkdir(parents=True)
        code, out = quiet(self.library.main, ["name", "quoting"])
        self.assertEqual(code, 1)
        self.assertIn("codex", out)

    def test_copy_check_allows_short_quotes(self):
        notes = "Use the line: send the quote on the same day you see the job. Then follow up."
        c = self.library.copy_check(notes, SOURCE)
        self.assertLessEqual(c["longest_run"], 40)


class Plan(Base):
    def test_plan_marks_learned_sources_and_dedupes(self):
        f = self.root / "feed.xml"
        f.write_bytes(FEED)
        lib = self.library.lib_data()
        lib["items"]["x"] = {"source": "https://example.com/3.mp3"}
        self.library.save_lib(lib)
        code, out = quiet(self.listen.main, ["plan", str(f), str(f), "--all"])
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertEqual(summary["already_learned"], 1)
        self.assertEqual(summary["to_do"], 2)
        self.assertEqual(len(summary["items"]), 3)


class SharedLibrary(unittest.TestCase):
    def test_shared_files_match_the_other_skills(self):
        """library.py is shared by listen, read and watch, media.py by listen and watch; a change goes to all."""
        checked = 0
        for name, others in (("library.py", ("read", "watch")), ("media.py", ("watch",))):
            mine = (SCRIPTS / name).read_text()
            for other in others:
                theirs = SCRIPTS.parent.parent / other / "scripts" / name
                if theirs.exists():
                    checked += 1
                    self.assertEqual(mine, theirs.read_text(), f"{other}/scripts/{name} differs; copy it across")

        if not checked:
            self.skipTest("the sibling skills are not in this repository")

if __name__ == "__main__":
    unittest.main()


EPISODE_PAGE = (b'<html><head><meta property="og:title" content="Pricing your work"/>'
                b'<meta property="og:description" content="Made Up Show &#183; Episode"/>'
                b'<meta name="music:duration" content="3723"/><meta name="music:release_date" content="2026-11-02T09:00:00Z"/>'
                b'</head></html>').replace(b"&#183;", "\u00b7".encode())
SHOW_PAGE = b'<html><head><meta property="og:title" content="Made Up Show"/></head></html>'
SPOTIFY_EP = "https://open.spotify.com/episode/aaaaaaaaaaaaaaaaaaaaaa"
SPOTIFY_SHOW = "https://open.spotify.com/show/bbbbbbbbbbbbbbbbbbbbbb"


class Spotify(Base):
    """A Spotify link is read through the same show's public feed. Every page and feed here is made up."""

    def stub(self, page=EPISODE_PAGE, shows=(("Made Up Show", "https://example.com/feed"),), feed=FEED):
        self.fetched = []

        def fetch(url, trusted_local=False, limit=0, to=None, with_type=False):
            self.fetched.append(url)
            if "spotify.com" in url:
                return page
            return feed

        self.listen.fetch_url = fetch
        self.listen.itunes = lambda path, params: {"results": [
            {"collectionName": name, "artistName": "Someone", "feedUrl": url} for name, url in shows]}

    def test_episode_comes_from_the_public_feed(self):
        self.stub()
        items = self.listen.expand(SPOTIFY_EP)
        self.assertEqual([(i["title"], i["url"]) for i in items], [("Pricing your work", "https://example.com/3.mp3")])
        self.assertEqual(items[0]["note"], self.listen.SPOTIFY_NOTE)
        self.assertIn("https://example.com/feed", self.fetched)

    def test_a_slightly_different_title_on_the_same_days(self):
        self.stub(page=EPISODE_PAGE.replace(b"Pricing your work", b"Pricing Your Work!"))
        self.assertEqual(self.listen.expand(SPOTIFY_EP)[0]["url"], "https://example.com/3.mp3")

    def test_show_link_gives_the_newest_or_more(self):
        self.stub(page=SHOW_PAGE)
        self.assertEqual([i["title"] for i in self.listen.expand(SPOTIFY_SHOW)], ["Pricing your work"])
        self.assertEqual(len(self.listen.expand(SPOTIFY_SHOW, take_all=True)), 3)
        self.assertEqual([i["title"] for i in self.listen.expand(SPOTIFY_SHOW, match="hiring")], ["Hiring your first person"])

    def test_a_show_only_on_spotify_says_so(self):
        self.stub(shows=(("Another Show", "https://example.com/other"),))
        with self.assertRaisesRegex(RuntimeError, "probably only on Spotify.*Another Show"):
            self.listen.expand(SPOTIFY_EP)

    def test_two_public_shows_with_the_name_asks_which(self):
        self.stub(shows=(("Made Up Show", "https://example.com/a"), ("Made up show", "https://example.com/b")))
        with self.assertRaisesRegex(RuntimeError, "more than one public show.*Ask which one"):
            self.listen.expand(SPOTIFY_EP)

    def test_an_episode_not_in_the_feed_lists_the_closest(self):
        self.stub(page=EPISODE_PAGE.replace(b"Pricing your work", b"Something else entirely"))
        with self.assertRaisesRegex(RuntimeError, 'Closest: "Pricing your work"|Closest: .*"Pricing your work".*Ask which one'):
            self.listen.expand(SPOTIFY_EP)

    def test_plan_shows_the_note_once(self):
        self.stub()
        code, out = quiet(self.listen.main, ["plan", SPOTIFY_EP])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["notes"], [self.listen.SPOTIFY_NOTE])
