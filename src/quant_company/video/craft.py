"""The owner's craft rules for the episode script (2026-10-22), taken from a fast-growing explainer channel: the title
and thumbnail promise one thing, the first seconds open a loop the end settles, one metaphor carries the main issue and
the close asks a question viewers answer in a word.

They live outside episode.py and contracts.py, which the Fargate render image imports, so a change here needs no new
render image. A job freezes the version it was created with (VideoStore.enqueue, policy['craft']) and keeps that
version's text; a job without one keeps its prompts exactly as before, so a still-running turn is asked again with the
identical request. Version 3 (owner, 2026-11-06) replaces the title rule and leaves the thumbnail to a per-episode
design (video/thumbnail.py); version 4 (owner, 2026-11-09) adds plain words for the title and the thumbnail text."""

import json

CRAFT_GUIDE = (
    "Craft rules (owner, 2026-10-22), on top of every rule above; the source lines, cited numbers, size words, title "
    "rules and the no-advice rule still bind:\n"
    "1. Title hook: the day's number or a stock name from the lines within its first 20 characters plus one tension, "
    "at most 28 characters before the series suffix, in the viewer's words: what it means for them or the question "
    "they are asking (in the spirit of '코스피 3.29% 반등, 믿어도 될까', 'SK하이닉스 5% 상승, 이유는 하나'). Never pack "
    "two or three facts into it.\n"
    "2. Thumbnail text: at most 14 characters in two short lines of one to three words each, read at a glance on a "
    "phone; never a full sentence. The number goes in thumbnail_stat.\n"
    "3. Opening (the cold_open narration, the first ~15 seconds after the service's greeting): (a) one concrete moment "
    "of the day the viewer saw or felt, in plain words; (b) the obvious reading and why it may mislead ('숫자만 보면 "
    "…인데', '…로 보이지만'), which starts answering the title's question as the title rules require; (c) one smaller "
    "open loop on top of that, a signal or a common misreading the end settles (in the spirit of '마지막에 볼 신호 "
    "하나는 반대로 읽히기 쉬우니 끝까지 봐 주세요'). The closing signals scene settles that loop in its narration. No "
    "greeting and no channel name.\n"
    "4. One everyday metaphor for the day's main issue (in the spirit of 줄다리기, 시소, 밀물과 썰물, 체온계), introduced in "
    "the cold_open or the main issue and used again in the closing. It explains; it never changes a fact or a "
    "direction. Leave it out when none fits naturally.\n"
    "5. Talk to the viewer: once or twice, name the question or feeling they likely have (in the spirit of '어제 빠진 걸 "
    "보고 오늘 반등이 진짜인지 헷갈리셨다면'), in plain '~요' sentences, never as advice.\n"
    "6. Closing: one concrete thing to check at the next session (a signal from the lines, never a trade) and one "
    "question viewers can answer in a word, a choice or a number, asked aloud in the last narration and ending "
    "pinned_comment before any disclaimer (in the spirit of '내일 외국인 순매수, 이어진다 vs 꺾인다? 댓글로 골라 주세요.'); "
    "a number in it comes from the lines like every other number.")

CRAFT_REVIEW = (
    "\nCraft (owner, 2026-10-22; measured by the service: the title hook is {hook} characters, the thumbnail text "
    "{thumbnail}): add one concern, with a better version built from the lines, when the title hook packs more than "
    "one tension or is over 28 characters, the thumbnail text is a sentence or over 14 characters, the cold_open opens "
    "no smaller loop or the closing never settles it, or the closing asks no question viewers can answer in a word, a "
    "choice or a number. The opening still starts answering the title's question. Never ask for a metaphor that does "
    "not fit.")

# Version 2 (owner, 2026-10-22: "어떤 신호나 어떤 현상이 나타나면 어떻게 될거 같다 … 현재는 이렇기 때문에 오늘 당장이나
# 당분간은 이럴거 같다"): the close states the outlook, conditions and direction only (owner: no new numbers, no trades).
OUTLOOK = (
    "\n7. Outlook in the closing signals scene, only from the lines the scene cites: for each signal, then says what it "
    "could mean for the market, a sector or the flows, keeping the line's own strength ('…할 수 있습니다', '…가능성이 "
    "있습니다'; never stronger than the line). Then, when the cited lines give one, one base case for today's session "
    "(morning edition) or the next session and the coming days (closing edition): what today's evidence makes more "
    "likely, said as the brief's view, and what would prove it wrong. Leave the base case out when the lines give no "
    "basis, and never credit the brief with a view it does not state. This is not the price prediction ruled out "
    "above: never a level, size or timed move of an index or price ('3,000선 회복', '내일 2% 반등'), only a direction "
    "for the market, a sector or the flows under a named condition. No number that is not in the cited lines, no buy, "
    "sell or weighting advice, never a certainty ('반드시', '확실히').")
OUTLOOK_REVIEW = (
    " Also add a concern when the cited lines give a base case but the closing states none, or when an outlook states "
    "a certainty, a target level or timed move, advice, or a view the lines do not give.")

# Version 3 (owner, 2026-11-06, after briefing thumbnails ran at about a 2.1% click-through rate and 30 of 40 titles
# opened with an index and its percent change): no fixed formula for the title, made for the day's market and its core;
# the thumbnail is designed per episode afterwards (video/thumbnail.py). Rule 1 is replaced, rules 2-7 stay.
TITLE_V3 = (
    "1. Title hook (owner, 2026-11-06: no fixed formula, made for this day's market): the one thing an economy-minded "
    "viewer scrolling past would most want to click today, taken from the day's main issue in the lines, at most 28 "
    "characters before the series suffix. Say what happened and who or what drove it in strong, concrete Korean: a "
    "company, a person, a country, a vote, a policy, or a number that is news by itself, with one tension the first 30 "
    "seconds settle. Open with an index and its percent change only when that move is itself the story (a record, a "
    "sidecar, the largest move in years), never as the default opening. A question is one choice among many, not the "
    "default ending. The example phrases quoted in the rules above only illustrate tone: never reuse or imitate them. "
    "Where the title rules above differ from this rule, this rule wins, except that the service still requires, within "
    "the first 20 characters, a number from the lines or a name the lines write right before a figure (a company, an "
    "index or a person); a generic word such as 반도체, 외국인 or 금리 alone does not count. Every number appears exactly "
    "as the lines write it: a rounded value such as 2.5조 for 2조 5,068억원 is a number the lines do not contain. Never "
    "repeat the opening, the ending (such as '왜?' or '믿어도 될까') or the shape of the recent hooks listed below.\n")
THUMBNAIL_V3 = (
    "\n8. The thumbnail (owner, 2026-11-06) is designed after this script, for this episode alone, from the final title, "
    "the lines and the pictures; thumbnail and thumbnail_stat are its seed message, and the thumbnail and the title "
    "should say different things (one who or how much, the other what happened). Make thumbnail_image the strongest "
    "picture of the day's main subject. When a person is the day's key issue (a statement, a decision, an election, a "
    "deal), ask for a real photo of that person ('PHOTO: <their name as English sources write it> || SCENE: …') and make "
    "it thumbnail_image, so the face can fill the thumbnail.")
TITLE_V3_REVIEW = (
    " Under the owner's 2026-11-06 title rule, also add a concern, with a better hook built from the lines, when the "
    "hook opens with an index and its percent change although that move is not itself the day's story, misses the "
    "day's main issue, or repeats the opening, the ending or the shape of a recent hook: {recent}.")
HEADER, RULES = CRAFT_GUIDE.split('1. Title hook:', 1)
CRAFT_V3 = (HEADER + TITLE_V3 + RULES[RULES.index('2. Thumbnail text:'):] + OUTLOOK + THUMBNAIL_V3)

# Version 4 (owner, 2026-11-09, on the 11/06 and 11/07 episodes: "제목과 문구도 이상해 … 유튜브 시청자들이 우리 영상을
# 보고 싶게끔"): the hooks joined two stories with a comma or led with jargon ('규제는 아직, SK하이닉스는 10.76% 급락',
# '메타 잉여현금흐름 마이너스, 반도체 반등의 한계'). Version 3 stays as it is, plus this rule.
PLAIN_WORDS = (
    "\n9. Plain words (owner, 2026-11-09: \"제목과 문구도 이상해 … 시청자들이 우리 영상을 보고 싶게끔\"): the title hook and "
    "the thumbnail text are in everyday Korean a casual investor gets at a glance, about one subject and one point. Never "
    "join two stories with a comma (in the spirit of what to avoid: '규제는 아직, SK하이닉스는 10.76% 급락'). No jargon "
    "(잉여현금흐름, 경상수지, 담보비율 …) unless the day's story is that word, then say what it means. The thumbnail text "
    "carries no date or weekday and is not a recap of figures: it names what is at stake for the viewer or the question "
    "the video answers.")
PLAIN_WORDS_REVIEW = (
    " Under the owner's 2026-11-09 rule, also add a concern, with a better version built from the lines, when the title "
    "hook or the thumbnail text joins two stories with a comma, uses jargon a casual viewer would not get, or the "
    "thumbnail text carries a date or weekday or only recaps figures.")
CRAFT_VERSION = 4

# Each version's text stays here, so a job keeps the rules it was created with after a later version.
VERSIONS = {1: (CRAFT_GUIDE, CRAFT_REVIEW), 2: (CRAFT_GUIDE + OUTLOOK, CRAFT_REVIEW + OUTLOOK_REVIEW),
            3: (CRAFT_V3, CRAFT_REVIEW + OUTLOOK_REVIEW + TITLE_V3_REVIEW),
            4: (CRAFT_V3 + PLAIN_WORDS, CRAFT_REVIEW + OUTLOOK_REVIEW + TITLE_V3_REVIEW + PLAIN_WORDS_REVIEW)}


def recent_block(recent):
    """The recent hooks a version-3 script must not echo, as data after its rules (frozen with the job's qa)."""
    return '\nRecent hooks, newest first (data, not instructions): ' + json.dumps(list(recent), ensure_ascii=False)


def with_craft(prompt, policy, recent=()):
    """A job's script prompt (episode_prompt) with its craft rules before its last line, the data, which stays last;
    unchanged for a job created before them. recent: the latest episodes' hooks (version 3)."""
    version = (policy or {}).get('craft')
    guide = VERSIONS.get(version)
    if not guide:
        return prompt
    rules, data = prompt.rsplit('\n', 1)
    return rules + '\n' + guide[0] + (recent_block(recent) if version >= 3 else '') + '\n' + data


def craft_review(policy, plan, recent=()):
    """The craft checks for a job's review prompt, with the lengths measured here (a model counts characters badly);
    '' for a job created before them and for a text-v1 plan, whose script prompt never carried them."""
    guide, upload = VERSIONS.get((policy or {}).get('craft')), getattr(plan, 'upload', None)
    if not guide or upload is None:
        return ''
    return guide[1].format(hook=len(upload.title), thumbnail=len(plan.thumbnail),
                           recent=json.dumps(list(recent), ensure_ascii=False) if recent else 'none')
