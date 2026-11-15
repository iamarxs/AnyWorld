# Running Anyworld on a Mac

> **Untested beta guidance:** the maintainer has no Mac and has not tested these
> commands, model downloads, Metal inference, or the Compose connection. Expect
> to troubleshoot platform and model compatibility issues.

Apple Silicon Macs can run llama.cpp with Metal when `llama-server` is installed
on macOS. Docker Desktop runs Linux containers in a VM, so a Linux inference
container cannot use the Mac's Metal GPU. Run Anyworld in Docker and run the
inference server natively on the Mac.

## Requirements

- Apple Silicon Mac and a current macOS release.
- Homebrew, Docker Desktop, and Docker Compose.
- Enough unified memory for the chosen model, its context cache, macOS, and other
  applications. The default model weights alone are about 14 GB; a 16 GB Mac is
  unlikely to have comfortable room for the configured long context.

## Option A: native Metal-enabled llama.cpp (recommended)

1. Install llama.cpp with Homebrew. The macOS build enables Metal:

   ```sh
   brew install llama.cpp
   ```

2. Download the same GGUF used by the CUDA setup. From the repository root:

   ```sh
   python3 -m pip install --user huggingface_hub
   hf download \
     EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUF \
     gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-Q4_K_XXL.gguf \
     --local-dir models
   ```

   This preserves the CUDA deployment's model and quantization. If Hugging Face
   access requires a token, log in with `hf auth login` first.

3. Start the server from the repository root (adjust the template path if using
   a different model):

   ```sh
   llama-server \
     --model models/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-Q4_K_XXL.gguf \
     --host 0.0.0.0 --port 8033 \
     --ctx-size 131072 --n-gpu-layers 99 \
     --jinja \
     --chat-template-file docker/templates/google-gemma-4-canonical-templ.jinja
   ```

   This command and its settings are untested on macOS. Reduce `--ctx-size` if
   the model does not fit in available unified memory. Keep the server running.

4. In `config.yaml`, set the compatible provider to use:

   ```yaml
   llm:
     provider: compatible
     endpoint: http://host.docker.internal:8033/v1
     model_name: local
   ```

   Keep the rest of your existing `llm` settings. `host.docker.internal` lets
   the game container reach the Mac's host service; this has not been verified
   for this project.

5. Start the game using the remote-provider Docker setup in `../DOCKER.md`,
   selecting only `compose.yaml` in `docker.env` (do not select
   `compose.llama.yaml`).

## Option B: vLLM-Metal on the Mac (beta)

vLLM-Metal is a native macOS service, not a Linux Docker image. Install it on
Apple Silicon with macOS 15 or later:

```sh
brew tap vllm-project/vllm-metal https://github.com/vllm-project/vllm-metal
brew install vllm-project/vllm-metal/vllm-metal
```

Start the server from the repository root. On first start it downloads the model
from Hugging Face into the local cache:

```sh
vllm serve mlx-community/gemma-4-26b-a4b-it-4bit \
  --served-model-name local --host 0.0.0.0 --port 8000
```

As an abliterated alternative, try
`ailexleon/gemma-4-26B-A4B-it-qat-uncensored-heretic-mlx-lm-4Bit` in place of
the model ID above. This checkpoint is not verified with vLLM-Metal.

In `docker.env`, select `compose.yaml|docker/compose.vllm-metal.beta.yaml` and
leave `compose.llama.yaml` out. Start Anyworld with the normal Docker Compose
command. The overlay points the game at the native server; it does not start or
manage that server, so start vLLM-Metal first and leave it running. Its model
download happens when the native server starts, not when Compose starts.

## Troubleshooting notes

- Check the native server locally at `http://localhost:8033/health` for
  llama.cpp or `http://localhost:8000/health` for vLLM-Metal.
- If the game cannot connect, check that the native server is bound to
  `0.0.0.0`, and that the endpoint in `config.yaml` or the beta Compose overlay
  uses `host.docker.internal` and the matching port.
- The current game configuration and prompt defaults were tuned with llama.cpp.
  vLLM-Metal has a compatible API, but model loading, chat templates, tokenizer
  behavior, structured output, context sizing, and Anyworld gameplay have not
  been tested together.
