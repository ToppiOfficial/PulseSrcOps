# Import Rewrite - Phase Plan

Mirrors the `export/` package rewrite. Goal: split `import_smd.py` (2551 lines) into an
`importsrc/` package with a parse -> IR -> build pipeline, and replace the single
all-formats import button with one operator per format.

Order is **format by format**. DMX first. Nothing else moves until DMX lands.

---

# RESUME HERE (handoff, end of session 1)

## State

Branch `rewrite-export-smd`. **Nothing is committed.** Working tree:

```
 M io_scene_valvesource/__init__.py        menu_func_import -> menu; ImportDMX registered
 M io_scene_valvesource/gui/menus.py       SMD_MT_ImportChoice added
 M io_scene_valvesource/gui/panels.py      Import button -> menu; unused import removed
 M io_scene_valvesource/import_smd.py      ImporterBase/SmdImporter/ImportDMX split;
                                           readDMX rewired; _readDMX_legacy kept
 M io_scene_valvesource/translations.py    4 new keys
?? io_scene_valvesource/importsrc/         new package (7 modules)
?? .todo/                                  this file
?? tools/dmx_import_parity.py              new-vs-legacy differ (unused, user verified by hand)
```

- **Phase 1 (DMX) is done and hand-verified in Blender.** Import routes through
  `importsrc`; `KST_OLD_DMX_IMPORT=1` still falls back to `_readDMX_legacy`.
- **Phase 2 (SMD) is half done and NOT wired.** `importsrc/smd.py` exists and
  `build_mesh` supports both formats, but `readSMD` still calls the old readers, so
  SMD behaviour is unchanged. Nothing is broken by the half-finished state.

## Do first

1. **Commit phase 1** before touching anything else. It is verified and independent;
   leaving it entangled with unfinished SMD work is the main risk right now.

## Then, to finish phase 2

2. `read_shapes` (VTA) in `importsrc/smd.py` - **not started.** Port from `readShapes`
   (import_smd.py 924-1046). It builds a temp object, shrinkwraps it against the mesh
   and maps coordinates back; it has no DMX counterpart, so give it its own builder
   rather than forcing it through `build_mesh`.
3. Wire `readSMD` to the new readers. Order is forced: node block -> build armature ->
   `read_polys(ctx, smd, group_names)`, because `group_names` must be the armature's
   bone list. See the `importsrc/smd.py` docstring.
4. SMD needs its own `build_smd_skeleton` - `build_skeleton` is DMX-shaped (matrices
   from `DmeTransform`, `boneTransformIDs`). SMD gets rest matrices from frame 0 of the
   skeleton block via `apply_frames`, which is already shared.
5. `ImportSMD` operator (`*.smd;*.vta`), takes `import_scene.smd` from `SmdImporter`;
   add it to `SMD_MT_ImportChoice`.
6. Verify against a file imported by the old reader. `tools/dmx_import_parity.py`
   generalises to SMD with a one-line change to the operator call.

## Watch out for

- `build_mesh`'s `split_duplicate_faces` branch is **new code, never executed.** It is
  only reachable from SMD, so phase 1 did not exercise it.
- The cloth-group `loop_i` counter in `build_mesh` walks `bm.faces` and drifts if any
  face failed to build. Pre-existing bug, faithfully preserved - do not "fix" it while
  chasing an unrelated SMD problem.
- `ctx` is duck-typed across `importsrc` (`.warning`, `.error`, `.append`,
  `.existingBones`, `.appliedReferencePose`, `.qc`, `.properties`). No enforcement. If
  it gets unwieldy in phase 3, an explicit context dataclass is the fix.

## Deferred decisions

- Delete `_readDMX_legacy` + `KST_OLD_DMX_IMPORT` once cloth maps, APPEND-into-existing-
  armature, and a QC-pulling-DMX import have each been exercised. Mirrors `e8ced64`.
- DMX prefab import never increments the summary counters (see phase 1.6 note) - real
  bug, preserved, fix candidate for phase 5.
- `.vmdl_prefab` is claimed by both `ImportVMDL` (phase 4) and `ImportPrefab` (phase 5).

---

## Guiding rules

- **Old operator keeps working the whole time.** `SmdImporter` stays registered and
  handling `.smd`/`.vta`/`.qc`/`.vmdl` until each format migrates.
- **Never fork logic.** When a format moves to the new package, the old entry point
  delegates to the new module rather than keeping a copy. `readQC` calls `readDMX`
  internally, so QC-driven DMX imports exercise the new code path automatically -
  that is the parity check, and it is structural rather than a test we have to write.
- **IR is designed against SMD too, not just DMX.** Read `readNodes`/`readPolys`/
  `readShapes` while writing `records.py` so phase 2 does not have to re-litigate it.
- Behaviour frozen unless a line is marked `BEHAVIOUR CHANGE`.

## Open questions (decide before phase 1 build tasks)

- [ ] Verification: build a round-trip harness (import DMX -> export -> diff against
      original) up front, or verify by eye? Export is parity-verified now, so the
      harness is available for the first time. Affects how aggressively phase 1 moves.
Decided: package name is `importsrc/` - "import" + Source engine. Avoids the `import`
keyword clash without an ugly trailing underscore.

---

# Phase 1 - DMX (mesh + skeleton + animation)

`readDMX` is `import_smd.py:1530-2218`, ~690 lines, all nested closures. It decomposes
into seven concerns:

| Concern | Lines | Destination |
|---|---|---|
| Load, format detect, jobType sniff | 1548-1575 | `dmx.py` |
| Traversal helpers (`get_transform_matrix`, `isBone`, `enumerateBonesAndAttachments`) | 1591-1631 | `dmx.py` |
| Skeleton build - validate/append + new-armature branches | 1636-1754 | `build.py` |
| Prefab side effects (jigglebones/hitboxes/procbones) | 1756-1802 | moved as-is, see 1.6 |
| Mesh build (`parseModel`) | 1812-2095 | `build.py` |
| Flex / combinationOperator deferral | 2101-2118 | `flex.py` |
| Animation channels -> keyframes | 2123-2211 | `anim.py` |

## 1.1 Scaffold

- [x] Create `io_scene_valvesource/importsrc/` with `__init__.py` re-exporting public names
      (same pattern as `export/__init__.py`).
- [x] `records.py` - IR dataclasses: `ImportedBone`, `ImportedAttachment`,
      `ImportedSkeleton`, `ImportedLoopLayer`, `ImportedFace`, `ImportedShape`,
      `ImportedMesh`, `ImportedAnim`, `ImportedFile`.
- [x] `ImportInfo` - DMX-relevant fields only for now. SMD-only fields (`file`,
      `shapeNames`, `phantomParentIDs`, `vta_ref`) get added in phase 2.

**Finding - affects the phase 2 premise.** Having now read both readers, SMD and DMX
mesh building diverge more than assumed, so a shared `build_mesh` is not free:

| | SMD (`readPolys`) | DMX (`parseModel`) |
|---|---|---|
| Vertex groups | created for **every** bone (810-811) | only for **weighted** joints (2029) |
| Up-axis Y | `md.transform(rx90)` on mesh data (912) | `matrix_local`/`matrix_world` (1848, 2040) |
| Vertex dedup | `vertMap` cache keyed on `(co, weights)` (873) | explicit index arrays |
| Normals | flat per-loop list (910) | indexed loop layer (1876) |

The IR absorbs the third and fourth rows - SMD's parser can dedup and emit index
arrays like DMX. Rows one and two are genuine behaviour differences that phase 2 has
to reconcile deliberately, not something `build_mesh` can paper over. Do not treat
"route SMD through `build_mesh`" as mechanical.

## 1.2 Parse: DMX -> IR (`importsrc/dmx.py`)

No `bpy` calls in this module beyond `Matrix`/`Vector` math.

- [x] `load_dmx(filepath) -> ParsedDmx` - datamodel load, `format_ver`, `getDmxKeywords`,
      corrective separator detection, jobType sniff, axisSystem up-axis override.
- [x] Port `transform_matrix` / `blender_quat` / `is_bone` /
      `enumerate_bones_and_attachments` as module functions.
- [x] Mesh extraction: `read_meshes` / `_read_mesh` emit `ImportedMesh`.
- [x] Animation extraction: `read_anim` -> raw `ImportedChannel` list.

Notes from the port:

- **`ImportedAnim` holds raw channels, not KeyFrames.** Line 2186 branches on whether
  the resolved pose bone has a parent - a Blender fact the parser cannot know. KeyFrame
  assembly therefore belongs in the build half, not here.
- **`version_bumps` instead of scene writes.** `_ensureSceneDmxVersion` (1565, 1934)
  writes `scene.vs.dmx_format` mid-parse. The parser records the requested bumps and
  the caller applies them, keeping this module free of scene mutation.
- **`ImportedFace.face_set` is a face-set index, not a material slot index.**
  `getMeshMaterial` dedupes by name, so two face sets sharing a material land in one
  slot. Build must map face set -> slot; using the index directly would create
  duplicate slots. (Caught during the port - was wrong in the first draft.)
- **Dead code found:** `skipfaces` (1862) is added to at 2001 and never read. Degenerate
  faces are silently dropped either way. Not carried over; nothing changes.
- `parsed.warnings` collects parse-time warnings for the caller to emit through `Logger`.

## 1.3 Build: IR -> Blender (`importsrc/build.py`)

- [x] `build_skeleton` - both branches unified over `ImportedSkeleton`, plus
      `apply_rest_pose`.
- [x] `build_attachments`.
- [x] `build_mesh` - bmesh construction from `ImportedMesh`.
- [x] `build_shape_keys` incl. corrective drivers.
- [x] Move `applyFrames`, `createArmature`, `findArmature`, `getMeshMaterial`,
      `truncate_id_name` out of the operator into `build.py`.
- [x] `anim.py` - `build_anim` assembles KeyFrames from `ImportedChannel` and calls
      `apply_frames`.

Deviations to re-check at cutover:

- The "attachments but no skeleton" warning now names `smd.jobName` (basename without
  extension) where the original used `os.path.basename(filepath)`. Message text only.
- Original interleaved bone and attachment creation in one loop; build now does all
  bones then all attachments. Safe - parents always precede children in traversal
  order - but it is a real reordering, not a pure move.

## 1.4 Flex (`importsrc/flex.py`)

- [ ] Move `_populate_dme_flex_from_dmx` (2227).
- [ ] Keep the QC deferral seam intact (2101-2118): when invoked under a QC import,
      stash `pending_combo_op` / `flex_meshes` on the QC context instead of applying.
      Do not try to fix the `QcInfo` pending-flush design in this phase.

## 1.5 Operator split

- [x] `ImporterBase(bpy.types.Operator, Logger)` - file-browser props, the options every
      format honours (`createCollections`, `append`, `upAxis`, `rotMode`, `boneMode`),
      `execute`, `invoke`, `report_unreadable`, and all the readers. Subclasses supply
      `bl_idname`, `filter_glob`, format-specific props, and `read_file()`.
- [x] `SmdImporter(ImporterBase)` - unchanged behaviour, keeps `import_scene.smd`; owns
      `doAnim` + `makeCamera` and the extension dispatch.
- [x] `ImportDMX(ImporterBase)` - `import_scene.kst_dmx`, `filter_glob = "*.dmx"`.
      **BEHAVIOUR CHANGE**: `doAnim` and `makeCamera` are no longer shown for DMX.
- [x] `SMD_MT_ImportChoice` in `gui/menus.py`; `menu_func_import` and the sidebar
      Import button now point at it.
- [x] Translation keys: `importmenu_title`, `import_menuitem_dmx`, `importer_dmx_title`,
      `importer_dmx_tip`.

**Correction to the options table above:** `createCollections` *is* used by the DMX
path - `createCollection()` (1477) reads it and `readDMX` calls it. The table in the
phase-1 header wrongly listed it as QC-only. Only `doAnim` and `makeCamera` are
genuinely QC-only, so the dead-control cleanup is two properties, not three.

## 1.6 Prefab - carry across only

Prefab handling inside `readDMX` is **deferred to phase 5**. Do not add a toggle and do
not restructure the logic in this phase.

- [x] Moved to `importsrc/prefab.py` as `apply_dmx_prefab_data`, invoked under the same
      `if smd.a and smd.jobType != ANIM` condition.
- [x] Behaviour unchanged: still unconditional.
- Note: the DMX path prints jigglebone/hitbox/procbone counts but never increments
  `imported_jigglebones` / `imported_hitboxes` / `imported_procbones`, so they are
  missing from the final import summary. Only the QC path (1098) counts them. Looks
  like a real bug; **preserved as-is** under the behaviour-freeze rule. Fix candidate
  for phase 5.

## 1.7 Cut over

- [x] `SmdImporter.readDMX` now drives the `importsrc` pipeline. The old body survives
      as `_readDMX_legacy`, reachable by setting the `KST_OLD_DMX_IMPORT` env var
      (mirrors `KST_OLD_DMX` from the export rewrite, added in `8feff88` and deleted in
      `e8ced64` once verified).
- [x] Verified in Blender by hand - DMX import works through the new pipeline.
- [ ] Remaining edge cases worth hitting before deleting the legacy path:
      - [ ] DMX reference mesh (new armature)
      - [ ] DMX animation
      - [ ] DMX into existing armature, `append = VALIDATE`
      - [ ] DMX into existing armature, `append = APPEND`
      - [ ] DMX with attachments
      - [ ] DMX with no skeleton (mesh only)
      - [ ] DMX with cloth-enable maps
      - [ ] DMX with shape keys + correctives
      - [ ] DMX with jigglebones / hitboxes / procedural bones
      - [ ] A QC that pulls in DMX bodies (exercises the deferred-flex seam)
- [ ] Delete `_readDMX_legacy` and the env var once the matrix passes.

---

# Phase 2 - SMD / VTA

- [x] `importsrc/smd.py` - `parse_quote_blocked_line`, `scan_smd`, `read_nodes`,
      `read_frames`, `read_polys` -> IR.
- [x] The two format differences are now `ImportedMesh` fields rather than branches in
      `build_mesh`: `data_transform` (SMD corrects Y-up on mesh data, DMX on the object
      matrix) and `split_duplicate_faces` (SMD gives a duplicate face its own vertices,
      DMX drops it). The "vertex groups for every bone" difference dissolved on its own -
      `group_names` just means "groups to create", and each format fills it differently.
- [ ] `read_shapes` (VTA) - **not started.** The shrinkwrap-based vertex matching in
      `readShapes` (924-1046) has no DMX counterpart and does not fit `build_mesh`;
      it likely stays its own builder.
- [ ] Wire `readSMD` to the new path. `read_polys` needs `group_names` in armature bone
      order, so the node block must be built first - the interleaving is inherent to the
      format, see the module docstring.
- [ ] `ImportSMD` operator, `*.smd;*.vta`, takes over `import_scene.smd`.
- [ ] Verify against the same file imported by the old reader.

**Structural finding.** SMD cannot be cleanly parse-then-build the way DMX was. Weight
resolution needs the target armature: groups are created for every bone on `smd.a`
(possibly a pre-existing armature with bones the file never mentions), and triangle
weights reference node IDs that only resolve once the node block has been reconciled
against it. `importsrc/smd.py` is therefore a reader that touches Blender, not a pure
parser. The shared win is still real - mesh construction goes through `build_mesh` -
but the clean two-phase split is DMX-specific.

# Phase 3 - QC / QCI

The hard one. `readQC` is ~410 lines of recursive directive parsing that also
orchestrates child SMD/DMX imports.

- [ ] Split parse (directives -> job list) from orchestration (run the jobs).
- [ ] Format readers must stay plain callables - QC cannot invoke operators.
- [ ] Revisit `QcInfo`'s `*_pending` accumulator lists (`utils.py:1099-1105`); suspected
      latent ordering bugs in the flex flush. Cheapest to fix here.
- [ ] `ImportQC` operator, `*.qc;*.qci`.

# Phase 4 - VMDL

- [ ] Extract `_import_vmdl` (2335) and `_extract_vmdl_bones` / `_vmdl_local_matrix` /
      `_resolve_dmx_ref` out of the QC path they are currently bolted onto (1083).
- [ ] `ImportVMDL` operator, `*.vmdl;*.vmdl_prefab`.

# Phase 5 - Prefab

`ImportPrefab` is defined by what it does **not** do: it never creates an armature and
never creates a mesh. It attaches prefab data to the **active selection**. That is the
whole distinction from `ImportDMX` / `ImportQC`, which read the same bytes but build
geometry from them.

- [ ] `ImportPrefab(ImporterBase)`, `filter_glob = "*.qc;*.qci;*.vrd;*.dmx;*.vmdl_prefab"`.
- [ ] `poll()` requires an active armature. Error clearly when there is none - this is
      the operator's core precondition, not an edge case.
- [ ] Dispatch by extension to the existing `prefab_io/` readers. All six already exist
      and are re-exported from `utils.py`:
      - `.qc`/`.qci` - `import_jigglebones_from_content`, `import_hitboxes_from_content`
      - `.vrd` - `import_proc_bones_from_vrd_content` (currently only reachable from
        `readQC:1210`; this gives it a standalone entry point)
      - `.dmx` - `import_jigglebones_from_dmx_elements`, `import_hitboxes_from_dmx_root`,
        `import_proc_bones_from_dmx_elements`
      - `.vmdl_prefab` - `import_jigglebones_from_kv3`, `import_hitboxes_from_kv3`
        (in scope; overlap with `ImportVMDL`'s handling of the same extension still to
        be worked out - see phase 4)
- [ ] No options beyond `createCollections` (hitboxes use it). No `upAxis`, no `append`,
      no `rotMode`, no `boneMode` - nothing is being built.
- [ ] Once this exists, revisit whether `ImportDMX` should still import prefab data
      unconditionally (see 1.6) or gate it behind a toggle now that there is a dedicated
      path for it.

# Phase 6 - Teardown

- [ ] Delete `import_smd.py` once every format has migrated.
- [ ] Drop import-only fields from `SmdInfo` in `utils.py`.
- [ ] Update `CLAUDE.md` module layout table.
