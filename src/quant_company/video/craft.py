"""The owner's craft rules for the episode script (2026-10-22), taken from a fast-growing explainer channel: the title
and thumbnail promise one thing, the first seconds open a loop the end settles, one metaphor carries the main issue and
the close asks a question viewers answer in a word.

They live outside episode.py and contracts.py, which the Fargate render image imports, so a change here needs no new
render image. A job freezes the version it was created with (VideoStore.enqueue, policy['craft']) and keeps that
version's text; a job without one keeps its prompts exactly as before, so a still-running turn is asked again with the
identical request."""

CRAFT_VERSION = 1

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

# Each version's text stays here, so a job keeps the rules it was created with after a later version.
VERSIONS = {1: (CRAFT_GUIDE, CRAFT_REVIEW)}


def with_craft(prompt, policy):
    """A job's script prompt (episode_prompt) with its craft rules before its last line, the data, which stays last;
    unchanged for a job created before them."""
    guide = VERSIONS.get((policy or {}).get('craft'))
    if not guide:
        return prompt
    rules, data = prompt.rsplit('\n', 1)
    return rules + '\n' + guide[0] + '\n' + data


def craft_review(policy, plan):
    """The craft checks for a job's review prompt, with the lengths measured here (a model counts characters badly);
    '' for a job created before them and for a text-v1 plan, whose script prompt never carried them."""
    guide, upload = VERSIONS.get((policy or {}).get('craft')), getattr(plan, 'upload', None)
    if not guide or upload is None:
        return ''
    return guide[1].format(hook=len(upload.title), thumbnail=len(plan.thumbnail))
