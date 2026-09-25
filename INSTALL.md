# Install and configure Anyworld

[Back to the game overview](README.md)

## Install

Clone the repository and change into its directory:

```bash
git clone https://github.com/iamarxs/AnyWorld.git
cd AnyWorld
```

Python 3.11 or newer is required. Run the remaining commands from the repository root. On
Linux, use `python3` and `venv/bin/activate`; on Windows Git Bash, use `py -3.11` and
`venv/Scripts/activate` (or replace `-3.11` with your installed Python version).

Linux:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -e .
```

Windows Git Bash:

```bash
py -3.11 -m venv venv
source venv/Scripts/activate
python -m pip install -e .
```

For formatting, linting, and tests, install the development extras:

```bash
python -m pip install -e '.[dev]'
```

Windows PowerShell (without activating the environment):

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -e .
# After configuring passwords and the model connection:
.\venv\Scripts\python.exe app.py
```

## Configure

Edit `config.yaml` in the project directory before launching. Set `host_password` and
`player_password` to distinct, nonempty passwords; replace any existing example values as well.
The application rejects missing or identical passwords, but does not enforce password strength.
The `null` values below are placeholders, not usable credentials.

The following is a configuration outline. Keep the full game instructions in the existing
`system_prompt` when changing connection settings; the short example here is not a replacement
for those instructions.

```yaml
server:
  host: "0.0.0.0"
  port: 4141
  host_password: null
  player_password: null
  max_players: 6
llm:
  provider: "compatible" # use "openai" for the direct OpenAI API
  endpoint: "http://localhost:8033/v1"
  api_key: "sk-no-key-required"
  context_window_size: 8192 # fallback; llama.cpp auto-detection takes precedence
  tokenizer_encoding: "cl100k_base"
  model_name: "local"
  system_prompt: >
    Direct a coherent multiplayer RPG. Resolve actions simultaneously, preserve established
    facts and exact player names, and reserve dice for meaningful risks. Keep private
    guidance secret. Return only the requested structured object.
```

Set `provider` to `compatible` for a local OpenAI-compatible backend (the default), or to
`openai` to connect directly to OpenAI (not yet tested live in this project).
For direct OpenAI use, set `api_key` to your OpenAI API key and set `model_name` to the OpenAI
model you want to use; `endpoint` is ignored. For local backends, `endpoint` must support
OpenAI-compatible structured chat completion parsing.
`context_window_size` is an optional fallback value. When using a compatible backend, Anyworld
still attempts to read the context size from the llama.cpp `/props` endpoint even when a value is
configured. A successful discovery takes precedence; if discovery is unavailable, the configured
value is used. The default fallback is a conservative 8,192 tokens. OpenAI does not currently
provide automatic context-limit discovery, so configure this value to the selected model's
documented limit. The configured value cannot increase the backend's actual capacity.
llama.cpp token counting uses its `/apply-template` and `/tokenize` endpoints when available.
For direct OpenAI, a configured `tokenizer_encoding` is used only if it matches the model's known
tiktoken encoding. Unknown models, mismatches, unavailable endpoints, or `null` encoding use a
conservative UTF-8-byte estimate. Schema/framing allowances and a safety margin are added; counts
are not advertised as exact. The token indicator's tooltip describes the counting method.

Optional limits under `llm` (defaults shown) bound model calls. Output caps are selected by
request type; memory audits share the summary output cap:

```yaml
initial_output_tokens: 1024
round_output_tokens: 2048
dice_output_tokens: 512
summary_output_tokens: 1024
token_safety_margin: 256
request_timeout_seconds: 120.0
max_retries: 1
```

Additional optional `llm` settings:

| Setting                      | Default | Behavior                                                                                                                                                                                                                 |
| ---------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `enable_thinking`            | `null`  | Sends `chat_template_kwargs.enable_thinking` only to compatible backends when set. Support depends on the backend/template; the current `config.yaml` sets it to `false`.                                                |
| `planner_system_prompt`      | `null`  | Replaces the system prompt for dice planning only. Scenario, private guidance, memory, and recent history are still supplied.                                                                                            |
| `compaction_target_fraction` | `0.75`  | After compaction starts, aims to leave the upcoming request within this fraction of the context window. Allowed range: `0.5`–`1.0`.                                                                                      |
| `history_round_limit`        | `null`  | Optionally requests earlier memory checkpoints after this many stored request/response pairs, including the generated opening; title generation is not stored. Allowed range: `2`–`100`; this is not a hard history cap. |

Choose caps that leave sufficient input capacity within the effective backend context, especially
for large parties. Before a request exceeds its budget, older rounds are merged into separate
durable memory, followed by a separate model audit of lasting facts. Empty, non-shrinking,
oversized, or rejected summaries leave the original memory intact; old history is not simply
discarded. A model audit reduces risk but cannot guarantee perfect recall. Oversized actions
are rejected while retaining the player's turn. An inference or compaction failure pauses the round
with its submitted actions and any existing dice preserved; the host can use **Retry paused round**
or **End game**. Retrying uses the same dice. Chat stays available during inference.

A normal round uses two generation requests: dice planning and narrative resolution. Compaction
adds summary and audit requests; retries and backend token-counting requests add further work.
The expandable token indicator uses backend tokenization for retained context when available,
labels conservative fallback estimates, identifies the context-limit source, and shows round/game usage,
cache counters, errors, retries, and timing. Missing provider counters appear as unknown. Cached
input still occupies context, and retained context is not the full size of the next request.

Dice planning includes private guidance, durable facts and recent history so old injuries, obstacles
and secret triggers remain relevant. Public output is instructed to reveal only observable consequences;
direct guidance echoes and explicit hidden-roll disclosures are rejected. This guard is not a guarantee
against every possible paraphrase of a secret.

Planning instructions default to no roll for routine observations or searches. A concrete
obstacle, opposition, or hazard can justify a check; atmosphere alone should not. These are
model instructions, not a deterministic classifier, so decisions depend on the backend. Dice
values are generated by the server from 0 through 100 inclusive. Exact repeated player-name
labels are removed from outcomes before display and history storage; arbitrary spelling errors
in generated prose are not automatically corrected.

Pending sockets receive no game broadcasts. Optional `server` admission settings are
`max_pending_connections: 32`, `auth_timeout_seconds: 30.0`, and `max_auth_attempts: 3`.
Reconnects require a private per-player token as well as the password. A disconnected tab
reconnects automatically using its session credentials. If you close the tab or browser, reopen
the **same address** in the **same browser profile** and enter the same player name and password.
After successful login, the browser saves that player's ID and reconnect token in localStorage;
a new tab can restore them after you enter your credentials. Passwords and password digests
are not saved in localStorage. Rejoining replaces the previous socket for that player.

Clearing site storage, using a different browser/profile, or changing the scheme, hostname/IP,
or port loses access to that saved identity. The shared password and name alone cannot reclaim
an existing player. Private browsing or blocked storage may prevent recovery after closing the
tab. Sessions created before this recovery feature need one successful login/reconnect in the
original tab with the updated client before their identity is saved for new-tab recovery.
Reconnect snapshots restore current state and submitted actions, not the entire past log. The UI
keeps at most 500 game-log elements (including the banner) and 300 chat entries. The banner is
the first item in the game pane and scrolls with its contents.

The title-only request uses at most 128 output tokens (or the initial output cap if lower).

## Run

For `provider: compatible`, start your model server first. Development has used llama.cpp;
compatibility with other servers depends on their structured-response support. Direct OpenAI
support exists but has not been tested live in this project. Run from the repository root:

```bash
python app.py
# or, after installation:
anyworld
```

Open [https://127.0.0.1:4141/](https://127.0.0.1:4141/) on the server machine.
The application serves HTTPS; an `http://` URL will not work with the normal launcher.

See [How to play](README.md#how-to-play) for the host and player steps.

Other players connect to `https://<server-IP>:4141/` using the server's LAN address, or its public
address for internet play. Allow the configured TCP port through the firewall; internet play may
also require router port forwarding. Remove temporary forwarding when the session ends.

### AI model recommendation

During development, Gemma 4 26B A4B with a 128k context was used as the DM AI.
This is a record of the project's setup, not a minimum requirement or a guarantee of story quality.
Smaller context limits require more frequent summaries; memory checks cannot guarantee perfect recall.

**These sampling settings provided a more than adequate game experience with a Q4 quantized Gemma 4:**

```yaml
temperature: 1.0
top-p: 0.95
top-k: 20
min-p: 0.0
presence-penalty: 0.0
repeat-penalty: 1.0
```

Configure these in the model server; Anyworld does not set them.

Of course, feel free to try out your own models!

### HTTPS certificates

`api/tls_bootstrap.py` creates `certs/cert.pem` and `certs/key.pem`. It attempts public-IP
discovery via external services, falling back to a local interface address. Certificates cover
that detected IP and `127.0.0.1`, last 825 days, and are regenerated at startup when missing,
within 30 days of expiry, or no longer covering the detected IP. They do not include the
`localhost` hostname or every LAN address.

**Browsers will display a warning to joining players because the certificate is self-signed**
("Your connection is not private"), and may also report an address mismatch when using a
different address from the primary LAN or WAN IPs. For a server you recognize and trust,
a joining player can use the browser's certificate exception that's available in most modern
browsers and allows to continue to the site.

The host can also send players the cert.pem file, which they can then deploy to their browser's
trusted certificate storage, allowing them to join without issues or warnings.

**Never give out the private key.pem file**, as it will allow a malicious attacker to impersonate
your server.

One more option is that the host can create a reverse proxy, circumventing the need to hand out
the public certificate file.

### Server lifecycle

One server process hosts one in-memory game. Multiple workers and concurrent independent games
are not supported. Restarting loses the live session; the HTML transcript is a record, not a
loadable save. After ending a game, restart the server to begin another.

Restart after configuration changes. CLI overrides are available as `--host` and `--port`, for
example `anyworld --host 0.0.0.0 --port 4141`. Development reload is available with
`python app.py --reload`; code-triggered reloads also reset the in-memory game.

## Quality checks

Set `PYTHONDONTWRITEBYTECODE=1` before the checks: `export PYTHONDONTWRITEBYTECODE=1`
in Bash, or `$env:PYTHONDONTWRITEBYTECODE = "1"` in PowerShell.

```bash
black --check app.py api core logic tests
flake8 app.py api core logic tests
python -B -m pytest -p no:cacheprovider
# Client reconnect and password-hashing regressions (requires Node.js):
node --test tests/client_reconnect.test.cjs
```

Tests use isolated settings and fake model clients; they do not require a running LLM.
Each test runs in a temporary working directory that is deleted on teardown, including after
test failures. HTML transcripts, debug logs, and temporary configuration files stay there;
existing game logs and user files are not removed. Tests may run during read-only reviews
when temporary files are acceptable. The command above disables Python bytecode and pytest's
cache; use `PYTHONDONTWRITEBYTECODE=1` as well to suppress bytecode in child Python processes.
In-process ASGI tests are included; live server startup and opt-in backend benchmarks are not
part of this review-safe workflow. Cleanup cannot be guaranteed after forced process termination.

## Logs and diagnostics

The game logs quite a bit of its behavior to the console. Logs include compaction stages,
estimates, timing and rollback; inference jobs, retries, accepted actions, and transcript writes.
Private-guidance checks log player names, roll values, and whether a retry reused the rolls.
These logs are for the server operator; private checks are not broadcast to players.

### Raw model responses

To investigate generated wording, launch with `python app.py --debug` or
`anyworld --debug-raw-responses`. The flag also works with `--reload` and enables logging
for that launch without changing `config.yaml`. Alternatively, set `debug_raw_responses: true`
under `llm` in the configuration. Raw-response file logging is disabled by default. Restarting the application
resets the current game. Each completion HTTP response is saved as a timestamped JSON file under
`.debug/llm/` in the working directory. The `body` field contains the raw response text,
recorded before SDK parsing, narrative checks, name normalization, or display. This includes
responses rejected during retries and HTTP error responses. Connection failures with no HTTP
response cannot produce a raw-response file.

These files are private diagnostics: model output can include hidden dice or private guidance,
and error bodies can contain sensitive data. They are excluded from Git and are not served by
the web app. Request prompts, headers, and credentials are not deliberately logged. Files are
not automatically rotated; disable the option after diagnosis and remove unneeded logs.
Sampling settings remain controlled by the backend; Anyworld does not override repetition or
presence penalties.
