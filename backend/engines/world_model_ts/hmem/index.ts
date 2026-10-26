// H-MEM in the Pricise sidecar. This is the HMEM_IMPL target for the Meterless conformance suite:
//   HMEM_IMPL=<repo>/backend/engines/world_model_ts/hmem/index.ts npx tsx <meterless-clone>/engines/hmem/conformance/runner.ts
// HMEM is the vendored reference engine (./src, Meterless commit 1742dec, unchanged, Apache-2.0).
// PriciseMemory (./pricise.ts) is the Pricise layer the sidecar routes use: privacy policy, our own persistence, no
// sleep / dreaming / mining.
export { HMEM } from "./src/index.ts";
export { PriciseMemory, badNote, policy } from "./pricise.ts";
export type { Note, NoteInput, NoteKind, StoreTrust } from "./pricise.ts";
