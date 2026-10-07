# Install and configure Anyworld

[Back to the game overview](README.md)

This guide runs the game directly with Python. For Docker, use
[DOCKER.md](DOCKER.md), including its [Quick Startup](DOCKER.md#quick-startup).

## Download the project

Clone the repository, or download and extract its ZIP from
[GitHub](https://github.com/iamarxs/AnyWorld). Run commands from the project root:

```bash
git clone https://github.com/iamarxs/AnyWorld.git
cd AnyWorld
```

## Install

Requires [Python](https://www.python.org/downloads/) 3.11 or newer.

Windows PowerShell:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -e .
```

Adjust `-3.11` to your installed Python version.

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

## Configure

Copy `config.example.yaml` to `config.yaml` before launching:

```powershell
Copy-Item config.example.yaml config.yaml
```

On Linux or Git Bash, use `cp config.example.yaml config.yaml` instead.
Do this only for a new setup; copying again would replace your existing settings.
The example file is a template and is not loaded automatically.

Set distinct, nonempty `host_password` and `player_password` values. Password
strength is not enforced. Preserve the complete `system_prompt` from the example;
the abbreviated outline below is not a replacement configuration.

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
  context_window_size: 16384 # used only if local context discovery fails
  openai_context_window_size: 245760 # keeps OpenAI requests below higher pricing
  tokenizer_encoding: null # legacy fallback; local llama.cpp uses its own tokenizer
  openai_tokenizer_encoding: auto
  model_name: "local"
  system_prompt: >
    Direct a coherent multiplayer RPG. Resolve actions simultaneously, preserve established
    facts and exact player names, and reserve dice for meaningful risks. Keep hidden
    guidance out of narration during play. Return only the requested structured object.
```

Individual settings can be overridden without editing YAML by using the `AD_` prefix and
`__` for nested fields. Environment values take precedence over `config.yaml`; for example:

```dotenv
AD_SERVER__PORT=4242
AD_LLM__MODEL_NAME=local
AD_LLM__REQUEST_TIMEOUT_SECONDS=180
```

`AD_OPENAI_API_KEY` is the only API-key source when `provider: openai` is selected. Secrets should
stay in the process environment or `.env`, not in a committed configuration file.

### Switching between local llama.cpp and OpenAI

The default is the local llama.cpp-compatible backend:

```yaml
llm:
  provider: "compatible"
  endpoint: "http://localhost:8033/v1"
  model_name: "local"
  api_key: "sk-no-key-required"
```

Start llama-server and load a model before starting Anyworld. The game does not
install or launch it in a Python setup. For automatic installation, model download
and startup, use [Docker Quick Startup](DOCKER.md#quick-startup).

If you already run llama-server, set `endpoint` to its address, including `/v1`,
and set `model_name` to its served alias. The server
must support structured responses through the OpenAI-compatible Chat Completions API.
Anyworld asks llama.cpp for its context limit and token counts when supported.

To use OpenAI directly, follow the [official API setup guide](https://developers.openai.com/api/docs/quickstart)
for API credentials and billing. Set the key in a project-root `.env` file:

```dotenv
AD_OPENAI_API_KEY=your-api-key-here
```

Keep `.env` private. Anyworld loads `AD_OPENAI_API_KEY` with `python-dotenv` when the provider is
`openai`; the key does not need to be written into `config.yaml` and is never printed in normal
logs. Then change these values in the existing `llm` section; keep its other settings
and full `system_prompt`:

```yaml
llm:
  provider: "openai"
  model_name: "gpt-5.6-luna"
  context_window_size: 16384 # fallback for local backends
  openai_context_window_size: 245760 # budget below the long-context pricing threshold
  openai_tokenizer_encoding: auto # GPT-5.6 maps to o200k_base in tiktoken
  endpoint: "http://localhost:8033/v1" # ignored for provider: openai
  api_key: "sk-no-key-required" # ignored for provider: openai; use AD_OPENAI_API_KEY
```

Direct OpenAI support has been tested live with `gpt-5.6-luna`, including scenario titles,
opening-state generation, dice planning, and round resolution. The model's documented context
window is approximately 1.05 million tokens, but the example intentionally uses a smaller
OpenAI budget to avoid long-context pricing; see the explanation below.
The token display distinguishes retained context from total AI usage.

To switch back, restore `provider: "compatible"`, the local endpoint, and the local model name.
The `.env` file may remain in place; its key is only used when `provider: "openai"` is selected.

### Context size and OpenAI costs

For local backends, `endpoint` must support OpenAI-compatible structured chat completion parsing.
`context_window_size` is an optional fallback value. When using a compatible backend, Anyworld
still attempts to read the context size from the llama.cpp `/props` endpoint even when a value is
configured. A successful discovery takes precedence; if discovery is unavailable, the configured
value is used. The example uses 16,384 tokens; omitting the setting uses the internal
default of 8,192. OpenAI does not
provide automatic context-limit discovery; its optional `openai_context_window_size` setting
selects a separate fallback (245,760 in the examples). If omitted/null, it continues using
`context_window_size` for existing configurations. Neither setting increases the backend's
actual capacity; the configured budget must not exceed the selected model's supported limit.

**Why use `openai_context_window_size: 245760` instead of the model's maximum?**
The currently recommended `gpt-5.6-luna` supports a much larger context, but prompts above
272,000 input tokens incur higher per-token prices: **2x input and 1.5x output for
the entire request**, not just the tokens beyond the threshold.
See the [official model pricing](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
For models with this pricing rule, 245,760 is a deliberate cost-control budget. It makes
Anyworld compact the game context before exhausting that smaller budget, rather than waiting
until the model's maximum context is approached. This leaves 26,240 tokens of headroom below
the 272,000-token pricing threshold; the request budget also reserves output, schema/framing
overhead and a safety margin. Keep this value unless you deliberately want larger, more
expensive requests, and check the chosen model's current pricing when switching models.
Token bounds remain estimates, so this is a budgeting precaution rather than a billing guarantee.

llama.cpp token counting uses its `/apply-template` and `/tokenize` endpoints when available.
For OpenAI, `openai_tokenizer_encoding: auto` selects the model's known tiktoken
encoding. The installed tiktoken maps GPT-5.6 models, including Luna, to `o200k_base`;
it currently has no GPT-6 mapping. Unknown models, explicit encoding mismatches or
unavailable encodings use conservative UTF-8-byte estimates. An explicit
encoding such as `o200k_base` is accepted only when it matches a known model mapping.
If `openai_tokenizer_encoding` is omitted/null, the legacy `tokenizer_encoding`
setting applies; setting both to `null` disables local OpenAI tokenization. Neither
setting overrides llama.cpp's model tokenizer. Schema/framing allowances and a
safety margin still apply. The token indicator shows the counting method.
See [OpenAI's token-counting guidance](https://developers.openai.com/api/docs/guides/token-counting)
for the limitations of local estimates.

### Advanced AI settings

Optional `llm` settings and defaults. Output limits include hidden thinking tokens:

```yaml
initial_output_tokens: 4096
round_output_tokens: 4096
dice_output_tokens: 768
summary_output_tokens: 3072
token_safety_margin: 256
request_timeout_seconds: 120.0
max_retries: 1
```

Additional optional `llm` settings:

| Setting                      | Default | Meaning                                                                                                                                                                                                           |
| ---------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reasoning_effort`           | `none`  | Requested thinking level: `none`, `low`, `medium` or `high`. Support depends on the model and chat template. `none` is recommended for gameplay.                                                                  |
| `compaction_target_fraction` | `0.75`  | After summarizing older text, aim to fit the next request within 75% of the configured context. Allowed values: `0.5`–`1.0`.                                                                                      |
| `history_round_limit`        | `null`  | Optional earlier summary checkpoint after this many saved request/response pairs, including the opening. Allowed values: `2`–`100`; `null` disables this extra trigger. It does not delete history at that count. |

Low, medium and high thinking reserve an additional 2,048, 4,096 or 8,192 output
tokens.

Scenario titles and the short dice, event-audit, and summary-audit requests force
`reasoning_effort: none` so their small structured-output budgets are not consumed by hidden
thinking. Round, opening, and compaction-summary generation use the configured effort.

Choose caps that leave sufficient input capacity within the effective backend context, especially
for large parties. Before a request exceeds its budget, older rounds are merged into separate
durable memory, followed by a separate model audit of lasting facts. Empty, non-shrinking,
oversized, or rejected summaries leave the original memory intact; old history is not simply
discarded. A model audit reduces risk but cannot guarantee perfect recall. Oversized actions
are rejected while retaining the player's turn. An inference or compaction failure pauses the round
with its submitted actions and any existing dice preserved; the host can use **Retry paused round**
or **End game**. Retrying uses the same dice. Chat stays available during inference.

A basic round asks the AI to plan dice checks and then describe the results.
Conditional chance rules, eligibility checks and hidden checks can add requests.
Summaries, audits and retries add more work; token-counting calls also take time.
The expandable token indicator uses backend tokenization for retained context when available,
labels conservative fallback estimates, identifies the context-limit source, and shows round/game usage,
cache counters, errors, retries, and timing. Missing provider counters appear as unknown. Cached
input still occupies context, and retained context is not the full size of the next request.

Dice planning includes hidden guidance, durable facts and recent history so injuries,
obstacles and event triggers remain relevant. During play, narration presents their
consequences rather than the underlying instructions. Direct guidance echoes and
explicit hidden-roll disclosures are rejected, though the model can still reveal
more than intended.

Scenario setup has **Chance-based event rule (optional)** controls and a separate
**Additional freeform DM guidance** field. Use the controls for one percentage
rule per game, including when it applies, who is eligible and what happens.
Leave Chance empty to disable it. Use hidden guidance for surprises, pacing or
story direction; percentage rules there are rejected. The old chance-text field
has been removed. See [the player guide](README.md#optional-private-chance-rule)
for an example. The server rolls the dice; the AI decides whether described
conditions apply and writes the result.

Planning instructions default to no roll for routine observations or searches. A concrete
obstacle, opposition, or hazard can justify a check; atmosphere alone should not. A setting-
conflicting attempt may still receive a public difficulty roll when its outcome is uncertain,
with low plausibility reflected in the result. These are model instructions, not a deterministic
classifier, so decisions depend on the backend. Dice values are generated by the server from 0
through 100 inclusive. Exact repeated player-name
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
Rejoining restores the opening and current state, then catches up on available missed public
events from History. The on-screen log keeps at most 500 entries (including the banner)
and chat keeps 300. Use History to browse older public events. The banner is
the first item in the game pane and scrolls with its contents.

The title-only request uses at most 128 output tokens (or the initial output cap if lower).

## Run

For `provider: compatible`, start your model server first. Development has used llama.cpp;
compatibility with other servers depends on their structured-response support. For
`provider: openai`, ensure `.env` contains `AD_OPENAI_API_KEY`; startup fails if it is missing.
Run from the repository root:

Windows PowerShell:

```powershell
.\venv\Scripts\python.exe app.py
```

Linux or Git Bash, with the virtual environment activated:

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

Keep the launch terminal open. Ctrl+C stops the game. Restart with the same
command. Stopping loses the active game and public History; HTML archives remain
in `.logged_games/`. Sharing these transcripts after a session is encouraged: they
include hidden guidance and rolls, letting players see how events were steered.
If you want to reuse the same surprises, review the transcript before sharing.
Back up `.logged_games/` while stopped; include `certs/` to preserve certificates
and the key, keeping that key private.

### Troubleshooting

| Problem                              | What to check                                                                                                               |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Python or `py` is not found          | Install Python 3.11 or newer, then open a new terminal.                                                                     |
| A package cannot be imported         | Use the virtual environment's Python, and install with that same Python.                                                    |
| Password configuration error         | Both passwords must be filled in and different.                                                                             |
| YAML error                           | Check spelling and spaces; use the example as a guide and keep the full `system_prompt`.                                    |
| Model connection fails               | Start llama-server first. Check its address, port and model alias; include `/v1` in the game's endpoint.                    |
| OpenAI authentication or quota error | Check `.env`, API-key access and API billing.                                                                               |
| Browser certificate warning          | Use HTTPS and follow the certificate steps below. An account-free Docker tunnel gives a public HTTPS link.                  |
| Players cannot connect               | `localhost` works only on the host computer. Check the shared address, certificate coverage, firewall and router/VPN route. |
| A setting change has no effect       | Restart the game. Check `.env` and process `AD_` variables, which take priority over YAML.                                  |
| A round is paused                    | The host can retry it with the same actions and dice, or end the game. Check the console for the cause.                     |

### Benchmarking local-model instruction following

The chance-event benchmark is an opt-in live-model test. It exercises the configured compatible
backend with 20 scenarios and 40 action trials: 16 conditional rules each receive one triggering
and one non-triggering action, while four per-round rules receive two ordinary actions. All rules
use `100%`, so the score measures whether the model identifies when the event applies rather than
whether a random roll happened to succeed. The benchmark also prints the generated responses for
manual review, but excludes usage, token, retry, and timing diagnostics from its result records.

Start the selected local model server and verify the `llm.endpoint` in `config.yaml` points to its
OpenAI-compatible API. Load one model at a time, keeping sampling and context settings fixed when
comparing models. From the repository root, run the benchmark without starting the game server:

Linux or macOS:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B benchmarks/benchmark_chance_events.py \
  --output benchmarks/model-bench-<model-name>.json
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\venv\Scripts\python.exe -B benchmarks\benchmark_chance_events.py `
  --output benchmarks\model-bench-<model-name>.json
```

Use a different output filename for every model. The console ends with overall, conditional, and
per-round success rates plus the complete pass duration, for example
`overall=35/40 (87.5%) ... duration=184.321s`. The JSON report contains the same summary,
including `duration_seconds`, plus each event description, action, expected result,
detected/occurred flags, pass/fail status, and generated response. A conditional trial passes when its triggering action produces
the event and its non-triggering action does not; both trials for a per-round rule must produce
the event. A failed model request is recorded as a failed trial so one backend error does not
discard the rest of the run.

A score below 100% does not mean the model cannot work well as the DM AI. The game
has built-in response repair and automatic retries for detected inference failures;
if recovery is exhausted, the host can retry a paused round with the same actions
and dice. These systems help recover from occasional failures, but cannot correct
every plausible yet wrong event decision. Use the benchmark alongside actual play
to assess narration, consistency and reliability. Also note that model tests help
compare settings; a high score does not guarantee a good Dungeon Master.

Benchmark reports include chance-rule effects and model output. They are diagnostic
results rather than full game transcripts. The benchmark script is the one tracked
file under the otherwise ignored `benchmarks/` directory; generated JSON reports
and other benchmark scripts remain ignored.

### AI model recommendation

For a 16 GB NVIDIA GPU, the recommended local model is:

- Repository: [EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUF](https://huggingface.co/EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUF).
- File: `gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-Q4_K_XXL.gguf`.

Weights occupy about 14.3 GB, excluding KV cache and runtime allocations.
The supplied settings passed a short game on a 16 GB RTX 5070 Ti, but the
131,072-token setting is not guaranteed to fit every 16 GB card. CPU-only
inference is too slow for the supported game setup.

[Docker Quick Startup](DOCKER.md#quick-startup) downloads this model and uses the
included canonical chat template. The template permits thinking, but
`reasoning_effort: none` is recommended for gameplay.

These model-server settings worked well during development:

| Setting                                 | Value  |
| --------------------------------------- | ------ |
| Temperature (`--temp`)                  | `1.0`  |
| Top-p (`--top-p`)                       | `0.95` |
| Top-k (`--top-k`)                       | `20`   |
| Min-p (`--min-p`)                       | `0.0`  |
| Presence penalty (`--presence-penalty`) | `0.0`  |
| Repeat penalty (`--repeat-penalty`)     | `1.0`  |

Set these in llama-server, not the game's YAML. The complete local launch settings
are in `compose.llama.yaml`. Other models may need different settings. Smaller
context limits cause more frequent summaries; summary checks do not guarantee
perfect recall.

### HTTPS certificates

The launcher uses HTTPS with `certs/cert.pem` and `certs/key.pem`.

By default, the launcher tries to find the computer's public IP address, falling
back to a local address. The generated certificate covers that address,
`127.0.0.1`, `::1` and `localhost`. If players connect through another IP or name,
list the addresses explicitly to avoid an address mismatch. For example, with
an activated Python environment:

```bash
anyworld --tls-address localhost --tls-address 192.168.1.50
```

Replace `192.168.1.50` with your computer's actual local network address. In
PowerShell without activation, use `.\venv\Scripts\python.exe app.py` followed
by the same flags. You can instead put `tls_addresses: ["localhost", "192.168.1.50"]`
under `server` in `config.yaml`. Explicit addresses skip automatic IP discovery.
Docker sets these addresses through `TLS_ADDRESSES` in `docker.env`.

Generated certificates are self-signed. Accept the browser exception for a trusted
server, or install `cert.pem` in the client's trust store. **Never share `key.pem`.**

Certificates last 825 days. At startup, the launcher replaces a generated pair
if it is missing, expires within 30 days or does not cover the requested addresses.
Changing settings does not update a running process; restart after changes.

If you already have a certificate and matching key, set `tls_certfile` and
`tls_keyfile` under `server`, or use `--tls-certfile` and `--tls-keyfile`.
Also list the addresses the certificate covers with `tls_addresses`. Supplied
files must match, cover those addresses and remain valid for more than 30 days.
The launcher checks them but does not renew them.

For a public link without browser certificate warnings or router changes, use
[the Docker tunnel](DOCKER.md#quick-startup). A separately configured HTTPS proxy
is also possible; it needs to support WebSocket connections.

### Server lifecycle

One server process hosts one in-memory game. Multiple workers and concurrent independent games
are not supported. Restarting loses the live session; the HTML transcript is a record, not a
loadable save. After ending a game, the host can select **Start new game** without
restarting the server. Players join again once the new scenario is ready.

Restart after configuration changes. CLI overrides are available as `--host` and `--port`, for
example `anyworld --host 0.0.0.0 --port 4141`. Development reload is available with
`python app.py --reload`; code-triggered reloads also reset the in-memory game.

## Docker deployment

See [DOCKER.md](DOCKER.md) for container installation, configuration and operation.

## Quality checks

Install developer dependencies in the virtual environment:

```bash
python -m pip install -e '.[dev]'
```

In PowerShell without activation, replace `python` with `.\venv\Scripts\python.exe`.
For the following checks, use an activated environment, or run each Python tool
as `.\venv\Scripts\python.exe -m black`, `-m flake8` or `-m pytest`.

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

Round inference failures receive up to two automatic retries before Retry/End is shown. These
retries retain actions and already generated dice and are logged on the server. They share the
existing overall job deadline (three times `request_timeout_seconds`); expiration pauses the round
even if retries remain. Per-request `max_retries` repairs still apply inside each round attempt.
Scenario preparation is not automatically retried by this round-level recovery.

Console logs include compaction stages,
estimates, timing and rollback; inference jobs, retries, accepted actions, and transcript writes.
Private-guidance checks log player names, roll values, and whether a retry reused the rolls.
These checks are logged on the server rather than broadcast during play.

The console displays Uvicorn's `uvicorn.error` logger as `uvicorn.server`.
The level (`INFO`, `WARNING` or `ERROR`) indicates severity; normal WebSocket
connection messages are informational.

### Raw model responses

For detailed AI diagnostics, run `python app.py --debug` or
`anyworld --debug-raw-responses`. In PowerShell without activation, use
`.\venv\Scripts\python.exe app.py --debug`. Alternatively, set
`debug_raw_responses: true` under `llm` in `config.yaml`. Docker uses
`compose.debug.yaml` and saves diagnostics in the host project folder `data/debug/`.

This option is off by default. It writes readable `.log` files in `.debug/llm/`.
They show the request, response, request type, time and HTTP status. AI responses
are captured before the game parses or checks them, including rejected responses
and errors. If the backend supplies thinking fields, those are included too.
Round requests may first appear as `.tmp` files; completed round diagnostics are
combined into a single `.log`. A failed or interrupted round can leave temporary
files for diagnosis. A request is recorded before waiting for the response, so
connection failures can still leave a useful record.

These host-side diagnostics include hidden rolls, guidance and model output.
Review them before sharing if you want to preserve surprises for future sessions.
Request headers and API credentials are excluded. The game does not serve these
files to browsers, and Git ignores them. They are not automatically
rotated or deleted: disable debug logging after diagnosis and remove files you no
longer need. Restarting to change debug settings loses the current game.
