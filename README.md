# Anyworld

![Anyworld banner](static/media/AnyworldBanner.jpg)

A multiplayer text adventure where you never have to roll dice or keep score — just write what your
character does. One player (the host) describes the scenario, then everyone takes turns acting in
their own words while an AI weaves every choice into a story that keeps unfolding.

## Get started

One person runs the game server and connects it to an AI model. Everyone plays in a browser.
You need Python 3.11 or newer on the server; development has used llama.cpp for the AI.
Direct OpenAI support is available but has not been tested live in this project.

See [INSTALL.md](INSTALL.md) for installation, passwords, model settings, network access,
and troubleshooting. Once installed and configured, run `anyworld` or `python app.py`
from the repository directory, then open [the local game page](https://127.0.0.1:4141/).

## How to play

1. The host signs in first, using the host password, and writes a scenario. Include the setting
   and the party's goal. Optional private DM guidance can provide secrets or special rules.
2. The AI generates the scenario title. Players can then join using the player password. Everyone
   sees the banner and the **Host-typed scenario prompt**, unchanged by the AI.
3. When everyone is ready, the host clicks **Start Game**. The AI writes the **Opening scenario**,
   introducing the joined characters and their roles while explaining the setting and goal.
4. Players submit actions in join order. Once all actions are collected, the AI resolves them
   together as one round. Longer descriptions are prompted to use paragraphs, and the shared
   result is prompted to reflect the consequences of player actions.
5. Use party chat at any time, including while the AI is responding. Chat is not sent to the AI.
   If a round fails, the host can retry it with the same actions and dice, or end the game.

The player limit includes the host. Disconnected players may receive idle actions so the game
can continue. Story quality and consistency depend on the model; the game cannot guarantee
that it follows every instruction perfectly.

## Rejoining a game

A disconnected tab tries to reconnect automatically. If you close it, open the same game address
in the same browser profile and enter the same player name and password. Your saved browser
identity allows you to reclaim that character; the password and name alone are not enough.

Clearing site data, changing browsers, or using a different address can prevent recovery.
Rejoining restores the opening and current state, not every missed round. The on-screen game
log holds up to 500 entries and chat holds up to 300.

## History and longer games

The game writes HTML transcripts to `.logged_games/` on the server. They include the original
scenario prompt, generated opening, player actions, results, and dice rolls. **Transcripts also
include private DM guidance and hidden checks**, so review them before sharing with players.
Private checks stay out of the players' live game log.

For longer games, the AI summarizes older rounds into memory and checks the summary for lost
facts. If a summary fails those checks, the original history is kept. The context indicator
shows how much conversation is retained; its details explain the counting method, context
limit, and total AI usage. The total usage across calls is different from the space occupied
by the current conversation.

One server runs one game at a time. Restarting loses the live session, and a transcript cannot
be loaded as a saved game. Restart the server to begin another game after ending one.

## Development

See [quality checks](INSTALL.md#quality-checks), [the task list](TASKS.md), and
[the maintenance guide](AGENTS.md). Offline tests clean up their temporary files and may be
run during read-only reviews when temporary files are acceptable.

## Credits

Inspired by **AI Dungeon**, especially its earlier free web version, **AI Dungeon 2**.

## AI Credits

Alibaba Cloud's Qwen 3.8 27b and OpenAI's GPT-5.6 Luna and GPT-6 Astra models
assisted in the production of this app.
