# Anyworld

![Anyworld banner](static/media/AnyworldBanner.jpg)

A multiplayer text adventure where you never have to roll dice or keep score — just write what your
character does. One player (the host) describes the scenario, then everyone takes turns acting in
their own words while an AI weaves every choice into a story that keeps unfolding.

### What's new (2026-10-04)

- Save and reuse scenarios: The host can save, load, and delete scenarios in their browser. Scenarios are stored in the browser's localStorage and stay completely local.
- Start another adventure: After ending a game, the host can create a new scenario without restarting the server.
- Browse and export History: Easily search previous public events for forgotten details. Also exportable as JSONL.
- Multilingual play: With the OpenAI backend, narration follows the language of your scenario.

## Get started

One person runs the game server and connects it to an AI model. Everyone plays in a browser.
You need Python 3.11 or newer on the server. Anyworld supports a local llama.cpp backend and
direct OpenAI API access; direct OpenAI operation has been tested live with `gpt-5.6-luna`.
With the OpenAI backend, narration follows the language of the host's scenario, allowing
non-English play. The local compatible backend currently requests English narration.

`config.example.yaml` is an optional template to copy to your local `config.yaml`; it is not
loaded automatically. Set distinct host and player passwords in the local configuration or
through `AD_SERVER__HOST_PASSWORD` and `AD_SERVER__PLAYER_PASSWORD`. Environment values take
precedence over YAML, and the project's `.env` file can supply these variables.

For the direct OpenAI backend, put `AD_OPENAI_API_KEY` in the project's `.env` file or process
environment. An existing process value takes precedence over `.env`. The game ignores inherited
`OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` settings on its SDK clients to avoid conflicts with that
key. Restart the server after changing credentials or configuration.

See [INSTALL.md](INSTALL.md) for installation, passwords, model settings, backend switching,
network access, and troubleshooting. Once installed and configured, run `anyworld` or `python app.py`
from the repository directory, then open the local game page at https://127.0.0.1:4141/.

## How to play

1. The host signs in first, using the host password, and writes a scenario. Include the setting
   and the party's goal. The host may also add one optional percentage-based event and separate
   freeform private DM guidance.
2. The AI generates the scenario title. Players can then join using the player password. Everyone
   sees the banner and the host-typed scenario prompt.
3. When everyone is ready, the host clicks **Start Game**. The AI writes the **Opening scenario**,
   introducing the joined characters and their roles while explaining the setting and goal.
4. Players submit actions in join order. Once all actions are collected, the AI resolves them
   together as one round. The shared result describes the consequences of player actions within
   the world, and to them personally. Often new avenues to lead the plot to may appear.
5. Use party chat at any time, including while the AI is responding. Chat is not sent to the AI.
   If a round fails, the host can retry it with the same actions and dice, or end the game.

The player limit includes the host. The server assigns idle actions to disconnected players
so the game can continue. Story quality and consistency depend on the model;
the game cannot guarantee that it follows every instruction perfectly.

## One private percentage event

Use the **One private chance rule (optional)** controls for one random event per game:

| Control            | Meaning                                                               |
| ------------------ | --------------------------------------------------------------------- |
| Chance             | Whole-number percentage from 0–100%; leave blank to disable the rule. |
| Cadence            | **Once each round** or **On a triggering occurrence**.                |
| Occurrence trigger | Required for conditional cadence; leave blank for every-round checks. |
| Eligibility        | Optional condition that must hold; it does not change the cadence.    |
| Effect             | What happens when the chance check succeeds.                          |
| Roll scope         | One shared check or one check per eligible player in the round.       |

For example, set Chance to 20, choose conditional cadence, use "A player enters a building"
as the trigger, "The building is unstable" as eligibility, and "The building collapses" as
the effect. Shared scope makes one check if the trigger and eligibility match; per-player scope
makes separate checks for matching players.

For every-round cadence without eligibility, Python schedules the checks directly. The AI
identifies conditional triggers and evaluates optional eligibility against current actions and
world state. Remaining inside a building does not count as entering again, but can satisfy an
every-round eligibility condition. Each matching player receives at most one check per round
with per-player scope; shared scope receives at most one check per round.
Keep conditions concrete and tied to the current actions or established situation; avoid
chains where one random event must trigger another check in the same round.

Alternatively, expand **Legacy chance text** and enter one single-line rule with exactly one
percentage and explicit timing, such as "Add a 40% chance every round that a bell rings."
Use the controls or legacy text, not both. Missing or mixed timing is rejected in legacy input.

Python makes the percentage rolls separately from action dice: 0% never triggers and 100%
always triggers when the condition occurs. Each new check is independent; a 2% chance does
not guarantee an event within 50 rounds. Checks begin with action rounds, not the opening scenario.
Rules, rolls, and failed checks stay private; players see only observable story consequences.
Server logs and private HTML transcripts record the checks.

The AI identifies whether a conditional trigger occurred and narrates the result, so those steps
still depend on model accuracy. A conditional result is used only if its trigger actually happens;
it cannot force a blocked action to succeed. A setting-conflicting attempt may still receive a
public difficulty roll when its outcome is uncertain, with the low plausibility reflected in the
result. Failed LLM rounds receive up to two automatic retries, retaining any dice already rolled.
If recovery fails or the overall deadline expires, the host can retry the paused round or end.
Leave Chance and legacy text blank when no percentage event is wanted.

## Freeform private guidance

Use **Additional freeform DM guidance** for non-probabilistic secret steering about the world,
story direction, pacing, or other compatible presentation choices. The AI should apply compatible
steering consistently without quoting the guidance. Do not put percentage-based rules in this
field; the server rejects them so the game can never accept more than one percentage event.

## Rejoining a game

A disconnected tab tries to reconnect automatically. If you close it, open the same game address
in the same browser profile and enter the same player name and password. Your saved browser
identity allows you to reclaim that character; the password and name alone are not enough.

Clearing site data, changing browsers, or using a different address can prevent recovery.
Rejoining restores the opening and current state, then replays available missed public events.
Action drafts and pending submissions are retained through connection interruptions within the
same game and cleared when joining a new game. The on-screen game log holds up to 500 entries
and chat holds up to 300.

## History and longer games

The **History** panel offers paginated public events, search, and a JSONL export. This public
history stays in server memory for the current game; no JSONL logs are automatically written.
Use **Export public history** to download a JSONL file before starting another game or restarting
the server. Public history excludes private DM guidance and hidden checks.

The game writes HTML transcripts to `.logged_games/` on the server. They include the original
scenario prompt, generated opening, player actions, results, and dice rolls. **Transcripts also
include private DM guidance and hidden checks**, which remain out of the players' live game log.
The private guidance section separates freeform guidance from the chance event. Structured
events show readable chance, timing, scope, trigger, eligibility, and effect fields.

For longer games, the AI summarizes older rounds into memory and checks the summary for lost
facts. If a summary fails those checks, the original history is kept. The context indicator
shows how much conversation is retained; its details explain the counting method, context
limit, and total AI usage. The total usage across calls is different from the space occupied
by the current conversation.

One server runs one game at a time. After ending a game, the host can click **Start new game**
to create a new scenario without restarting the server. Other players are disconnected and
must join again once the scenario is ready. The new game starts with fresh AI context, logs,
and History state; the previous game's archives remain on the server. A separate HTML transcript
is created when the new game starts, even if its title matches the previous game.
Restarting the server loses the live session, and a transcript cannot be loaded as a saved game.

## Development

See [quality checks](INSTALL.md#quality-checks), [the task list](TASKS.md), and
[the maintenance guide](AGENTS.md). Offline tests clean up their temporary files and may be
run during read-only reviews when temporary files are acceptable.

## Credits

Inspired by **AI Dungeon**, especially its earlier free web version, **AI Dungeon 2**.

## AI Credits

Alibaba Cloud's Qwen 3.8 27b and OpenAI's GPT-5.6 Luna and GPT-6 Astra models
assisted in the development of this app.

## Model benchmarking

The new benchmarks/benchmark_chance_events.py script can be used to benchmark your local model's
ability to follow instructions, and to test out different model settings.
It creates a set of trigger-events and runs a benchmark on whether the AI properly responded to the caused trigger-event or not.
This is not a direct test for whether the model is fit to be a DM for this game, but a lot of failures means the model is very unlikely to be suitable.
Also, the json logged responses can give an indication of the model's general intelligence and creativity.

Development showed that the proper configurations (temp, top-p, top-k, presence-penalty and repeat-penalty and others)
are a massive influence on how well the model passes the benchmark. **With good model settings,
the benchmark pass rate for a model climbed from 62% to a consistent 100% over several runs.**

Make sure to find out what are the proper settings for the model you plan to use.

Read a more comprehensive description and a couple of model recommendations in [INSTALL.md](INSTALL.md)

### Running benchmarks

```bash
# Activate venv first if not already done.
python -B benchmarks/benchmark_chance_events.py --output benchmarks/model-bench-<model-name>.json
```
