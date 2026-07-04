# MockProvider fixtures (Session 18)

These JSON files are the canned, deterministic responses served by
`src.ai.llm.mock_provider.MockProvider` so the full pytest suite runs with **zero
network access and no API keys** (FR-3B-06 / NFR-T-06).

## Keying scheme

Each request is reduced to a stable **sha256 key** by
`src.ai.llm.mock_provider.canonical_key(model, system, messages)`:

1. Build the canonical object
   ```json
   {"model": <model_str>, "system": <system_or_null>,
    "messages": [{"role": ..., "content": ...}, ...]}
   ```
   - `messages` **keeps its order**, but each entry is reduced to just
     `{"role", "content"}` (any other keys are ignored).
   - `system` of `None` serializes as JSON `null`.
2. Serialize with `json.dumps(obj, sort_keys=True, separators=(",", ":"),
   ensure_ascii=False)` (object keys sorted; compact separators; Unicode kept).
3. `key = sha256(serialized.encode("utf-8")).hexdigest()`.

## Fixture file format

A fixture lives at `tests/fixtures/llm/{key}.json` and contains:

```json
{
  "text": "the canned completion text",
  "input_tokens": 12,
  "output_tokens": 7,
  "stop_reason": "end_turn"
}
```

`input_tokens` / `output_tokens` / `stop_reason` are optional (default `0` / `0`
/ `null`).

## Lookup order

`MockProvider.complete` resolves a request key as:

1. an in-memory `responses` mapping passed to the constructor (or via
   `register(key, payload)`);
2. a `{fixtures_dir}/{key}.json` file (when `fixtures_dir` is set);
3. a **deterministic synthetic fallback** derived from the key — still
   zero-network and reproducible. Most tests rely on this fallback (so they need
   not precompute a hash); register a payload only when a specific response text
   is required.

To author a fixture for a specific request, compute its key:

```python
from src.ai.llm.mock_provider import canonical_key
key = canonical_key("claude-sonnet-4-6", "You are a router.", [{"role": "user", "content": "hi"}])
```

then write `tests/fixtures/llm/{key}.json`.
