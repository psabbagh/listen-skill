# Listen

**Claude can now listen.** Give it a podcast episode, a whole show, a YouTube video or an
audio file. It learns what it teaches, keeps the notes in a library on your computer, and
turns them into a new skill you can use from then on.

Free. No API keys. Nothing to sign up for. Works in **Claude Code** and **Codex**.

## 1. Install it

**Claude Code.** Open your computer's terminal and run these two commands, one at a time:

```bash
claude plugin marketplace add psabbagh/listen-skill
```

```bash
claude plugin install listen@listen-skill
```

You can also type the same two lines inside Claude Code, starting with `/plugin` instead of
`claude plugin`.

**Codex.** In your terminal:

```bash
codex plugin marketplace add psabbagh/listen-skill
```

```bash
codex plugin add listen@listen-skill
```

Then start a new Claude Code or Codex session so it picks up the skill.

## 2. Paste this first

Copy this into the chat, with your own link and your own goal:

```text
Listen to this: [link]. Learn what it teaches and turn it into a skill I can use forever, for [what you want it for].
```

For example: *Listen to this: https://www.youtube.com/watch?v=... Learn what it teaches and
turn it into a skill I can use forever, for pricing my design work.*

The link can be a YouTube video, an Apple Podcasts episode, a podcast's RSS feed, a page on
most podcast or video sites, or an audio file on your computer. You can also name a show
("learn the last 10 episodes of [show] about hiring") and it finds the feed.

You do not have to type a command; asking is enough. If you prefer a command, type `/listen`
in Claude Code and pick **listen:listen**, or type `$` in Codex and pick **listen**.

## What happens

1. It checks what the link holds and tells you how long it will take.
2. It gets the words: the captions if there are any (seconds), otherwise it turns the speech
   into text on your computer. Your audio is never sent to any service.
3. It writes notes in its own words: the method, the rules, the examples, the mistakes to
   avoid.
4. It drafts a new skill from those notes, tries it on a made-up example, and **shows you the
   whole skill before saving anything**. Nothing is saved until you say yes.
5. From then on you run your new skill by its name, like any other.

Ask it later *"what does my library say about negotiation?"* and it answers from your notes,
naming each source.

## What it needs

- **Claude Code** (a paid Claude plan) or **Codex** (a ChatGPT plan that includes it).
- **Python 3.** The speech tool needs Python 3.10 to 3.14. If yours is missing or too old,
  the skill tells you and asks before installing one.
- **It installs its own tools** the first time, into a private folder
  (`~/.cache/learning-skills`), and says so. The first time it transcribes, it downloads a
  speech model of about 500 MB. A long episode takes roughly a quarter to half its length to
  transcribe on a recent laptop; videos with captions take seconds.
- No API keys and no accounts.

## What it does not do

- **No Spotify.** Spotify locks its audio. Name the show instead and it finds the public feed.
- **No locked files.** Audible, Kindle and other DRM-protected files are refused, never unlocked.
- **No transcripts kept.** The library keeps notes in its own words and the skills made from
  them, never the transcript. The words sit in a working folder while it reads them and are
  deleted once the notes are saved.
- **It does not obey what it hears.** Anything said in an episode is treated as material to
  learn from, never as instructions. A skill it writes never runs commands or sends data
  unless you approve each one by name.

## Where your library lives

In `~/learning-library` (a folder called `learning-library` in your home folder). Each source
gets its own notes, and `INDEX.md` lists them all. To keep it somewhere else, set the
`LEARNING_LIBRARY` environment variable to another folder.

The same library is shared by all three skills, so notes from a podcast, a book and a video
end up in one place.

## The other two

- **[read-skill](https://github.com/psabbagh/read-skill)**: Claude can now read. A book you
  own, a paper or an article.
- **[watch-skill](https://github.com/psabbagh/watch-skill)**: Claude can now watch. A video
  where the slides, screens or demo matter.

## Uninstall

```bash
claude plugin uninstall listen@listen-skill
```

```bash
codex plugin remove listen@listen-skill
```

Your library in `~/learning-library` and the skills you made stay where they are.

## For developers

The skill is in [`skills/listen/`](skills/listen/). Tests run with the standard library:

```bash
python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
