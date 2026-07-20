# Import Rewrite - Phase Plan

Mirrors the `export/` package rewrite. Goal: split `import_smd.py` (2551 lines) into an
`importsrc/` package with a parse -> IR -> build pipeline, and replace the single
all-formats import button with one operator per format.

Order is **format by format**. DMX first. Nothing else moves until DMX lands.

---

# RESUME HERE (handoff, end of session 2)

## State

Branch `rewrite-export-smd`.

- **Phase 1 (DMX) is committed** (`Split DMX import into importsrc package`) and
  hand-verified in Blender. `KST_OLD_DMX_IMPORT=1` still falls back to `_readDMX_legacy`.
- **Phase 2 (SMD/VTA) is wired and partly verified in Blender.** `readSMD` now drives
  `importsrc`; the old `scanSMD` / `readNodes` / `readFrames` / `readPolys` /
  `readShapes` methods are deleted, so there is no fallback - if SMD import is broken,
  it is broken.

Verification so far, by hand in Blender:

- [x] Reference SMD, new armature (`build_smd_skeleton` new-armature branch)
- [x] SMD + VTA imported together in one go (`read_shapes` shrinkwrap path, and the
      selection handoff between the two files - see "Watch out for")
- [x] Up axis X and Y on the mesh (`data_transform`)
- [ ] SMD with a duplicate face (`split_duplicate_faces` - **new code, still has never
      run**; phase 1 could not reach it and none of the above hit it)
- [ ] Animation SMD into an existing armature, `append = APPEND` and `VALIDATE`
      (the validate/append branch of `build_smd_skeleton` - the runs above only
      validated against an armature the importer had just created itself)
- [ ] Physics SMD (PHYS: `show_wire`, one bone per vertex)
- [ ] A QC that pulls in SMD bodies + a VTA (`qc.ref_mesh` handoff - a different path
      from the selection scan that was fixed above)

## Then

Phase 3 (QC/QCI) - see below. Not blocked on the remaining boxes above.

While in phase 3: the legacy `applyFrames` in `import_smd.py` and `apply_frames` in
`importsrc/build.py` are now duplicate implementations, both live. The old one survives
only for `readQC`, `_readDMX_legacy` and `_import_vmdl`. They are identical today and
will drift. Phase 3 removes the last non-legacy caller, so fold them then.

## Behaviour changes made in phase 2

Both are deliberate; revert if they turn out to matter.

- `build_mesh` sets `display_type = 'SOLID'` on PHYS meshes. `readPolys` did not (only
  `getMeshMaterial` did, and only when it created a new material).
- **X up-axis now works for SMD meshes.** `readPolys` hardcoded `rx90` behind
  `if smd.upAxis == 'Y'`, so an X-up SMD imported unrotated while its bones
  (`build_smd_anim`) and any VTA (`read_shapes`) were rotated, which also made VTA
  shrinkwrap matching fail and left a stray "VTA vertices" object behind.
  `read_polys` now uses `getUpAxisMat(smd.upAxis)`, which is `rx90` for Y and identity
  for Z, so those two are bit-identical to before. Pre-existing upstream bug, not a
  rewrite regression.
- The `ImportedMesh.materials_are_paths` flag exists **because** `build_mesh` splits a
  DMX material path into `scene.vs.material_path` + basename, whereas `readPolys` used
  the whole triangle material line verbatim as the Blender material name. SMD sets the
  flag `False` to keep the old naming. In practice the SMD exporter writes a bare
  material name, so this only shows up on third-party / decompiled SMDs.

## Watch out for

- **`readSMD` must leave the built mesh active and selected.** Each file in a multi-file
  selection gets a fresh `SmdInfo`, so an SMD+VTA batch import hands the target mesh to
  `read_shapes` only through the selection. `readPolys` did this; `build_mesh` does not,
  and dropping it broke SMD+VTA-in-one-go with "no valid target object found". Note this
  also depends on the file browser handing `.smd` to us before `.vta` - true for the
  usual `name.smd` / `name.vta` pair, but nothing enforces it.

- `build_mesh`'s `split_duplicate_faces` branch is **new code, never executed.** SMD is
  the only caller that enables it, so it first runs during phase 2 verification.
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
- [x] `read_shapes` (VTA) in `importsrc/smd.py`. Kept out of `build_mesh` as predicted:
      VTA carries no topology, only positions in an id space that is not the target
      mesh's, matched by shrinkwrapping a throwaway point cloud.
- [x] `build_smd_skeleton` in `build.py`. Separate from `build_skeleton` because SMD has
      no rest matrices in the node block - they come from frame 0 of the skeleton block,
      applied through the shared `apply_frames`.
- [x] `build_smd_anim` in `anim.py`. `read_frames` returns `None` (not an empty
      `ParsedFrames`) when the block carries no pose, so VTA/PHYS skip the pose apply
      exactly as `readFrames`' early return did.
- [x] Wire `readSMD` to the new path; delete `scanSMD`/`readNodes`/`readFrames`/
      `readPolys`/`readShapes` (-405 lines).
- [x] `ImportSMD` operator, `import_scene.kst_smd`, `*.smd;*.vta`, in the import menu.
      `SmdImporter` keeps `import_scene.smd` and still handles SMD/VTA via the same
      `readSMD`, so QC-driven SMD imports exercise the new path too.
- [ ] **Verify in Blender.** See the resume block at the top - no legacy SMD path exists.

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

**Approach changed.** The original plan said "split parse from orchestration". That is
not achievable without behaviour change: directive handling is genuinely stateful and
order-dependent. `flex` / `flexpair` / `flexcontroller` / `localvar` / `%expr` are all
gated on `qc.ref_mesh`, which only exists after an earlier `$body` import ran; `$hbox`,
`$proceduralbones` and `$sequence` call `findArmature()` mid-parse; `$upaxis` writes
`scene.vs.up_axis` for every later import; `$include` recurses immediately into shared
accumulators. A "job list" would have to be re-evaluated between jobs, which is just the
interpreter again. Same finding as SMD in phase 2: `importsrc/qc.py` will be a *reader*
that touches Blender, not a pure parser.

What is worth fixing is the *lexing*, which is where the mess actually is:
`in_bodygroup` / `in_lod` / `in_sequence` booleans tracking `{` and `}` by hand across
lines, plus `num_words_to_skip` counters walking `$sequence` options.

- [x] `keyvalues1.py` - tokenizer + `Cursor` for Valve script syntax. Token stream, not
      a value tree: QC is `$directive arg arg` with positional args, so a KV tree would
      wrap every directive in a synthetic node. Shape follows PulseMDL's `qcloader.cpp`
      (`Tokenize` -> `Cur`/`Next`/`Eof` cursor, braces consumed inline). No `bpy` import,
      so it is testable outside Blender - 20 unit checks plus a tokenize-everything pass
      over the BlenderSourceTools and PulseMDL sample QCs (all balance to depth 0).
- [x] `importsrc/flexdata.py` (phase 1.4) - `parse_flex_text`,
      `apply_flex_text_to_object`, `set_flexgroup_from_qc`, `populate_dme_flex_from_dmx`.
      Named flexdata, not flex, to stay distinct from the add-on's top-level `flex.py`.
      The originals are deleted from `import_smd.py`; `_populate_dme_flex_from_dmx` is now
      a one-line delegate, and `gui/operators.py` imports from the new home.
- [x] `importsrc/qc.py` - dispatch rebuilt on `Cursor.block()`. The in_bodygroup / in_lod
      / in_sequence booleans and `num_words_to_skip` are gone; `$lod`, `$bodygroup` and
      braced `$sequence` read their bodies through `block()`. Raw-line handlers
      (`localvar`, `%expr`, `$hbox`, `$definemacro`) reach back via `Token.line`.
      `$var$` substitution and the lowercase + `/`->`\` normalisation sit in
      `_normalise_word`, above the lexer.
- [x] `ImportQC` operator, `import_scene.kst_qc`, `*.qc;*.qci`, in the import menu.
- [x] Old body kept as `_readQC_legacy` behind `KST_OLD_QC` (its `$include` recursion
      rewired to stay on the legacy path). Do not delete until verified - phase 2 deleted
      the SMD fallback and then hit two bugs with nothing to diff against.
- [ ] **Verify in Blender.** Untested: a QC with `$body` + `$sequence`, a braced
      `$bodygroup`, `$lod` / `replacemodel`, `flexfile` + `flexcontroller` accumulation,
      `$include` into a QCI, `$hbox`, `$proceduralbones`, `$origin`.

Checked without Blender: `scratchpad/qcwalk.py` replays the new token dispatch over the
BlenderSourceTools and PulseMDL sample QCs and prints what each `$body` / `$bodygroup` /
`$lod` / `$sequence` / `$collisionmodel` / `flexfile` would import. The BST QCs resolve to
their real files only. It caught one regression (a dropped `xfade` option, restored).

## VTA matching now spans every reference mesh (fixes a long-standing bug)

`readShapes` matched the VTA against a single mesh - `qc.ref_mesh`, i.e. whichever REF
happened to be imported last - and accepted any shrinkwrap snap. A decompiled VTA is
indexed against the *whole model*: its base frame lists every vertex while the deltas
stay sparse. So on any multi-bodygroup model most of the base frame had no home, the
"VTA vertices" error object survived, and vertices were silently snapped onto whichever
face vertex happened to be nearest.

`read_shapes` now matches the base frame against every mesh in `qc.ref_meshes` (new,
appended per REF import) and assigns each vertex to the mesh with the smallest snap
distance, then applies each delta to its owning mesh, creating shape keys lazily so a
frame only touches the meshes it moves.

Verified offline against `koleda_dorm` (7 bodygroups, 56554-vertex base frame):

    unmatched by ANY mesh: 3 of 56554        (was ~53k against facesrc alone)
    delta ids: 4230 -> facesrc 3994, facegf2 236

Known limits:
- 6374 positions in that model exist in more than one mesh. Ties go to the mesh checked
  first (import order) because the update test is `dist >= best_dist`. Harmless there -
  the deltas are all face vertices - but it is the ambiguity this approach cannot resolve
  from coordinates alone.
- `_MATCH_TOLERANCE` (0.01) is the snap distance above which a vertex is called
  unmatched. The original had no threshold, so a vertex that is genuinely far from every
  mesh is now reported instead of silently mismapped.
- A mesh referenced only by `$lod` `replacemodel` is never imported at LOD0, so deltas
  owned by it (facegf2's 236 here) are still dropped.

## VRD proc bones resolve across a stripped namespace (fixes a long-standing bug)

Crowbar writes VRD `<helper>` / `<aimconstraint>` bone names with the namespace stripped
(`Bip01_Pelvis`) while the armature keeps the full name (`ValveBiped.Bip01_Pelvis`), so
`_bone_resolver` - exact, then export-name, then lowercase - missed nearly everything and
`$proceduralbones` reported almost all entries as missing bones.

`prefab_io/proceduralbone.py:_bone_resolver` now also matches namespace-stripped, in both
directions, using `utils.get_preserved_bone_prefixes()`. That list is `ValveBiped.` plus
whatever the user registered in add-on preferences, so a custom rig namespace works too.
Both callers benefit: the VRD reader and the DME proc-bone reader.

Measured on `koleda_dorm.vrd` (32 `<helper>` blocks, 151-bone armature):

    before: 6 of 44 distinct names resolved  (38 reported missing)
    after: 44 of 44, i.e. 96/96 name slots across all blocks

Not a general problem: QC `$hbox` and `$jigglebone` carry full `ValveBiped.` names, so
hitbox and jigglebone import were never affected. VRD is the only format that strips.

## Pre-existing bug this surfaced - decide before phase 6

The `$sequence` option table is incomplete, in the original as much as the port: `fps`,
`loop`, `weightlist`, `subtract`, `iklock`, `ikrule` are not listed. In an *inline*
`$sequence idle anims/idle fps 30 loop` this is harmless - the file is found first and
the rest of the line is dropped. In a *braced* sequence each line is scanned
independently, so `fps 30` on its own line makes `fps` look like an animation filename
and the importer tries to load `fps.smd` / `fps.dmx` and errors.

PulseMDL-style QCs use the braced form heavily, so this is likely to bite. Preserved
as-is under the behaviour freeze rather than fixed silently: adding the keywords changes
which files get imported, and an animation legitimately named `loop` would change
meaning. Worth a deliberate decision.
- [ ] Format readers must stay plain callables - QC cannot invoke operators.
- [ ] Revisit `QcInfo`'s `*_pending` accumulator lists (`utils.py:1099-1105`); suspected
      latent ordering bugs in the flex flush. Cheapest to fix here.
- [ ] `ImportQC` operator, `*.qc;*.qci`.

# Phase 4 - VMDL

- [x] `importsrc/vmdl.py` - `read_vmdl` plus `local_matrix` / `extract_bones` /
      `resolve_dmx_ref`, lifted out of the QC path they were bolted onto. The 219-line
      `_import_vmdl` is split into `_build_skeleton` / `_read_render_meshes` /
      `_read_attachments` / `_read_animations`. No lexer work needed - VMDL is KV3, so
      `keyvalues3.py` already parses it and this module is extraction plus orchestration.
- [x] `ImportVMDL` operator, `import_scene.kst_vmdl`, `*.vmdl;*.vmdl_prefab`.
      It routes through `readQC` because `read_vmdl` needs the `QcInfo` that `read_qc`
      builds (job name, up axis, `imported_smds`); `read_qc` then dispatches by extension.
- [x] The old methods are deleted from `import_smd.py` (-258 lines, now 1873). The
      `_readQC_legacy` call site was repointed at `importsrc.read_vmdl` rather than left
      dangling - there is only one VMDL implementation now, no legacy copy.
- [ ] **Verify in Blender.** Untested: a VMDL with a Skeleton + RenderMeshList, an
      AttachmentList, jigglebones/hitboxes, and an AnimationList with nested Folders.

**`.vmdl_prefab` ownership (was an open question).** Both `ImportVMDL` and phase 5's
`ImportPrefab` accept the extension, and that is fine: the user picks the operator, so
the extension is not owned - the *intent* differs. `ImportVMDL` builds a skeleton and
pulls in referenced geometry; `ImportPrefab` will only attach prefab data to an existing
armature. A `.vmdl_prefab` with no Skeleton already takes the jigglebone-only path in
`read_vmdl`, which is the overlap in practice.

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
