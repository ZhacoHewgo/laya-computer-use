# Laya Computer Use

**A local browser decision agent built with Laya on Apple Silicon.**

> [!NOTE]
> This project is a clone of **[jev-ultrafast](https://github.com/browser-use/jev-ultrafast) by [Browser Use](https://github.com/browser-use)**, ported to make its decisions with **[Laya](https://github.com/mizorewww/laya-mlx)** running locally through MLX. The browser agent, DOM snapshot, executor, safety checks, inspector and most of the design are theirs. All credit for the original work goes to the jev-ultrafast authors. For how the agent works, see the [original repository](https://github.com/browser-use/jev-ultrafast).

This repository continues the work in [ipenywis/laya-ultrafast](https://github.com/ipenywis/laya-ultrafast). It retains the original MIT attribution and adds Unicode-aware matching, a Chinese local fixture, same-page result handling, current `laya-mlx` compatibility, and model-agnostic inspector wording.

> [!IMPORTANT]
> **Apple Silicon only.** Laya runs through [laya-mlx](https://github.com/mizorewww/laya-mlx), which needs an M-series Mac, macOS 14+, and Python 3.11+ (this project uses 3.12+). If you're looking to run it on a different OS/machine, check the original [Laya](https://github.com/NandhaKishorM/laya)

## What is different from jev-ultrafast

jev-ultrafast asks [TypeSafe's Jev](https://docs.typesafe.ai/introduction), a hosted model, to choose each browser action. This port replaces that API call with **Laya**, an open-weight typed-decision model that runs on your Mac:

- **No decision API charge.** Laya inference runs on your Mac. One browser step can use several model calls plus rules; step time is not a single forward-pass benchmark.
- **One planning stage per task.** An OpenAI-compatible model (OpenRouter by default, or a local server) turns the goal into field values, the item to open, and a finish condition. Invalid plans may trigger up to three calls. With a local planner and downloaded weights, inference needs no cloud API.
- **A different policy.** Laya answers narrow questions well: which field is the destination, whether `Tue, Oct 20` matches `October 20, 2026`, which suggestion is London. It does not reliably answer the open question "what should the browser do next?". So [`laya_ultrafast/laya.py`](laya_ultrafast/laya.py) combines narrow Laya questions with rules that apply on any site:
  1. Fill the values the goal states. Laya maps each one to a field, and Laya or plain code checks it.
  2. After typing or opening a control, choose from the options that appeared.
  3. Submit, then open the item the goal names, or wait for results that name the requested values.

  Every target is still an element the agent observed on the page, and there are no site-specific plans.
- **Hosted mode still works.** Set `DECISION_MODEL=typesafe` to use the original Jev policy unchanged.

## Improvements in this repository

- **Chinese and Unicode matching:** CJK text is retained instead of being discarded by ASCII folding. Overlapping CJK terms let labels such as `出发地` match requirements such as `出发城市`, while accented Latin text such as `Zürich` still normalizes predictably.
- **Chinese dates and controls:** the deterministic layer recognizes `2026年10月20日`, Chinese search and submit labels, and Chinese negative toggle wording.
- **Single-page applications:** a form submission may finish from visible result evidence even when the host and path do not change.
- **Local Chinese fixture:** the inspector includes a non-transactional `中文车票搜索` scenario for examining fields, choices, probabilities, and actions without touching a real account.
- **Current runtime:** the project targets `laya-mlx` 0.2.x.

These changes improve input handling and coverage. They do not establish general Chinese browser-task accuracy; evaluate the actual sites and goals you intend to automate.

## Laya setup

Laya itself is documented in **[mizorewww/laya-mlx](https://github.com/mizorewww/laya-mlx)**, an independent MLX port of [Convai Innovations' Laya](https://github.com/NandhaKishorM/laya). Read it for requirements, checkpoints, benchmarks and troubleshooting.

This project installs `laya-mlx` as a dependency and uses the **`aac6fef/laya-typed-decisions-mlx`** checkpoint (421M parameters, 1,024-token context). Download it once:

```bash
uv sync                                          # installs laya-mlx and the `hf` command
uv run hf download aac6fef/laya-typed-decisions-mlx
```

Later runs load it from the Hugging Face cache and need no network for decisions. To use another checkpoint, set `LAYA_MODEL`. The laya-mlx README lists the options and their limits: for example, the English `laya` checkpoint has only a 512-token context.

## Quick start

```bash
git clone https://github.com/ZhacoHewgo/laya-computer-use.git
cd laya-computer-use
uv sync
uv run hf download aac6fef/laya-typed-decisions-mlx
cp .env.example .env    # then set TEXT_MODEL_API_KEY, or point TEXT_MODEL_BASE_URL at a local server
uv run laya
```

Open **http://127.0.0.1:8766**, choose a scenario, and click **Start demo → Run automatically**.

For the local Chinese fixture, choose **中文车票搜索 · 本地测试页**. The fixture generates fictional results without login or purchases. A remote text planner would receive the task, field labels and visible item labels; use the local setup below to keep inference on your Mac.

### Fully local visible demo / 完全本地演示

```bash
uv sync --extra local
uv run --extra local hf download aac6fef/laya-multilingual-mlx
uv run --extra local hf download mlx-community/Qwen3-1.7B-4bit
uv run --extra local python examples/local_demo.py
```

Open **http://127.0.0.1:8770**, then **Start demo → Run automatically**. The launcher starts an isolated Chrome profile, a loopback-only MLX planner, and the inspector. The inspector shows live screenshots of the browser being controlled. Press Ctrl+C in the launcher terminal to stop its three services. Chrome must be installed in `/Applications`.

The launcher uses cached weights only and overrides cloud settings. Ports default to `8770` (inspector), `8771` (planner), and `9334` (test browser); change them with `--port`, `--planner-port`, and `--browser-port`. Use `--laya aac6fef/laya-typed-decisions-mlx` to compare the English typed-decisions checkpoint after downloading it.

Qwen supplies field values; Laya answers constrained selection questions; rules compose the next operation. The trace reports real Laya call counts and leaves rule probabilities empty. A score is not a task success rate. This is a DOM-based browser demo, not screenshot understanding or general desktop control.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), just as in jev-ultrafast. Enable remote debugging at `chrome://inspect/#remote-debugging`, and run `uv run browser-harness --doctor` if the connection fails.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DECISION_MODEL` | `laya` | `laya` for local decisions, `typesafe` for the original hosted Jev policy |
| `LAYA_MODEL` | `aac6fef/laya-typed-decisions-mlx` | Laya checkpoint, from the Hub or a local path |
| `TEXT_MODEL_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint. `localhost` endpoints need no key |
| `TEXT_MODEL` | `inception/mercury-2.5` | Model that plans the task once per run |
| `TEXT_MODEL_API_KEY` | — | Required for remote endpoints |
| `TEXT_MODEL_REASONING` | `none` | Turns reasoning off for faster planning |
| `TYPESAFE_API_KEY`, `TYPESAFE_MODEL` | — | Only for `DECISION_MODEL=typesafe` |

For a fully offline setup with [Ollama](https://ollama.com):

```bash
TEXT_MODEL_BASE_URL=http://localhost:11434/v1
TEXT_MODEL=gemma4:latest
```

## Demos

| Scenario | Command |
| --- | --- |
| Google Flights (checks the final page) | `uv run --env-file .env python examples/flights.py --date 2026-10-20 --keep-open` |
| Skyscanner (checks the final page) | `uv run --env-file .env python examples/skyscanner.py --date 2026-10-20 --keep-open` |
| Any site and goal | `uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'` |
| Local Chinese fixture | `uv run laya`, then choose `中文车票搜索` in the inspector |

Flight sites only offer future dates, so pass `--date`. It defaults to 30 days ahead. The examples never select or book a flight.

**Skyscanner** may show an "Are you a person or a robot?" check, especially to automated or headless browsers. The agent does not try to get past it. Run it in your everyday Chrome and solve the check yourself if it appears. Skyscanner also ticks "Add a place to stay" by default, so its goal says "without adding a place to stay".

## Upstream measurements (not re-measured results for this fork)

For this fork's Apple M4 + local Qwen + multilingual Laya results, see [Qwen 规划修复与验证](docs/fixes-validation-20260926.md) and the [earlier baseline](docs/local-validation.md). That bounded run passed nine local scenarios; Python Docs, Wikipedia and Google Flights did not pass.

The newer [搜索等待与滚动修复](docs/search-results-fix-20260926.md) uses saved plans supplied by GPT in a Codex conversation, with zero text-model API calls during execution. Python Docs and three local scenarios passed; Wikipedia and Google Flights still failed. This is a supplied-plan execution comparison, not an automatic GPT API integration or a general success-rate benchmark.

The following numbers are retained from [ipenywis/laya-ultrafast](https://github.com/ipenywis/laya-ultrafast). They report an M1 Max with `inception/mercury-2.5` on OpenRouter. They do not measure this fork's Qwen or multilingual configuration:

| Task | Result | Time |
| --- | --- | --- |
| Google Flights, one way Zürich → London, checked by `examples/flights.py` | 5/5 passed | 7.5–12.1 s |
| Wikipedia: open the Gödel's incompleteness theorems article | 2/2 | ~3–5 s |
| Local hotel fixture: filters, search, open Casa Flora | 2/2 | ~1.7 s |
| Local reading-room fixture: open the matching article | 2/2 | ~1 s |
| Skyscanner | Form filled end to end, then blocked by the robot check in headless testing | — |

This is a small set of repeated tasks, not a general reliability benchmark. The original Jev measurements, video and methodology are in [jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

## Limitations

- Runs only on Apple Silicon, because Laya runs through MLX.
- The Laya policy is new and tested on few sites. The original jev-ultrafast limits still apply: shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling and arbitrary keyboard widgets are out of scope. See [its README](https://github.com/browser-use/jev-ultrafast#evidence-and-limits).
- A `DONE` decision is not proof of success. The examples check the final page independently.
- Planning quality depends on the text model. `inception/mercury-2.5` sometimes returns malformed JSON, so the planner retries up to 3 times.

## Everything else

The action space, DOM snapshot, executor, freshness and occlusion checks, the inspector, and the performance work all come from jev-ultrafast. **See [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** for how they work, the design notes, and the original evidence. The files in [`docs/`](docs/) are the original project's records and describe the hosted Jev runs.

Development checks are the same as upstream:

```bash
uv run ruff check .
uv run pytest            # offline: a fake stands in for Laya; no downloads or paid calls
node --check laya_ultrafast/static/app.js
node --check laya_ultrafast/snapshot.js
uv build
```

## Credits

- **[jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** by [Browser Use](https://github.com/browser-use): the original project. This repository is a clone and a port of it.
- **[Browser Harness](https://github.com/browser-use/browser-harness)** by Browser Use: the Chrome connection.
- **[laya-mlx](https://github.com/mizorewww/laya-mlx)**: the MLX runtime and converted checkpoints for Laya.
- **[Laya](https://github.com/NandhaKishorM/laya)** by Convai Innovations and contributors: the model and its weights.
- **[TypeSafe Jev](https://docs.typesafe.ai/introduction)**: the hosted policy the original project uses, still available here as `DECISION_MODEL=typesafe`.

## License

[MIT](LICENSE), unchanged from the original: Copyright (c) 2026 Browser Use. Laya and laya-mlx are Apache-2.0 under their own licenses. See their repositories.
