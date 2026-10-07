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

If someone else is hosting, ask them for the game link and player password.
You do not need to install Python, Docker or an AI model to join.

If you are hosting, choose one setup guide:

| Setup                                                         | Guide                                           |
| ------------------------------------------------------------- | ----------------------------------------------- |
| Docker: run llama.cpp on your NVIDIA GPU, or use OpenAI       | [Docker Quick Startup](DOCKER.md#quick-startup) |
| Python: run the game directly and connect it to an AI service | [Install and configure](INSTALL.md)             |

**llama.cpp** runs an AI model on your computer. **OpenAI** runs the model on its
servers and charges for API use. OpenAI narration follows the language of your
scenario; the local backend currently requests English narration.

## How to play

1. The host opens the game link, enters a character name and the host password,
   then describes the setting, characters and goal. Private guidance and a chance
   rule are optional.
2. Select **Generate scenario**. The AI creates a title. Other players can now
   join with their names and the player password. They see the host's scenario.
3. When everyone has joined, the host selects **Start Game**. The AI writes the
   opening and introduces the characters.
4. Players submit actions in the order they joined. After everyone has acted,
   the AI describes the results together as one round.
5. Use party chat at any time. Chat is visible to the party but is not sent to
   the AI. If a round cannot finish, the host can retry it or end the game.

The player limit includes the host. Disconnected players receive idle actions
when needed so the game can continue. Story quality depends on the model; it
may make mistakes or forget details.

## Optional private chance rule

The host can add a percentage- or trigger-based rule in **Chance-based event rule**. This adds unexpected
developments: during play, players see the effects rather than the rule or rolls.

| Control            | What to enter                                                              |
| ------------------ | -------------------------------------------------------------------------- |
| Chance             | A whole-number percentage from 0 to 100. Leave blank to turn the rule off. |
| Cadence            | When to check: once each round, or when a described event occurs.          |
| Occurrence trigger | The event to check for. Required only for **On a triggering occurrence**.  |
| Eligibility        | An optional condition that must also be true.                              |
| Effect             | What happens when the check succeeds.                                      |
| Roll scope         | One roll for everyone, or a separate roll for each eligible player.        |

For example: Chance **20**, Cadence **On a triggering occurrence**, trigger
**A player enters a building**, eligibility **The building is unstable**, and
effect **The building collapses**. Use one shared roll for one common result,
or per-player rolls for separate results.

Entering again can trigger another check; staying inside does not count as
entering. With **Once each round**, leave the trigger empty and use eligibility
if the rule should apply only in certain situations. Checks begin with action
rounds, after the opening. Each eligible player gets at most one check per round;
a shared rule gets at most one shared check.

0% never succeeds; 100% always succeeds when the rule applies. The AI decides
whether conditions apply and writes the effects, so AI can make mistakes.

## Optional private guidance and saved scenarios

Use **Additional freeform DM guidance** to steer the story, introduce surprises or
adjust pacing without announcing the plan to players. The AI receives this guidance
and reveals its effects through play; it may occasionally reveal more than intended.
Use the chance controls for percentage rules rather than putting them here.

The host can save, load and delete scenarios in the browser. Saved scenarios
include the scenario, private guidance and chance settings. They stay in that
browser profile on that game address. Clearing browser site data removes them;
changing the game address gives you a different set of saved scenarios.

## Rejoining a game

A disconnected tab tries to reconnect automatically. After closing a tab, open
the **same game address in the same browser profile**, then enter the same name
and password. The browser remembers a private identity for your character.
Your name and password alone cannot reclaim an existing character.

Changing browsers, clearing site data or using another address can prevent
rejoining. A recreated public tunnel has a new address, so the browser's saved
identity and scenarios from the old address will not be available there.
Action drafts survive interruptions in the same game and are cleared for a new game.

## History, archives and new games

**History** lets you browse and search public events. Use **Export public history**
to download a JSONL file: a text file with one event per line. Export before a
new game or server restart; public History is kept only in memory.

The server saves HTML transcripts in `.logged_games/`, including hidden guidance,
chance rules and rolls. **Sharing them with players after the session is encouraged:**
they reveal how the surprises and story direction came together. If you want to
reuse the same hidden instructions in a future game, check the transcript before
sharing it to preserve those surprises.
Docker keeps them in the project folder `data/logged_games/`;
[DOCKER.md](DOCKER.md#upgrade-and-back-up) explains how to export and back them up.

After **End game**, the host can select **Start new game** without restarting the
server. The other players must join again after the new scenario is ready.
The new game gets fresh AI memory and History; old transcripts remain.
Restarting the server loses the active game. Transcripts cannot be loaded as saved games.

For longer adventures, the AI summarizes older rounds to make room for new ones.
The game checks those summaries for missing facts, but perfect recall is not guaranteed.
The token display distinguishes the conversation currently kept by the AI from
the total AI usage across the game. Tokens are the small pieces of text models
process and, for paid services, bill for.

## For developers and model testers

See [quality checks](INSTALL.md#quality-checks), [the task list](TASKS.md),
and [the maintenance guide](AGENTS.md).

## Credits

Inspired by **AI Dungeon**, especially its earlier free web version, **AI Dungeon 2**.

## AI Credits

Alibaba Cloud's Qwen 3.8 27b and OpenAI's GPT-5.6 Luna and GPT-6 Astra models
assisted in the development of this app.

## Model benchmarking

The benchmarks/benchmark_chance_events.py script can be used to benchmark your local model's
ability to follow instructions, and to test out different model settings.
This is not a direct test for whether the model is fit to be a DM for this game, but a lot of failures means the model is very unlikely to be suitable.
Also, the json logged responses can give an indication of the model's general intelligence and creativity.

Development showed that the proper configurations (temp, top-p, top-k, presence-penalty and repeat-penalty and others)
are a massive influence on how well the model passes the benchmark. **With good model settings,
the benchmark pass rate for a model climbed from 62% to a consistent 100% over several runs.**

Read a more comprehensive description and a model recommendation in [INSTALL.md](INSTALL.md#benchmarking-local-model-instruction-following).

### Running benchmarks

```bash
# Activate venv first if not already done.
python -B benchmarks/benchmark_chance_events.py --output benchmarks/model-bench-<model-name>.json
```
