// Markovian in the Pricise sidecar. This is the MARKOVIAN_IMPL target for the Meterless conformance suite:
//   MARKOVIAN_IMPL=<repo>/backend/engines/world_model_ts/markovian/index.ts \
//     npx --prefix <repo>/backend/engines/world_model_ts tsx <meterless-clone>/engines/markovian/conformance/runner.ts
// Everything here is the vendored reference engine (./src, Meterless commit 1742dec, unchanged, Apache-2.0).
// Pricise never passes historyDir (the reference would write every chunk's content and carryover to history.json):
// the wishlist watch keeps its own checkpoint of structured facts (backend/watch.py).
export * from "./src/index.ts";
