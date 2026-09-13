# 阅影出海发帖文案（2026-09-13 生成，待用户过目）

## Show HN

**标题**：Show HN: Yueying – video to transcript and 3x3 keyframe sheets, no API key

备选：Show HN: Yueying – an MCP server that turns video into transcript and frames

**正文（发完帖立刻以自己账号发的第一条评论）**：

```
I write code with Claude Code, and most of the tutorials I learn from are on Bilibili. Claude could read my repo, my docs, a screenshot. Not a video. The video MCP servers I found in English were YouTube-only. The Bilibili ones were subtitle-only with Chinese docs. Most of the rest wanted a cloud Whisper key or a manual ffmpeg install, and were macOS-first with Windows untested. So I wrote yueying (阅影, "read video").

You give watch_video a local file or a URL, anything yt-dlp handles. It uses the platform subtitles when they exist and runs faster-whisper locally when they don't. It cuts keyframes at scene changes and packs nine of them into a 3x3 contact sheet, with the frame number and the time burned into each tile. What lands on disk is one folder per video: report.md, transcript.txt, transcript.srt, manifest.json, the frames, and the sheets. Six MCP tools read it back: watch_video, get_transcript, search_transcript, get_frames, get_frame_at, list_videos. No API key anywhere, no telemetry. ffmpeg comes bundled through imageio-ffmpeg, so there is no brew or winget step.

Timings on my machine, an RTX 5060 laptop. A 6-minute Bilibili video with no subtitles: about 90 seconds from URL to report. A 3m35s YouTube video that already has captions: about 22 seconds, because speech recognition never runs. On CPU with the small model, budget 1 to 2 minutes per 10 minutes of speech.

I had this as a shell script first. The two parts that turned it into something the model can drive by itself had nothing to do with video.

One is image cost. One frame per second of a six minute video is 360 images, and at roughly 1 to 2K vision tokens each that is most of a context window for six minutes of screencast. So sheets go out re-encoded to 1280 px at quality 72, about 150 KB each, three per call at most, because Claude Desktop drops a tool result over about 1 MB. watch_video itself never returns an image. Text first, pictures only when the model asks.

The other is time. Claude Desktop has a hard tool timeout of about 60 seconds that you cannot configure, and my pipeline takes minutes. So watch_video runs the pipeline in a child process, long-polls for wait_seconds (default 45), then returns RUNNING and the stage instead of an error. The model calls the same tool again with the same video and re-attaches to the same job. The retry key is the video, not a job id, because models drop ids. They do not drop the URL you just gave them. Clients that let you raise the timeout, like Claude Code and Cline, can pass wait_seconds up to 1500 and get it in one call.

What it is bad at. Keyframes are scene changes, so anything that happens between two cuts is invisible. get_frame_at pulls the exact frame for a local file, but the model has to know to ask. Speech recognition mishears names, numbers and code, so the server instructions tell the model to trust on-screen text over the transcript. The first speech run downloads a Whisper model once, about 480 MB on CPU and 1.6 GB on GPU. And "offline" only covers the processing: the video stays on your disk, but the transcript and the frames the model asks for then go to whatever model your MCP client uses. Not for live streams.

Setup is one line, uvx yueying mcp --setup, which also fetches the speech model up front so your first real call is not a download. MIT, Python 3.10+, CI on ubuntu, windows and macos. There is a GIF in the README of the whole thing running in Claude Desktop if you only want to see it work.

What I want feedback on: is the RUNNING-and-call-again loop acceptable, or is it a hack I should replace with something better?
```


## dev.to

标题：How to show an LLM a video without spending your whole context

标签：mcp, ai, python, opensource

<details><summary>正文 markdown</summary>

I am a developer in China. Most of the tutorials I actually learn from are on Bilibili. When I work with Claude Code I can hand it a repo, a doc, a screenshot. I could not hand it a video.

I looked for something first. Every English-facing video MCP server I found was YouTube-only. The Bilibili ones were subtitle-only with Chinese docs. Most of the rest wanted a cloud Whisper key, or a manual ffmpeg install, or were macOS-first with Windows untested. So I built one, called yueying (阅影, "read video").

This article is about the two problems you hit whenever you try to make a video readable to a language model. You will hit both of them no matter what you build, and neither is really about video.

## The shape of the problem

A model reads text and looks at images. So a video has to become a transcript with timestamps, plus some frames. The whole design lives in the word "some".

Say you sample one frame per second. A six minute video is 360 images. At roughly 1 to 2K vision tokens per image, that is most of a context window to look at six minutes of screencast. One frame every ten seconds is still 36 images, and it still misses the moment the command was typed. Sampling on a clock is the wrong axis.

## Problem one: making the pictures cheap

Three decisions, in order of how much they save.

**Cut frames at scene changes, not on a timer.** ffmpeg can score how much the picture changed between frames, and the frames above a threshold are the moments where something actually happened: a slide flipped, the terminal became a browser, the speaker switched to the editor. A screencast that sits on one window for two minutes gives you one frame instead of twelve. A video with fast cuts gives you more. That is the correct behaviour, and no fixed interval gives it to you.

**Pack the frames you keep.** Nine keyframes go into one 3x3 contact sheet. Re-encoded to 1280 px at JPEG quality 72, a sheet is about 150 KB and costs roughly 1 to 2K vision tokens. Nine separate images cost roughly nine times that. You lose pixel detail. You keep the storyline, which is what the model needs most of the time it is looking at all.

Then give the model a way to buy the detail back. `get_frame_at(video, time)` extracts the exact frame at one moment at full size, for a local file. Cheap overview by default, expensive close-up on request.

**Burn the frame number and the timestamp into every tile.** If I had to throw away everything else in this article, I would keep this one. Each tile carries something like `#14 03:15` in the corner. The label and the picture arrive as a single object, so the model cannot mis-pair them, it cites moments back to you as (03:15) so you can go check, and the timestamp doubles as an address: the model can ask for that exact second at full size when it needs to read code on screen. Nine pictures with no labels is a model guessing at an order.

**Watch the wire size too.** Claude Desktop drops a tool result over about 1 MB. So re-encode before sending, never ship the source JPEG, and cap the number of images per call. Mine returns at most three.

## Text is the index, pictures are the evidence

The transcript is cheap, frames are expensive, so the main tool never returns an image at all. `watch_video` returns text: title, duration, what sheets exist, and the transcript in `[mm:ss]` paragraphs. `get_transcript` takes a time range. `search_transcript` finds where something was said. `get_frames` and `get_frame_at` are the only things that spend vision tokens, and the model calls them after it has decided it needs to look.

One consequence worth writing into your server instructions: speech recognition mishears names, numbers and code. Always. So mine tells the model in writing to trust on-screen text over the transcript. In a coding tutorial the command is usually visible while it is being spoken. The transcript finds the moment. The frame is the source of truth for what was typed.

## Problem two: a tool call that takes minutes

Claude Desktop has a hard tool timeout of about 60 seconds and you cannot configure it. A 6-minute Bilibili video with no subtitles takes about 90 seconds on my RTX 5060 laptop. On CPU with the small Whisper model, budget 1 to 2 minutes per 10 minutes of speech. So a tool that does the work and then returns is broken by construction.

MCP progress notifications do not help here. Reporting progress does not extend the client's deadline. The answer has to arrive inside the window or not at all.

The pattern that works:

1. The tool spawns the real pipeline in a child process, keyed by the video.
2. It long-polls that job for `wait_seconds`, default 45, safely under the client limit.
3. If the job is not done, it returns **text, not an error**: `RUNNING` plus the stage and how long it has been going.
4. The server instructions tell the model to call the same tool again with the same video. That call re-attaches to the running job instead of starting a second one.

Step 4 is the one to get right. Make the job idempotent on the input the model already has, not on a job id it has to carry through the conversation. Models drop ids. They do not drop the URL you just gave them.

Clients whose timeout you can raise skip the whole dance. Claude Code and Cline accept `wait_seconds` up to 1500 and return the finished result in one call. The general rule I would repeat: design the protocol for the strictest client, and let the loose ones opt out.

## What is local and what leaves the machine

| Step | Where it happens |
|---|---|
| Your local video file | Stays on disk |
| Fetching a URL you pass | Your machine talks to that site through yt-dlp |
| Audio extraction, scene detection, frames | Local ffmpeg, bundled via imageio-ffmpeg |
| Subtitles | Taken from the platform when they exist |
| Speech recognition | Local faster-whisper, no API key |
| Whisper model weights | Downloaded once on first use, about 480 MB on CPU and 1.6 GB on GPU |
| Telemetry | None |
| The transcript and the frames the model asks for | Sent to whatever model your MCP client uses |

That last row is the honest boundary. "Offline" here describes the processing. It does not mean the result never reaches a model provider, because the entire point is that a model reads it.

## Install

You need `uv` first. On macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then warm it up once. This installs the package, probes the GPU, fetches the speech model and runs a short smoke test, so your first real question is not also a 480 MB download:

```bash
uvx yueying mcp --setup
```

Claude Desktop, in `%APPDATA%\Claude\claude_desktop_config.json` on Windows or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS. Quit Claude fully and reopen it after editing:

```json
{
  "mcpServers": {
    "yueying": {
      "command": "uvx",
      "args": ["yueying", "mcp"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

If Claude Desktop on Windows says the server disconnected, it is usually PATH: Desktop does not inherit your shell environment, so put the absolute path to `uvx.exe` in `"command"`.

Claude Code:

```bash
claude mcp add --transport stdio --scope user --env PYTHONUTF8=1 yueying -- uvx yueying mcp
```

In Claude Code, passing a large `wait_seconds` only helps if the client-side tool timeout is also above it. That is what `MCP_TOOL_TIMEOUT` (milliseconds) is for, set in the environment Claude Code runs in.

One note for Apple Silicon: the GPU path is CUDA, so on a Mac it runs on CPU with the small model. Videos that already have platform subtitles skip speech recognition entirely, so those are fast everywhere.

## Prior art, fairly

`bradautomates/claude-video` (17k stars) is an agent skill rather than an MCP server, with a cloud Whisper fallback. It is less setup and it does not need a local GPU, and audio can leave your machine. `HUANGCHIHHUNGLeo/claude-real-video` (2.1k stars) runs locally and has had an MCP server since 0.8, with ffmpeg installed separately. `guimatheus92/mcp-video-analyzer` is Node, with whisper installed separately. They are all reasonable, and two of them are much more popular than mine.

What I needed and could not find in one place: Bilibili, Douyin and Xiaohongshu handled next to YouTube, English docs, ffmpeg bundled so there is no separate install, Windows tested, and contact sheets instead of one image per frame. If that combination does not matter to you, install one of theirs.

## Where it breaks

Keyframes come from scene changes, so a slow pan, or a cursor moving inside one static window, produces nothing new. `get_frame_at` covers the exact moment for a local file, but the model has to ask for it.

Speech recognition mishears names, numbers and code. That is why the frames exist and why the instructions tell the model to prefer on-screen text.

The first transcription downloads a Whisper model once.

The privacy boundary is the table above: local processing, no key, no telemetry, but the transcript and the requested frames go to your MCP client's model.

Not for live streams.

CI runs on ubuntu, windows and macos across Python 3.10 and 3.13. One job really transcribes on Linux and macOS with the tiny model through both the CLI and the MCP path. Another builds the Docker image and does an `initialize` plus `tools/list` round trip over `docker run -i`.

Code is MIT: https://github.com/vsh5dvsch7-png/yueying. On PyPI as `yueying`, and in the MCP registry as `io.github.vsh5dvsch7-png/yueying`. If you build your own version of this, take the contact sheet idea. It is the highest value hour I spent on the project.

</details>


## r/ClaudeAI

标题：I wrote an MCP server so Claude can watch a video locally. Two Claude-specific limits shaped the whole design.

```
I kept wanting Claude Code to follow a Bilibili tutorial with me, and nothing I found did it without a cloud Whisper key or a manual ffmpeg install. So I wrote yueying (阅影, "read video"). MIT, on PyPI, and in the MCP registry as io.github.vsh5dvsch7-png/yueying.

There is a GIF in the README of the whole loop in Claude Desktop: paste a link, watch_video runs for about 40 seconds, Claude comes back with timestamped key points. That is the fastest way to see whether you want it.

Two things about Claude specifically, because they shaped everything else.

The ~1 MB tool result cap. watch_video never returns an image. Keyframes come through get_frames as 3x3 contact sheets, nine frames per image, with the frame number and the time burned into each tile. Each sheet is about 150 KB and roughly 1 to 2K vision tokens, three per call at most. Claude then cites moments back as (03:15) and you can go check them. get_frame_at pulls one exact frame at full size when it needs to read code on screen, for local files.

The hard ~60 second tool timeout in Desktop, which you cannot configure. Processing takes minutes, so watch_video runs the pipeline in a child process, waits 45 seconds, then returns RUNNING with the stage instead of failing. Claude calls the same tool again with the same link and re-attaches to the same job, so nothing restarts. In Claude Code you can raise the client timeout and pass wait_seconds up to 1500, and get the result in one call. It also ships as an agent skill for Claude Code, which falls back to the CLI when the MCP server is not connected.

Numbers from my machine, an RTX 5060 laptop: a 6-minute Bilibili video with no subtitles is about 90 seconds from URL to report. A 3m35s YouTube video with captions is about 22 seconds, since platform subtitles are used when they exist and speech recognition never runs. On CPU with the small model, 1 to 2 minutes per 10 minutes of speech. No API key, no telemetry, ffmpeg bundled.

Weak spots: keyframes are scene changes, so motion between cuts is missed. Whisper mangles names and code, so the server instructions tell the model to trust on-screen text over the transcript. The first run downloads a Whisper model once, about 480 MB on CPU. And the processing is local, but the transcript and the frames Claude asks for still go to Anthropic like any other tool result.

I am curious whether the contact sheets read well for you, or whether Claude keeps asking for single frames anyway.
```


## 常见质疑的现成回复


**Why pay image tokens at all? Gemini takes video natively.**

If your workflow is already in Gemini, native video is the simpler answer and I would use it. This is for the case where it is not. Claude Code, Claude Desktop, Cursor and Cline have no video input at all, and I am not switching editors to read a tutorial. The other difference is where the file goes: here the video stays on my disk, and only the transcript and the frames the model asks for are sent on. There is also a practical one. I cannot search inside native video. search_transcript gives me a timestamp, and get_frame_at gives me that exact frame.


**Keyframes miss what happens between them. Scene detection is not watching.**

True, and I say so in the README. Frames are cut at scene changes, so a cursor moving inside one static window is invisible. Two things soften it. The transcript is continuous, so speech in the gap is not lost. And get_frame_at extracts the exact frame at any moment from a local file, so when the transcript hints at something the model can go look at that second instead of guessing from the nearest sheet. It is a cost tradeoff, not a claim that nothing is lost. If you need every frame, this is the wrong tool.


**This is just yt-dlp plus whisper plus ffmpeg in a wrapper.**

Mostly yes, and I had it as a shell script first. yt-dlp, ffmpeg and faster-whisper do the heavy lifting and I wrote none of them. What I wrote is the part that makes the output usable by a model inside a real client: scene-based frame selection packed into 3x3 sheets with the number and time burned in, a transcript the model can page and search by time, image sizes capped so the tool result stays under the client limit, a long-poll protocol that survives a 60 second timeout, and ffmpeg bundled so there is no separate install. It is glue. Glue was the missing piece for me.


**You say offline, but you send everything to Claude anyway.**

Right, and that boundary is in the README rather than buried. Local: the download, the audio extraction, the frame extraction, the speech recognition. No API key, no telemetry. Not local: the transcript and the frames the model requests, which go to whatever model your MCP client uses, under that provider's terms. What you gain is that the video file and the raw audio never leave your disk, and that there is no cloud speech service in the loop. That is a bounded claim, not a private one.


**Why not call it claude-something so people can find it?**

Deliberate. Claude is Anthropic's name, not mine, and putting it in a package name implies an endorsement that does not exist. It would also be wrong on the facts, because this is a plain MCP server and people run it in Cursor, Cline, VS Code and Windsurf too. Yueying is 阅影, "read video". Being unsearchable in English is a real cost and I accepted it.


**Downloading from YouTube and Bilibili is against their terms.**

The tool hands a URL to yt-dlp. Deciding what you are entitled to process is your call, not mine, and the README says so. Local files are a first-class input and the path I use most, including my own screen recordings. If your policy is no downloading, use file paths and the only network access is the one-time model download.


**ASR gets every technical term wrong, so the transcript is useless for tutorials.**

It does get terms wrong, and I do not pretend otherwise. That is exactly why the frames exist, and why the server instructions tell the model to trust on-screen text over the transcript. In a coding tutorial the command is usually on screen while it is being spoken. The transcript is the index for finding the moment. The frame is the source of truth for what was typed. I would not use this where exact wording matters.


**480 MB to 1.6 GB of model download before it does anything.**

Once, and only for videos with no captions. Platform subtitles are used when they exist, which is why the captioned YouTube example comes back in about 22 seconds with no speech recognition at all. uvx yueying mcp --setup does the download up front with a smoke test, so it does not happen in the middle of your first real question.


**claude-video already has 17k stars. Why does this need to exist?**

It is a good project and it is easier to start with. It is an agent skill rather than an MCP server, and it falls back to cloud Whisper, so audio can leave the machine. My starting point was different: Bilibili links, Windows as my main development machine, no key, and no manual ffmpeg step. If those are not your constraints, star count is a reasonable tiebreaker and I would not argue with you.


**Why Python and uvx instead of an npm package?**

Because faster-whisper is Python and I was not going to reimplement it. uvx makes the install one line, and pip works if you prefer. ffmpeg is bundled through imageio-ffmpeg, so there is no separate system install on any of the three platforms that CI covers.


## 发帖清单

- Day before: reread the README top section with fresh eyes. The first screen must match the HN post exactly on the numbers (90 s, 22 s, 1-2 min per 10 min of speech, 480 MB / 1.6 GB, 3 images per call). Any mismatch is the first correction comment.
- Day before: verify the two config snippets by pasting them into a clean profile. Claude Desktop JSON with command uvx, args ["yueying","mcp"], env PYTHONUTF8=1. Claude Code: claude mcp add --transport stdio --scope user --env PYTHONUTF8=1 yueying -- uvx yueying mcp. A config that silently does nothing costs more than a weak title.
- Day before: run uvx yueying mcp --setup on a machine that has never had the package, and time it. Someone will ask in the first hour.
- Day before: confirm the README GIF plays on github.com in a logged-out browser and that it is under GitHub's size limit. Check the GitLab CC-BY attribution for GitInGifs is visible next to it.
- Post to Hacker News first, Tuesday to Thursday, 21:00 Beijing time (UTC+8). That is around 09:00 US Eastern, which is when the front page turns over, and it leaves you awake. Avoid Friday and the weekend.
- HN submission: put https://github.com/vsh5dvsch7-png/yueying in the url field, not in the text. Submit the story, then immediately post the hn_post text as the first comment from your own account.
- Have open before you submit: the repo, the PyPI page, the CI runs page, a terminal with a video ready to process, and this objection list in a text file so you can answer in two minutes instead of twenty.
- Stay at the keyboard for the first 2 hours after posting, 21:00 to 23:00 Beijing. Answer every comment in that window, even the short ones. Reply speed in the first hour matters more than reply length.
- Before sleeping, check once more around 23:30. Then sleep. Do not reply half awake at 03:00; a tired reply to a hostile comment is the only real way to lose the thread.
- Next morning, 08:00 Beijing, do one catch-up pass on overnight comments. Thank the people who found bugs and open issues for anything concrete, linking the issue in your reply.
- dev.to the next day, 21:00 Beijing. Tags: mcp, ai, python, opensource. Add a canonical link back to the repo, and link the HN thread at the end only if it went well.
- r/ClaudeAI last, on day three, 21:00 to 22:00 Beijing (mid-morning US). Read the sub's self-promotion rule and flair requirement first and apply the right flair, otherwise it is auto-removed and looks like a downvote.
- On Reddit, put the GIF in the post body. That sub responds to a 40 second demo far more than to paragraphs.
- Do not cross-post the same text to r/mcp and r/LocalLLaMA in the same hour. Wait at least a day between subs and rewrite the opening paragraph for each.
- Keep a scratch file of every question you could not answer immediately. That list is the next release's changelog and the next article's outline.

## 第一小时可能出的事

- Somebody copies a config snippet and it does not work. This kills the goodwill from the honest limitations section faster than any criticism. Mitigation: test both snippets on a clean profile before posting, and if a fix is needed, push it to the README within the hour and reply with the commit link.
- First comment is "just a wrapper around yt-dlp, whisper and ffmpeg". Likely and not hostile. Reply with the prepared answer, lead with "mostly yes, I had it as a shell script first", then name the four glue pieces. Never argue the premise.
- First comment is "why not Gemini, it reads video natively". Certain to appear. Answer in two sentences: the clients I use have no video input, and the video file itself stays on disk. Do not turn it into a comparison essay.
- Someone challenges the word offline. Point at the local-vs-leaves-the-machine table and agree with them out loud. Agreeing costs nothing and the table is already written.
- A macOS user reports that it is slow. The GPU path is CUDA, so Apple Silicon runs on CPU with the small model. Say it plainly, give the 1 to 2 minutes per 10 minutes of speech number, and remind them captioned videos skip speech recognition entirely.
- A Windows user reports "server disconnected" in Claude Desktop. That is almost always PATH, because Desktop does not inherit the shell environment. Have the absolute-path-to-uvx.exe answer ready to paste.
- Someone runs it on a 2 hour video and reports that Claude Desktop loops on RUNNING for a long time. That is the design working, but it looks like a hang. Explain the child process and the re-call, and suggest Claude Code with a raised timeout for long videos.
- A terms-of-service subthread about downloading from YouTube or Bilibili. Do not defend it. It passes a URL to yt-dlp, the user decides what they may process, and local files are the primary path.
- Someone asks what a Chinese package is phoning home to. The answer is no telemetry, no API key, MIT, and the CI runs are public. Have the CI link ready. Do not get defensive about it.
- The post gets four points and no comments. This is the most likely bad outcome, not hostility, because MCP servers are a crowded Show HN category. Do not repost the same day and do not ask anyone to upvote. Let dev.to and Reddit carry it, and try HN again months later only with a genuinely new version.
- A bug report arrives during the thread. Best possible outcome if handled fast. Open an issue, reply with the issue link, and if it is small, ship a patch release the same night and say so in the thread.
- Your own facts drift. If anyone quotes a number back that you cannot reproduce on your machine right now, correct it publicly in the thread immediately. One voluntary correction buys more trust than the whole limitations paragraph.