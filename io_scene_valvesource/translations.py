#coding=utf-8
_languages = ['ja']

_data = {
    'action_filter': {
        'en': "Action Filter",
        'ja': "アクションフィルター",
    },
    'action_selection_filter_tip': {
        'en': "All actions that match the armature's filter term and have users",
        'ja': "アーマチュアのフィルターに一致するすべてのアクション",
    },
    'action_selection_mode': {
        'en': "Action Selection",
        'ja': "アクション選択",
    },
    'action_selection_mode_tip': {
        'en': "How actions are selected for export",
        'ja': "アクションのエクスポート選択方法",
    },
    'action_slot_current': {
        'en': "Current Action Slot",
        'ja': "現在のアクションスロット",
    },
    'action_slot_selection_current_tip': {
        'en': "The armature's active action slot",
        'ja': "アーマチュアのアクティブなアクションスロット",
    },
    'activate_dep_shapes': {
        'en': "Activate Dependency Shapes",
        'ja': "依存シェイプを有効化",
    },
    'activate_dep_shapes_success': {
        'en': "Activated {0} dependency shapes",
        'ja': "{0}個の依存シェイプを有効化しました",
    },
    'activate_dep_shapes_tip': {
        'en': "Activates shapes found in the name of the current shape (underscore delimited)",
        'ja': "現在のシェイプ名からアンダースコア区切りで依存シェイプを有効化",
    },
    'activate_dependency_shapes': {
        'en': "Activate dependency shapes",
        'ja': "依存シェイプを有効化",
    },
    'active_exportable': {
        'en': "Active exportable",
        'ja': "アクティブ・エクスポート可能",
    },
    'apply_drivers': {
        'en': "Regenerate Shape Key Names From Drivers",
        'ja': "ドライバーからシェイプキー名を再生成",
    },
    'apply_drivers_success': {
        'en': "{0} shapes renamed.",
        'ja': "{0}個のシェイプを名前変更しました。",
    },
    'apply_drivers_tip': {
        'en': "Renames corrective shape keys so that each their names are a combination of the shape keys that control them (via Blender animation drivers)",
        'ja': "ドライバーの組み合わせから是正シェイプキーを名前変更",
    },
    'bake_shapekey_as_basis_normals': {
        'en': "Use Basis Normals For Shapekeys",
        'ja': "シェイプキーにベースの法線を使用",
    },
    'bake_shapekey_as_basis_normals_tip': {
        'en': "Preserve the basis shape normals when exporting, applying them to all shapekeys (useful for anime-style models)",
        'ja': "すべてのシェイプキーにベースシェイプの法線を保持（アニメスタイル向け）",
    },
    'controller_source': {
        'en': "DMX Flex Controller source",
        'ja': "DMXフレックスコントローラーのソース",
    },
    'controllers_advanced_tip': {
        'en': "Insert the flex controllers of an existing DMX file",
        'ja': "既存のDMXファイルからフレックスコントローラーを挿入",
    },
    'controllers_mode': {
        'en': "DMX Flex Controller generation",
        'ja': "DMXフレックスのコントローラー生成",
    },
    'controllers_mode_tip': {
        'en': "How flex controllers are defined",
        'ja': "フレックスコントローラーの定義方法",
    },
    'controllers_simple_tip': {
        'en': "Generate one flex controller per shape key",
        'ja': "シェイプキーごとに1つのフレックスコントローラーを生成",
    },
    'controllers_source_tip': {
        'en': "A DMX file (or Text datablock) containing flex controllers",
        'ja': "フレックスコントローラーを含むDMXファイルまたはテキストブロック",
    },
    'controllers_dme_tip': {
        'en': "Define flex controllers and flex rules following the DMX model spec (DMX export only)",
        'ja': "DMXモデル仕様に従いフレックスコントローラーとルールを定義（DMXエクスポート専用）",
    },
    'controllers_builder_tip': {
        'en': "Only shapekeys explicitly listed as flex controllers will be exported. All other shapekeys are ignored.",
        'ja': "明示的にリストされたシェイプキーのみエクスポート",
    },
    'curve_poly_side': {
        'en': "Polygon Generation",
        'ja': "ポリゴン生成",
    },
    'curve_poly_side_back': {
        'en': "Backward (inner) side",
        'ja': "背面（内側）",
    },
    'curve_poly_side_both': {
        'en': "Both sides",
        'ja': "両面",
    },
    'curve_poly_side_fwd': {
        'en': "Forward (outer) side",
        'ja': "前面（外側）",
    },
    'curve_poly_side_tip': {
        'en': "Determines which side(s) of this curve will generate polygons when exported",
        'ja': "カーブのどちら側にポリゴンを生成するか",
    },
    'dmx_encoding': {
        'en': "DMX encoding",
        'ja': "DMXの符号化",
    },
    'dmx_encoding_tip': {
        'en': "Manual override for binary DMX encoding version",
        'ja': "バイナリDMXエンコーディングバージョンの手動上書き",
    },
    'dmx_format': {
        'en': "DMX format",
        'ja': "DMXのフォーマット",
    },
    'dmx_format_tip': {
        'en': "Manual override for DMX model format version",
        'ja': "DMXモデルフォーマットバージョンの手動上書き",
    },
    'dmx_mat_path': {
        'en': "Material Path",
        'ja': "マテリアルのパス",
    },
    'dmx_mat_path_tip': {
        'en': "Folder relative to game root containing VMTs referenced in this scene (DMX only)",
        'ja': "VMTを含むゲームルートからの相対フォルダー（DMXのみ）",
    },
    'dummy_bone': {
        'en': "Implicit motionless bone",
        'ja': "暗黙の静止ボーン",
    },
    'dummy_bone_tip': {
        'en': "Create a dummy bone for vertices which don't move. Emulates Blender's behaviour in Source, but may break compatibility with existing files (SMD and Source 1 DMX)",
        'ja': "動かない頂点用のダミーボーンを作成（SMDおよびSource 1 DMX）",
    },
    'engine_path': {
        'en': "Engine Path",
        'ja': "エンジンのパス",
    },
    'engine_path_tip': {
        'en': "Directory containing studiomdl (Source 1) or resourcecompiler (Source 2)",
        'ja': "studiomdlまたはresourcecompilerを含むディレクトリ",
    },
    'export_format': {
        'en': "Export Format",
        'ja': "エクスポートのフォーマット",
    },
    'export_format_tip': {
        'en': "File format written by the scene exporter",
        'ja': "シーンエクスポーターが書き込むファイル形式",
    },
    'export_menuitem': {
        'en': "Source Engine (.smd, .vta, .dmx)",
        'ja': "Source Engine (.smd, .vta, .dmx)",
    },
    'exportables_arm_filter_result': {
        'en': "\"{0}\" actions ({1})",
        'ja': "「{0}」アクション ({1})",
    },
    'exportables_arm_no_slot_filter': {
        'en': "All action slots ({0}) for \"{1}\"",
        'ja': "「{1}」のすべてのアクションスロット（{0}）",
    },
    'exportables_flex_count': {
        'en': "Shapes: {0}",
        'ja': "シェイプ：{0}",
    },
    'exportables_flex_count_corrective': {
        'en': "Corrective Shapes: {0}",
        'ja': "是正シェイプ：{0}",
    },
    'exportables_flex_generate': {
        'en': "Generate Controllers",
        'ja': "コントローラーを生成します",
    },
    'exportables_flex_help': {
        'en': "Flex Controller Help",
        'ja': "フレックスコントローラーのヘルプ",
    },
    'exportables_flex_split': {
        'en': "Stereo Flex Balance:",
        'ja': "ステレオフレックスバランス：",
    },
    'exportables_flex_src': {
        'en': "Controller Source",
        'ja': "コントローラーのソースファイル",
    },
    'exportables_group_bypass_suffix': {
        'en': "(bypassed)",
        'ja': "(バイパス)",
    },
    'exportables_group_bypass_hint': {
        'en': "Bypassed - objects are listed under the parent group",
    },
    'exportables_group_mute_suffix': {
        'en': "(suppressed)",
        'ja': "(ミュート)",
    },
    'exportables_prefab_row': {
        'en': "{0} ({1})",
        'ja': "{0} ({1})",
    },
    'exportables_title': {
        'en': "Source Engine Exportables",
        'ja': "Source Engineのエクスポート可能",
    },
    'exporter_err_arm_nonuniform': {
        'en': "Armature \"{0}\" has non-uniform scale. Mesh deformation in Source will differ from Blender.",
        'ja': "アーマチュア「{0}」のスケールが均一ではありません",
    },
    'exporter_err_bonelimit': {
        'en': "Exported {0} bones, but SMD only supports {1}!",
        'ja': "{0}ボーンをエクスポートしましたが、SMDは{1}ボーンまでしかサポートしていません",
    },
    'exporter_warn_procbone_no_action': {
        'en': "Procedural bone '{0}' has no action; keeping it a plain joint.",
    },
    'exporter_warn_procbone_no_driver': {
        'en': "Procedural bone '{0}' has no exportable driver bone; keeping it a plain joint.",
    },
    'exporter_warn_procbone_no_triggers': {
        'en': "Procedural bone '{0}' produced no triggers; keeping it a plain joint.",
    },
    'exporter_warn_procbone_no_target': {
        'en': "Procedural LookAt bone '{0}' has no target attachment; keeping it a plain joint.",
    },
    'exporter_warn_procbone_too_many': {
        'en': "Procedural bone '{0}' has {1} triggers; Source clamps to 32.",
    },
    'exporter_warn_procbone_duplicate': {
        'en': "Duplicate procedural entry for helper bone '{0}'; only the first is exported.",
    },
    'exporter_warn_procbone_jiggle_conflict': {
        'en': "Bone '{0}' is both a jigglebone and a procedural helper; keeping the jigglebone and skipping the procedural entry.",
    },
    'exporter_err_dme_corrective_no_components': {
        'en': "'{0}': CORRECTIVE rule has no components",
    },
    'exporter_err_dme_corrective_unknown_component': {
        'en': "'{0}': CORRECTIVE component '{1}' is not a known shape key",
    },
    'exporter_err_dme_domination_no_dominators': {
        'en': "'{0}': DOMINATION rule has no dominators",
    },
    'exporter_err_dme_domination_no_suppressed': {
        'en': "'{0}': DOMINATION rule has no suppressed names",
    },
    'exporter_err_dme_expression_no_name': {
        'en': "'{0}': EXPRESSION rule has no name",
    },
    'exporter_err_dme_expression_unknown_ctrl': {
        'en': "'{0}': expression '{1}' is not a known controller",
    },
    'exporter_err_dme_expression_unknown_delta': {
        'en': "'{0}': expression '%{1}' is not a known shape key or local var",
    },
    'exporter_err_dme_expression_unknown_target': {
        'en': "'{0}': EXPRESSION target '{1}' is not a shape key or local var",
    },
    'exporter_err_dme_localvar_no_name': {
        'en': "'{0}': LOCALVAR rule has no name",
    },
    'exporter_err_dme_no_ctrl_name': {
        'en': "'{0}': flex controller has no name",
    },
    'exporter_err_dme_passthrough_no_name': {
        'en': "'{0}': PASSTHROUGH rule has no name",
    },
    'exporter_err_dme_passthrough_unknown': {
        'en': "'{0}': PASSTHROUGH '{1}' is not a known controller",
    },
    'exporter_err_dmxother': {
        'en': "Cannot export DMX. Resolve errors with the SOURCE ENGINE EXPORT panel in SCENE PROPERTIES.",
        'ja': "DMXをエクスポートできません。SCENE PROPERTIESのSOURCE ENGINE EXPORTパネルでエラーを解決してください",
    },
    'exporter_err_dupeenv_arm': {
        'en': "Armature modifier \"{0}\" found on \"{1}\", which already has a bone parent or constraint. Ignoring.",
        'ja': "「{1}」にアーマチュアモディファイアー「{0}」が見つかりましたが、既にボーン親または制約があります。無視します",
    },
    'exporter_err_dupeenv_con': {
        'en': "Bone constraint \"{0}\" found on \"{1}\", which already has a bone parent. Ignoring.",
        'ja': "「{1}」にボーン制約「{0}」が見つかりましたが、既にボーン親があります。無視します",
    },
    'exporter_err_facesnotex_ormat': {
        'en': "{0} faces on {1} did not have a Material or Texture assigned",
        'ja': "{1}の{0}面にマテリアルまたはテクスチャが割り当てられていません",
    },
    'exporter_err_flexctrl_loadfail': {
        'en': "Could not load flex controllers. Python reports: {0}",
        'ja': "フレックスコントローラーを読み込めませんでした。Pythonの報告: {0}",
    },
    'exporter_err_flexctrl_missing': {
        'en': "No flex controller defined for shape {0}.",
        'ja': "シェイプ{0}のフレックスコントローラーが定義されていません",
    },
    'exporter_err_flexctrl_undefined': {
        'en': "Could not find flex controllers for \"{0}\"",
        'ja': "「{0}」のフレックスコントローラーが見つかりません",
    },
    'exporter_err_groupempty': {
        'en': "Group {0} has no active objects",
        'ja': "グループ{0}にアクティブなオブジェクトがありません",
    },
    'exporter_err_groupbypassed': {
        'en': "Group {0} is bypassed into its parent group",
        'ja': "グループ「{0}」は親グループにバイパスされています",
    },
    'exporter_err_groupmuted': {
        'en': "Group {0} is suppressed",
        'ja': "グループ「{0}」はミュートです",
    },
    'exporter_err_hidden': {
        'en': "Skipping {0}: object cannot be selected, probably due to being hidden by an animation driver.",
        'ja': "{0}をスキップ: おそらくアニメーションドライバーにより非表示です",
    },
    'exporter_err_makedirs': {
        'en': "Could not create export folder. Python reports: {0}",
        'ja': "エクスポートフォルダーを作成できませんでした。Pythonの報告: {0}",
    },
    'exporter_err_missing_corrective_target': {
        'en': "Found corrective shape key \"{0}\", but not target shape \"{1}\"",
        'ja': "是正シェイプキー「{0}」が見つかりましたが、ターゲットシェイプ「{1}」が見つかりません",
    },
    'exporter_err_nogroupitems': {
        'en': "Nothing in Group \"{0}\" is enabled for export",
        'ja': "グループ「{0}」でエクスポートが有効なオブジェクトがありません",
    },
    'exporter_err_nopolys': {
        'en': "Object {0} has no polygons, skipping",
        'ja': "オブジェクト{0}にポリゴンがありません。スキップします",
    },
    'exporter_err_open': {
        'en': "Could not create {0} file. Python reports: {1}.",
        'ja': "{0}ファイルを作成できませんでした。Pythonの報告: {1}",
    },
    'exporter_err_relativeunsaved': {
        'en': "Cannot export to a relative path until the blend file has been saved.",
        'ja': "blendファイルが保存されるまで相対パスにエクスポートできません",
    },
    'exporter_err_shapes_decimate': {
        'en': "Cannot export shape keys from \"{0}\" because it has a '{1}' Decimate modifier. Only Un-Subdivide mode is supported.",
        'ja': "「{0}」に'{1}'デシメートモディファイアーがあるためシェイプキーをエクスポートできません",
    },
    'exporter_err_solidifyinside': {
        'en': "Curve {0} has the Solidify modifier with rim fill, but is still exporting polys on both sides.",
        'ja': "カーブ{0}はリムフィル付きSolidifyモディファイアーがありますが、両面でポリゴンをエクスポートしています",
    },
    'exporter_err_unconfigured': {
        'en': "Scene unconfigured. See the SOURCE ENGINE EXPORT panel in SCENE PROPERTIES.",
        'ja': "シーンが未設定です。SCENE PROPERTIESのSOURCE ENGINE EXPORTパネルを確認してください",
    },
    'exporter_err_unmergable': {
        'en': "Skipping vertex animations on Group \"{0}\", which could not be merged into a single DMX object due to its envelope. To fix this, either ensure that the entire Group has the same bone parent or remove all envelopes.",
        'ja': "グループ「{0}」の頂点アニメーションをスキップします",
    },
    'exporter_prop_group': {
        'en': "Group Name",
        'ja': "グループの名前",
    },
    'exporter_prop_group_tip': {
        'en': "Name of the Group to export",
        'ja': "エクスポートするグループの名前",
    },
    'exporter_prop_scene_tip': {
        'en': "Export all items selected in the Source Engine Exportables panel",
        'ja': "Source Engine Exportablesパネルで選択されたすべてのアイテムをエクスポート",
    },
    'exporter_report': {
        'en': "{0} files exported in {1} seconds",
        'ja': "{0}個のファイルを{1}秒でエクスポートしました",
    },
    'exporter_report_menu': {
        'en': "Source Tools Error Report",
        'ja': "Source Tools エラーレポート",
    },
    'exporter_report_suffix': {
        'en': " with {0} Errors and {1} Warnings",
        'ja': "エラー{0}個・警告{1}個",
    },
    'exporter_tip': {
        'en': "Export and compile Source Engine models",
        'ja': "Source Engineモデルをエクスポートおよびコンパイル",
    },
    'exporter_title': {
        'en': "Export SMD/VTA/DMX",
        'ja': "SMD/VTA/DMXをエクスポート",
    },
    'exporter_warn_correctiveshape_duplicate': {
        'en': "Corrective shape key \"{0}\" has the same activation conditions ({1}) as \"{2}\". Skipping.",
        'ja': "是正シェイプキー「{0}」は「{2}」と同じ有効化条件({1})です。スキップします",
    },
    'exporter_warn_multiarmature': {
        'en': "Multiple armatures detected",
        'ja': "複数のアーマチュアが検出されました",
    },
    'exporter_warn_unkeyframed_pose': {
        'en': "Animation \"{0}\": {1} posed but un-keyframed bone(s) will be reset to rest ({2}). Keyframe them, or disable 'Reset Pose Per Anim' to keep the pose.",
        'ja': "アニメーション「{0}」: ポーズ済みだがキーフレーム未設定のボーン{1}個がレストに戻されます（{2}）。キーフレームを設定するか、'Reset Pose Per Anim' を無効にしてポーズを保持してください。",
    },
    'exporter_warn_sanitised_filename': {
        'en': "Sanitised exportable name \"{0}\" to \"{1}\"",
        'ja': "エクスポート名「{0}」を「{1}」にサニタイズしました",
    },
    'exporter_warn_source2names': {
        'en': "Consider renaming \"{0}\": in Source 2, model names can contain only lower-case characters, digits, and/or underscores.",
        'ja': "「{0}」の名前変更を検討してください: Source 2では英小文字・数字・アンダースコアのみ使用可",
    },
    'exporter_warn_source2smdsupport': {
        'en': "Source 2 no longer supports SMD.",
        'ja': "Source 2はSMDをサポートしていません",
    },
    'exporter_warn_unicode': {
        'en': "Name of {0} \"{1}\" contains Unicode characters. This may not compile correctly!",
        'ja': "{0}「{1}」の名前はUnicode文字を含んでいます。正常にコンパイルされない可能性があります。",
    },
    'exporter_warn_weightlinks_excess': {
        'en': "{0} verts on \"{1}\" have over {2} weight links. Source does not support this!",
        'ja': "「{1}」の{0}頂点が{2}個を超えるウェイトリンクを持っています",
    },
    'exporterr_goldsrc_multiweights': {
        'en': "{0} verts on \"{1}\" have multiple weight links. GoldSrc does not support this!",
        'ja': "「{1}」の{0}頂点に複数のウェイトリンクがあります。GoldSrcはサポートしていません",
    },
    'exportmenu_invalid': {
        'en': "Cannot export selection",
        'ja': "選択をエクスポートできません",
    },
    'exportmenu_scene': {
        'en': "Scene export ({0} files)",
        'ja': "シーンをエクスポート ({0}ファイル)",
    },
    'exportmenu_selected': {
        'en': "Selected objects ({0} files)",
        'ja': "選択オブジェクト（{0}ファイル）",
    },
    'exportmenu_title': {
        'en': "Source Tools Export",
        'ja': "Source Tools エクスポート",
    },
    'exportname': {
        'en': "Export Name",
        'ja': "エクスポート名",
    },
    'exportname_tip': {
        'en': "Override the bone name written to exported files",
        'ja': "エクスポートされるファイルに書き込まれるボーン名を上書き",
    },
    'exportpanel_dmxver': {
        'en': "DMX Version:",
        'ja': "DMXのバージョン：",
    },
    'exportpanel_title': {
        'en': "Source Engine Export",
        'ja': "Source Engine エクスポート",
    },
    'exportroot': {
        'en': "Export Path",
        'ja': "エクスポートのパス",
    },
    'exportroot_tip': {
        'en': "The root folder into which SMD and DMX exports from this scene are written",
        'ja': "このシーンのSMD/DMXエクスポートのルートフォルダー",
    },
    'forward_axis': {
        'en': "Target Forward Axis",
        'ja': "ターゲット前方軸",
    },
    'game_path': {
        'en': "Game Path",
        'ja': "ゲームのパス",
    },
    'game_path_tip': {
        'en': "Directory containing gameinfo.txt (if unset, the system VPROJECT will be used)",
        'ja': "gameinfo.txtを含むディレクトリ",
    },
    'gen_block': {
        'en': "Generate DMX Flex Controller block",
        'ja': "DMXフレックスのコントローラーの抜粋を生成します",
    },
    'gen_block_success': {
        'en': "DMX written to text block \"{0}\"",
        'ja': "DMXテキストブロック「{0}」に書き込みました",
    },
    'gen_block_tip': {
        'en': "Generate a simple Flex Controller DMX block",
        'ja': "シンプルなフレックスコントローラーDMXブロックを生成",
    },
    'gen_drivers': {
        'en': "Generate Corrective Shape Key Drivers",
        'ja': "是正シェイプキーのドライバーを生成します",
    },
    'gen_drivers_tip': {
        'en': "Adds Blender animation drivers to corrective Source engine shapes",
        'ja': "Source Engineの是正シェイプキーにドライバーを追加",
    },
    'group_bypass': {
        'en': "Bypass",
        'ja': "バイパス",
    },
    'group_bypass_tip': {
        'en': "Fold this nested group's objects into its parent group instead of exporting it as a separate exportable. Has no effect on top-level groups",
        'ja': "このネストされたグループを個別のエクスポート対象にせず、親グループにオブジェクトを統合します。トップレベルのグループには影響しません",
    },
    'group_merge_mech': {
        'en': "Merge mechanical parts",
        'ja': "メカニカルなパーツを結合",
    },
    'group_merge_mech_tip': {
        'en': "Optimises DMX export of meshes sharing the same parent bone",
        'ja': "同じ親ボーンを持つメッシュのDMXエクスポートを最適化",
    },
    'group_suppress': {
        'en': "Suppress",
        'ja': "ミュート",
    },
    'group_suppress_tip': {
        'en': "Export this group's objects individually",
        'ja': "このグループのオブジェクトを個別にエクスポート",
    },
    'help': {
        'en': "Help",
        'ja': "ヘルプ",
    },
    'ignore_bone_exportnames': {
        'en': "Ignore Bone Export Names",
        'ja': "ボーンエクスポート名を無視",
    },
    'ignore_bone_exportnames_tip': {
        'en': "Export bones using their Blender names, ignoring any export name overrides",
        'ja': "エクスポート名の上書きを無視し、Blenderのボーン名を使用",
    },
    'import_menuitem': {
        'en': "Source Engine (.smd, .vta, .dmx, .qc, .qci)",
        'ja': "Source Engine (.smd, .vta, .dmx, .qc, .qci)",
    },
    'importer_balance_group': {
        'en': "DMX Stereo Balance",
        'ja': "DMXステレオバランス",
    },
    'importer_bone_parent_miss': {
        'en': "Parent mismatch for bone \"{0}\": \"{1}\" in Blender, \"{2}\" in {3}.",
        'ja': "ボーン「{0}」の親が一致しません: Blenderでは「{1}」、{3}では「{2}」",
    },
    'importer_bonemode': {
        'en': "Bone shapes",
        'ja': "ボーンカスタムシェイプ",
    },
    'importer_bonemode_tip': {
        'en': "How bones in new Armatures should be displayed",
        'ja': "新しいアーマチュアでのボーンの表示方法",
    },
    'importer_bones_append': {
        'en': "Append to Target",
        'ja': "ターゲットに追加",
    },
    'importer_bones_append_desc': {
        'en': "Add new bones to the target Armature",
        'ja': "ターゲットアーマチュアに新しいボーンを追加",
    },
    'importer_bones_mode': {
        'en': "Bone Append Mode",
        'ja': "ボーン追加モード",
    },
    'importer_bones_mode_desc': {
        'en': "How to behave when a reference mesh import introduces new bones to the target Armature (ignored for QCs)",
        'ja': "参照メッシュのインポート時に新しいボーンが追加された場合の動作",
    },
    'importer_bones_newarm': {
        'en': "Make New Armature",
        'ja': "アーマチュアを生成",
    },
    'importer_bones_newarm_desc': {
        'en': "Make a new Armature for this import",
        'ja': "このインポート用に新しいアーマチュアを作成",
    },
    'importer_bones_validate': {
        'en': "Validate Against Target",
        'ja': "ターゲットと照合",
    },
    'importer_bones_validate_desc': {
        'en': "Report new bones as missing without making any changes to the target Armature",
        'ja': "変更せずにターゲットアーマチュアで不足ボーンを報告",
    },
    'importer_complete': {
        'en': "Imported {0} files in {1} seconds",
        'ja': "{0}ファイルを{1}秒でインポートしました",
    },
    'importer_doanims': {
        'en': "Import Animations",
        'ja': "アニメーションをインポート",
    },
    'importer_err_badfile': {
        'en': "Format of {0} not recognised",
        'ja': "{0}のフォーマットが認識されません",
    },
    'importer_err_badweights': {
        'en': "{0} vertices weighted to invalid bones on {1}",
        'ja': "{1}の{0}頂点が無効なボーンにウェイト付けされています",
    },
    'importer_err_bonelimit_smd': {
        'en': "SMD only supports 128 bones!",
        'ja': "SMDは128ボーンまでしかサポートしていません",
    },
    'importer_err_missingbones': {
        'en': "{0} contains {1} bones not present in {2}. Check the console for a list.",
        'ja': "{0}に{2}にないボーンが{1}個含まれています。詳細はコンソールを確認",
    },
    'importer_err_namelength': {
        'en': "{0} name \"{1}\" is too long to import. Truncating to \"{2}\"",
        'ja': "{0}の名前「{1}」はインポートには長すぎます。「{2}」に切り詰めます",
    },
    'importer_err_noanimationbones': {
        'en': "No bones imported for animation {0}",
        'ja': "アニメーション{0}のボーンがインポートされていません",
    },
    'importer_err_nofile': {
        'en': "No file selected",
        'ja': "選択ファイルはありません",
    },
    'importer_err_qci': {
        'en': "Could not open QC $include file \"{0}\" - skipping!",
        'ja': "QC $includeファイル「{0}」を開けませんでした - スキップします",
    },
    'importer_err_refanim': {
        'en': "Found animation in reference mesh \"{0}\", ignoring!",
        'ja': "参照メッシュ「{0}」にアニメーションが見つかりました。無視します",
    },
    'importer_err_shapetarget': {
        'en': "Could not import shape keys: no valid target object found",
        'ja': "シェイプキーをインポートできません: 有効なターゲットオブジェクトが見つかりません",
    },
    'importer_err_smd': {
        'en': "Could not open SMD file \"{0}\": {1}",
        'ja': "SMDファイル「{0}」を開けませんでした: {1}",
    },
    'importer_err_smd_ver': {
        'en': "Unrecognised/invalid SMD file. Import will proceed, but may fail!",
        'ja': "認識できない/無効なSMDファイルです。インポートは続行しますが失敗する可能性があります",
    },
    'importer_err_unmatched_mesh': {
        'en': "{0} VTA vertices ({1}%) were not matched to a mesh vertex! An object with a vertex group has been created to show where the VTA file's vertices are.",
        'ja': "{0}個のVTA頂点（{1}%）がメッシュ頂点と一致しませんでした",
    },
    'importer_makecamera': {
        'en': "Make Camera At $origin",
        'ja': "$originにカメラを生成",
    },
    'importer_makecamera_tip': {
        'en': "For use in viewmodel editing; if not set, an Empty will be created instead",
        'ja': "ビューモデル編集用; 未設定の場合はEmptyが作成されます",
    },
    'importer_name_nomat': {
        'en': "UndefinedMaterial",
        'ja': "UndefinedMaterial",
    },
    'importer_name_unmatchedvta': {
        'en': "Unmatched VTA",
        'ja': "未一致のVTA",
    },
    'importer_qc_macroskip': {
        'en': "Skipping macro in QC {0}",
        'ja': "QC {0}のマクロをスキップします",
    },
    'importer_rotmode': {
        'en': "Rotation mode",
        'ja': "回転モード",
    },
    'importer_rotmode_tip': {
        'en': "Determines the type of rotation Keyframes created when importing bones or animation",
        'ja': "ボーンまたはアニメーションのインポート時に作成されるキーフレームの回転タイプ",
    },
    'importer_tip': {
        'en': "Imports uncompiled Source Engine model data",
        'ja': "未コンパイルのSource Engineモデルデータをインポート",
    },
    'importer_title': {
        'en': "Import SMD/VTA, DMX, QC",
        'ja': "インポート SMD/VTA, DMX, QC",
    },
    'importer_up_tip': {
        'en': "Which axis represents 'up' (ignored for QCs)",
        'ja': "上方向軸を設定（QCは無視）",
    },
    'importer_use_collections': {
        'en': "Create Collections",
        'ja': "コレクションを作成",
    },
    'importer_use_collections_tip': {
        'en': "Create a Blender collection for each imported mesh file. This retains the original file structure (important for DMX) and makes it easy to switch between LODs etc. with the number keys",
        'ja': "インポートされたメッシュファイルごとにBlenderコレクションを作成",
    },
    'insert_uuid': {
        'en': "Insert UUID",
        'ja': "UUIDを挿入",
    },
    'insert_uuid_tip': {
        'en': "Inserts a random UUID at the current location",
        'ja': "現在の位置にランダムなUUIDを挿入",
    },
    'label_armature_data': {
        'en': "Armature Data",
        'jp': "アーマチュアデータ"
    },
    'label_activate': {
        'en': "Activate",
        'ja': "有効化",
    },
    'label_add_all': {
        'en': "Add All",
        'ja': "すべて追加",
    },
    'label_import_flex_text': {
        'en': "Import from Text Block",
        'ja': "テキストブロックからインポート",
    },
    'label_combine_stereo': {
        'en': "Combine L/R into Stereo",
        'ja': "左右をステレオに統合",
    },
    'op_combine_stereo_tip': {
        'en': "Merge left_/right_ controller pairs into a single base-named stereo controller (e.g. left_lid_raise + right_lid_raise -> lid_raise)",
        'ja': "left_/right_ コントローラーのペアを単一のベース名ステレオコントローラーに統合 (例: left_lid_raise + right_lid_raise -> lid_raise)",
    },
    'op_import_flex_text_tip': {
        'en': "Import flex controllers and rules from QC-style text in a Blender text block",
        'ja': "BlenderのテキストブロックのQC形式テキストからフレックスコントローラーとルールをインポート",
    },
    'op_import_flex_text_block_tip': {
        'en': "Blender text block containing the flexcontroller / localvar / %expression definitions",
        'ja': "flexcontroller / localvar / %式の定義を含むBlenderテキストブロック",
    },
    'label_all_attachments': {
        'en': "All Attachments",
        'ja': "すべてのアタッチメント",
    },
    'label_all_hitboxes': {
        'en': "All Hitboxes",
        'ja': "すべてのヒットボックス",
    },
    'label_all_jigglebones': {
        'en': "All Jigglebones",
        'ja': "すべてのジグルボーン",
    },
    'label_angle': {
        'en': "Angle",
        'ja': "角度",
    },
    'label_angle_constraints': {
        'en': "Angle Constraints:",
        'ja': "角度制約:",
    },
    'label_attachment_no_parent': {
        'en': "Attachment cannot be a parent",
        'ja': "アタッチメントは親になれません",
    },
    'label_base_spring_properties': {
        'en': "Base Spring Properties:",
        'ja': "ベーススプリングプロパティ:",
    },
    'label_boing_properties': {
        'en': "Boing Properties:",
        'ja': "バネ動作プロパティ:",
    },
    'label_controller_name': {
        'en': "Controller Name",
        'ja': "コントローラー名",
    },
    'label_damping': {
        'en': "Damping",
        'ja': "減衰",
    },
    'label_delete_all': {
        'en': "Delete All",
        'ja': "すべて削除",
    },
    'label_delta_name': {
        'en': "Delta Name",
        'ja': "デルタ名",
    },
    'label_direction_naming': {
        'en': "Direction Naming:",
        'ja': "方向命名:",
    },
    'label_dme_components_valid': {
        'en': "Components valid",
    },
    'label_dme_override_conflict': {
        'en': "Conflict: renames to an existing or duplicated delta name",
        'ja': "競合: 既存または重複したデルタ名へのリネーム",
    },
    'prop_delta_override_split_tip': {
        'en': "On export, split this shape key into separate <delta>L and <delta>R deltas using the mesh stereo balance, instead of one whole delta. Does not work if the shape key is assigned directly to a flex controller",
        'ja': "エクスポート時に、このシェイプキーを1つのデルタではなく、メッシュのステレオバランスを使用して <delta>L と <delta>R の個別のデルタに分割します。シェイプキーがフレックスコントローラーに直接割り当てられている場合は機能しません",
    },
    'label_dme_split_hint': {
        'en': "Exports as {0}L and {0}R",
        'ja': "{0}L と {0}R としてエクスポート",
    },
    'label_dme_split_on_controller': {
        'en': "Split ignored: shape key is assigned to a flex controller",
        'ja': "分割は無視されました: シェイプキーがフレックスコントローラーに割り当てられています",
    },
    'exporter_warn_dme_split_no_balance': {
        'en': "'{0}': L/R delta split requested but no stereo balance is configured; the split will be lopsided",
        'ja': "'{0}': L/Rデルタ分割が要求されましたが、ステレオバランスが設定されていません。分割が偏ります",
    },
    'exporter_warn_dme_split_on_controller': {
        'en': "'{0}': shape key '{1}' is split to L/R but is assigned to a flex controller; exporting it whole instead",
        'ja': "'{0}': シェイプキー '{1}' はL/Rに分割されますが、フレックスコントローラーに割り当てられています。代わりに全体をエクスポートします",
    },
    'delta_override_filter_tip': {
        'en': "Filter overrides by shape key or delta name",
        'ja': "シェイプキーまたはデルタ名で上書きをフィルター",
    },
    'label_dme_corrective_hint': {
        'en': "component shape keys separated by +",
    },
    'label_dme_dominator_hint': {
        'en': "Dominators: controller names, comma-separated",
    },
    'label_dme_expression_hint': {
        'en': "%localvar  or  controller:  + - * / () min() max() sqrt()",
    },
    'label_dme_expression_valid': {
        'en': "Expression valid",
    },
    'label_dme_flex_controllers': {
        'en': "Flex Controllers",
    },
    'label_dme_flex_rules': {
        'en': "Flex Rules & Domination",
    },
    'label_dme_suppressed_hint': {
        'en': "Suppressed: delta shape names, comma-separated",
    },
    'label_dme_unknown_controller': {
        'en': "{0}: unknown controller",
    },
    'label_dme_unknown_delta': {
        'en': "%{0}: unknown shape key or local var",
    },
    'label_dme_unknown_shapekey': {
        'en': "{0}: unknown shape key",
    },
    'label_dmx_only': {
        'en': "Only Applicable in DMX!",
        'ja': "DMXのみ適用可能!",
    },
    'label_export_name_format': {
        'en': "Export Name",
        'ja': "エクスポート名",
    },
    'label_extra_args': {
        'en': "Extra Args",
        'ja': "追加引数",
    },
    'label_flex_type': {
        'en': "Flex Type",
        'ja': "フレックスタイプ",
    },
    'label_forward': {
        'en': "Forward",
        'ja': "前方",
    },
    'label_forward_limits': {
        'en': "Forward Limits:",
        'ja': "前方の制限:",
    },
    'label_friction': {
        'en': "Friction",
        'ja': "摩擦",
    },
    'label_generate_lods': {
        'en': "Generate LODs on export",
        'ja': "エクスポート時にLODを生成",
    },
    'label_hitbox_group': {
        'en': "Hitbox Group",
        'ja': "ヒットボックスグループ",
    },
    'label_ignore': {
        'en': "Ignore",
        'ja': "無視",
    },
    'label_in_multiple_collection': {
        'en': "In Multiple Collections",
        'ja': "複数のコレクションに存在",
    },
    'label_is_eyelid': {
        'en': "Is Eyelid",
        'ja': "まぶた",
    },
    'label_is_stereo': {
        'en': "Is Stereo",
        'ja': "ステレオ",
    },
    'label_jiggle_flexibility': {
        'en': "Flexibility",
        'ja': "柔軟性",
    },
    'label_jiggle_length': {
        'en': "Length",
        'ja': "長さ",
    },
    'label_jiggle_type': {
        'en': "Jiggle Type:",
        'ja': "ジグルタイプ:",
    },
    'label_location_offset': {
        'en': "Location Offset:",
        'ja': "位置オフセット:",
    },
    'label_mass': {
        'en': "Mass",
        'ja': "質量",
    },
    'label_max': {
        'en': "Max",
        'ja': "最大",
    },
    'label_min': {
        'en': "Min",
        'ja': "最小",
    },
    'label_no_attachments': {
        'en': "No Attachments",
        'ja': "アタッチメントなし",
    },
    'label_no_hitboxes': {
        'en': "No Hitboxes",
        'ja': "ヒットボックスなし",
    },
    'label_no_jigglebones': {
        'en': "No Jigglebones",
        'ja': "ジグルボーンなし",
    },
    'label_not_in_collection': {
        'en': "Not in Collection",
        'ja': "コレクションに未登録",
    },
    'label_options': {
        'en': "Options",
        'ja': "オプション",
    },
    'label_physical_properties': {
        'en': "Physical Properties:",
        'ja': "物理プロパティ:",
    },
    'label_pitch': {
        'en': "Pitch",
        'ja': "ピッチ",
    },
    'label_pitch_limits': {
        'en': "Pitch Limits:",
        'ja': "ピッチ制限:",
    },
    'label_preview_additive': {
        'en': "Preview (Additive)",
        'ja': "プレビュー (加算)",
    },
    'label_preview_reset': {
        'en': "Preview (Reset)",
        'ja': "プレビュー (リセット)",
    },
    'label_properties_to_copy': {
        'en': "Properties to copy:",
        'ja': "コピーするプロパティ:",
    },
    'label_rotation_offset': {
        'en': "Rotation Offset:",
        'ja': "回転オフセット:",
    },
    'label_select_valid_bone': {
        'en': "Select a Valid Bone",
        'ja': "有効なボーンを選択",
    },
    'label_shapekey': {
        'en': "Shapekey",
        'ja': "シェイプキー",
    },
    'label_side': {
        'en': "Side",
        'ja': "サイド",
    },
    'label_side_constraints': {
        'en': "Side Constraints:",
        'ja': "サイド制約:",
    },
    'label_side_limits': {
        'en': "Side Limits:",
        'ja': "サイドの制限:",
    },
    'label_sim_gizmo_disabled': {
        'en': "Viewport gizmo posing is unusable while simulation is active.",
        'ja': "シミュレーション実行中は、ビューポートギズモでのポージングが使用できません。",
    },
    'label_sim_gizmo_disabled_2': {
        'en': "Use shortcut keys (R, G, S) to pose bones instead.",
        'ja': "代わりにショートカットキー（R・G・S）でポーズを調整してください。",
    },
    'label_sim_keyframe_warning': {
        'en': "Keyframed bones follow their action during simulation.",
        'ja': "キーフレームが設定されたボーンは、シミュレーション中にアクションに従います。",
    },
    'label_sim_keyframe_warning_2': {
        'en': "Unlink the active action to pose bones manually for testing.",
        'ja': "テスト時に手動でポーズを調整するには、アクティブアクションのリンクを解除してください。",
    },
    'label_sim_hud_jiggle_count': {
        'en': "Jigglebones: {}",
        'ja': "ジグルボーン: {}",
    },
    'label_sim_hud_proc_count': {
        'en': "Procedural Bones: {}",
        'ja': "プロシージャルボーン: {}",
    },
    'label_simulate_jigglebones': {
        'en': "Simulate JiggleBones",
        'ja': "ジグルボーンをシミュレート",
    },
    'label_sort_by_name': {
        'en': "Sort by Name",
        'ja': "名前でソート",
    },
    'label_stiffness': {
        'en': "Stiffness",
        'ja': "剛性",
    },
    'label_stiffness_damping': {
        'en': "Stiffness & Damping:",
        'ja': "剛性と減衰:",
    },
    'label_target_object': {
        'en': "Target Object:",
        'ja': "ターゲットオブジェクト:",
    },
    'label_up': {
        'en': "Up",
        'ja': "上",
    },
    'label_up_limits': {
        'en': "Up Limits:",
        'ja': "上の制限:",
    },
    'label_use_bone_length': {
        'en': "Use Bone Length",
        'ja': "ボーンの長さを使用",
    },
    'label_vertex_animations_help': {
        'en': "Vertex Animations Help",
        'ja': "頂点アニメーションのヘルプ",
    },
    'label_vertex_float_maps': {
        'en': "Vertex Float Maps:",
        'ja': "頂点フロートマップ:",
    },
    'label_vertex_maps': {
        'en': "Vertex Maps:",
        'ja': "頂点マップ:",
    },
    'valvesource_vertex_paint': {
        'en': "Paint Tint Color",
        'ja': "ペイントの色合い",
    },
    'valvesource_vertex_blend': {
        'en': "Paint Blend Params",
        'ja': "ペイントブレンドパラメータ",
    },
    'valvesource_vertex_blend1': {
        'en': "Paint Blend Params 1",
        'ja': "ペイントブレンドパラメータ 1",
    },
    'label_y_to': {
        'en': "Y to...",
        'ja': "Yを...",
    },
    'label_yaw': {
        'en': "Yaw",
        'ja': "ヨー",
    },
    'label_yaw_limits': {
        'en': "Yaw Limits:",
        'ja': "ヨー制限:",
    },
    'vertex_influence_limit_mode': {
        'en': "Limit Vertex Influence Mode",
        'ja': "頂点ウェイト制限モード",
    },
    'vertex_influence_limit': {
        'en': "Limit Vertex Influence",
        'ja': "頂点ウェイトを制限",
    },
    'vertex_influence_limit_tip': {
        'en': "The maximum number of bones that can influence a single vertex.",
        'ja': "1頂点に影響できるボーンの最大数",
    },
    'vertex_influence_limit_mode_auto_tip': {
        'en': "Exporter determines the limit based on format: Source 1 (DMX/SMD) = 3, Source 2 = 4",
        'ja': "エクスポーター形式に応じて自動決定：Source 1 (DMX/SMD) = 3、Source 2 = 4",
    },
    'vertex_influence_limit_mode_manual_tip': {
        'en': "Manually set the vertex influence limit",
        'ja': "頂点ウェイトの上限を手動で設定",
    },
    'menu_flex_controller_specials': {
        'en': "Flex Controller Specials",
        'ja': "フレックスコントローラー特別メニュー",
    },
    'op_add_all_flex_controllers': {
        'en': "Add All Flex Controllers",
        'ja': "すべてのフレックスコントローラーを追加",
    },
    'op_add_flex_controller': {
        'en': "Add Flex Controller",
        'ja': "フレックスコントローラーを追加",
    },
    'op_apply_remap_range': {
        'en': "Apply Remap Range",
        'ja': "リマップ範囲を適用",
    },
    'op_assign_bone_rot_export_offset': {
        'en': "Assign Bone Target Forward",
        'ja': "ボーンターゲット前方を割り当て",
    },
    'op_auto_assign_flex_groups': {
        'en': "Auto Assign Flex Groups",
        'ja': "フレックスグループを自動割り当て",
    },
    'op_auto_assign_flex_groups_tip': {
        'en': "Automatically categorize flex controllers based on keywords",
        'ja': "キーワードに基づいてフレックスコントローラーを自動分類",
    },
    'op_clear_flex_controllers': {
        'en': "Clear All Flex Controllers",
        'ja': "すべてのフレックスコントローラーをクリア",
    },
    'op_copy_bone_export_name': {
        'en': "Copy Name to Clipboard",
        'ja': "クリップボードに名前をコピー",
    },
    'op_copy_flex_controllers': {
        'en': "Copy Flex Data to Selected",
        'ja': "選択オブジェクトにフレックスデータをコピー",
    },
    'op_copy_flex_controllers_tip': {
        'en': "Copy flex controllers, rules, and delta overrides from the active object to other selected mesh objects",
        'ja': "アクティブオブジェクトのフレックスコントローラー、ルール、デルタ上書きを選択メッシュにコピー",
    },
    'op_copy_jigglebone_props': {
        'en': "Copy Jigglebone Properties",
        'ja': "ジグルボーンプロパティをコピー",
    },
    'op_copy_source_bone_props': {
        'en': "Copy Source Bone Properties",
        'ja': "Sourceボーンプロパティをコピー",
    },
    'op_move_flex_controller': {
        'en': "Move Flex Controller",
        'ja': "フレックスコントローラーを移動",
    },
    'op_preview_flex_controller': {
        'en': "Preview Flex Controller",
        'ja': "フレックスコントローラーをプレビュー",
    },
    'op_proc_bone_copy_tolerance': {
        'en': "Copy Tolerance",
        'ja': "許容角度をコピー",
    },
    'op_proc_bone_copy_tolerance_tip': {
        'en': "Copy this entry's tolerance keyframes to the clipboard",
        'ja': "このエントリの許容角度キーフレームをクリップボードにコピー",
    },
    'op_proc_bone_paste_tolerance': {
        'en': "Paste Tolerance",
        'ja': "許容角度を貼り付け",
    },
    'op_proc_bone_paste_tolerance_tip': {
        'en': "Paste tolerance keyframes from the clipboard into this entry's action",
        'ja': "クリップボードの許容角度キーフレームをこのエントリのアクションに貼り付け",
    },
    'op_proc_bone_copy_active': {
        'en': "Copy Active Entry",
        'ja': "アクティブなエントリをコピー",
    },
    'op_proc_bone_copy_active_tip': {
        'en': "Copy the active proc bone entry to clipboard",
        'ja': "アクティブなProcボーンエントリをクリップボードにコピー",
    },
    'op_proc_bone_copy_by_driver_bone': {
        'en': "Copy by Driver Bone",
        'ja': "ドライバーボーンでコピー",
    },
    'op_proc_bone_copy_by_driver_bone_tip': {
        'en': "Copy all proc bone entries sharing the active entry's driver bone to clipboard",
        'ja': "アクティブエントリと同じドライバーボーンを持つすべてのProcボーンエントリをクリップボードにコピー",
    },
    'op_proc_bone_copy_all': {
        'en': "Copy All Entries",
        'ja': "すべてのエントリをコピー",
    },
    'op_proc_bone_copy_all_tip': {
        'en': "Copy all proc bone entries to clipboard",
        'ja': "すべてのProcボーンエントリをクリップボードにコピー",
    },
    'op_proc_bone_paste_entries': {
        'en': "Paste Entries",
        'ja': "エントリを貼り付け",
    },
    'op_proc_bone_paste_entries_tip': {
        'en': "Paste proc bone entries from clipboard into this armature",
        'ja': "クリップボードのProcボーンエントリをこのアーマチュアに貼り付け",
    },
    'op_proc_bone_add_from_selected': {
        'en': "Add Selected as Proc Helpers",
        'ja': "選択ボーンをProcヘルパーとして追加",
    },
    'op_proc_bone_add_from_selected_tip': {
        'en': "Add each selected bone as a helper bone for new Procedural Bone entries. Optionally set a shared driver bone, action, and slot in the dialog",
        'ja': "選択した各ボーンを新しいProcedural Boneのエントリとして追加します。ダイアログで共有ドライバーボーン、アクション、スロットも設定可能です。",
    },
    'op_proc_bone_add_optional_hint': {
        'en': "Optional - leave blank to configure later",
        'ja': "任意 — 後で設定する場合は空欄のままにしてください",
    },
    'op_proc_bone_add_lookat': {
        'en': "Add Selected as LookAt Helpers",
        'ja': "選択ボーンをLookAtヘルパーとして追加",
    },
    'op_proc_bone_add_lookat_tip': {
        'en': "Add each selected bone as a helper bone for new LookAt Procedural Bone entries, aiming at a chosen bone or attachment",
        'ja': "選択した各ボーンを新しいLookAt Procedural Boneのエントリとして追加し、選択したボーンまたはアタッチメントを向かせます。",
    },
    'op_remove_flex_controller': {
        'en': "Remove Flex Controller",
        'ja': "フレックスコントローラーを削除",
    },
    'op_reset_jiggle_simulation': {
        'en': "Reset Simulation",
        'ja': "シミュレーションをリセット",
    },
    'op_reset_jiggle_simulation_tip': {
        'en': "Clear all jiggle and procedural bone simulation states, snapping bones back to their animated pose. Also clears the procedural bone trigger cache so actions are re-sampled on the next tick",
        'ja': "すべてのジグルおよびProcedural Boneのシミュレーション状態をクリアし、ボーンを元のアニメーションポーズに戻します。また、Procedural Boneのトリガーキャッシュもクリアされ、次のティックでアクションが再サンプリングされます。",
    },
    'op_sort_flex_controllers': {
        'en': "Sort Flex Controllers",
        'ja': "フレックスコントローラーをソート",
    },
    'panel_backface': {
        'en': "Backface",
        'ja': "裏面",
    },
    'panel_bone_data': {
        'en': "Bone Data",
        'ja': "ボーンデータ",
    },
    'panel_jigglebones': {
        'en': "Jigglebones",
        'ja': "ジグルボーン",
    },
    'panel_level_of_detail': {
        'en': "Level Of Detail",
        'ja': "詳細度レベル",
    },
    'panel_mesh_split': {
        'en': "Mesh Split",
        'ja': "メッシュ分割",
    },
    'panel_hitboxes': {
        'en': "Hitboxes",
        'ja': "ヒットボックス",
    },
    'panel_proc_bones': {
        'en': "Procedural Bones",
        'ja': "プロシージャルボーン",
    },
    'panel_select_mesh': {
        'en': "Select a Mesh",
        'ja': "メッシュを選択",
    },
    'panel_select_mesh_mat': {
        'en': "Select a material",
        'ja': "マテリアルを選択",
    },
    'panel_toon_outline_edgeline': {
        'en': "Toon Outline/Edgeline",
        'ja': "トゥーンアウトライン/エッジライン",
    },
    'panel_vertex_animations': {
        'en': "Vertex Animations",
        'ja': "頂点アニメーション",
    },
    'panel_vertex_float_maps': {
        'en': "Vertex Float Maps",
        'ja': "頂点フロートマップ",
    },
    'panel_vertex_maps': {
        'en': "Vertex Maps",
        'ja': "頂点マップ",
    },
    'panel_viewport_simulation': {
        'en': "Source Engine Preview & Simulation",
        'ja': "Source Engine プレビュー & シミュレーション",
    },
    'prefab_to_clipboard': {
        'en': "Prefab to Clipboard",
        'ja': "プレハブをクリップボードへ",
    },
    'prefab_to_clipboard_tip': {
        'en': "Copy prefab export content to clipboard instead of to a file",
        'ja': "プレハブエクスポートのコンテンツをファイルではなくクリップボードにコピー",
    },
    'prefab_export_mode': {
        'en': "Prefab Mode",
        'ja': "プレハブモード",
    },
    'prefab_export_mode_tip': {
        'en': "How jigglebones, attachments, hitboxes and procedural bones are exported (Source 1 only). QCI writes them to .qci/.vrd prefab files; DME encodes them into the model .dmx instead",
        'ja': "ジグルボーン・アタッチメント・ヒットボックス・プロシージャルボーンのエクスポート方法 (Source 1 のみ)。QCI は .qci/.vrd プレハブファイルに書き出し、DME はそれらをモデルの .dmx に埋め込みます",
    },
    'prefab_export_mode_qci_tip': {
        'en': "Write jigglebones, attachments and hitboxes to separate .qci prefab files",
        'ja': "ジグルボーン・アタッチメント・ヒットボックスを個別の .qci プレハブファイルに書き出します",
    },
    'prefab_export_mode_dme_tip': {
        'en': "Encode jigglebones, hitboxes, attachments and procedural bones into the exported model .dmx (no .qci/.vrd is written). Requires a DME-capable Source 1 compiler (KitsuneMDL)",
        'ja': "ジグルボーン・ヒットボックス・アタッチメント・プロシージャルボーンをエクスポートされるモデルの .dmx に埋め込みます (.qci/.vrd は書き出されません)。DME 対応の Source 1 コンパイラ (KitsuneMDL) が必要です",
    },
    'bone_naming_label': {
        'en': "Bone Naming",
        'ja': "ボーン命名",
    },
    'force_source2_bone_sanitize': {
        'en': "Force Source 2 Bone Names",
        'ja': "Source 2 ボーン名を強制",
    },
    'force_source2_bone_sanitize_tip': {
        'en': "Apply Source 2 (ModelDoc) bone name sanitization even when exporting for Source 1. Strips dots and non-ASCII characters (except preserved prefixes below), so e.g. 'Bone.001' becomes 'Bone_001'",
        'ja': "Source 1 へのエクスポート時でも Source 2 (ModelDoc) のボーン名サニタイズを適用します。ドットや非 ASCII 文字を除去し (下記の保持プレフィックスを除く)、例として 'Bone.001' は 'Bone_001' になります",
    },
    'bone_name_prefixes_title': {
        'en': "Preserved Bone Name Prefixes",
        'ja': "保持するボーン名プレフィックス",
    },
    'bone_name_prefixes_desc': {
        'en': "Prefixes kept verbatim during Source 2 sanitization. The trailing dot is added automatically.",
        'ja': "Source 2 サニタイズ時にそのまま保持されるプレフィックス。末尾のドットは自動で付きます。",
    },
    'bone_name_prefixes_desc2': {
        'en': "Shortcut: type !name! in a bone export name to insert the prefix (leave empty to disable).",
        'ja': "ショートカット: ボーンのエクスポート名に !name! と入力するとプレフィックスに展開されます (空欄で無効)。",
    },
    'bone_name_prefix': {
        'en': "Prefix",
        'ja': "プレフィックス",
    },
    'bone_name_prefix_tip': {
        'en': "Bone name prefix kept verbatim during Source 2 sanitization (the trailing dot is added automatically)",
        'ja': "Source 2 サニタイズ時にそのまま保持されるボーン名プレフィックス (末尾のドットは自動追加)",
    },
    'bone_name_shortcut': {
        'en': "Shortcut",
        'ja': "ショートカット",
    },
    'bone_name_shortcut_tip': {
        'en': "Optional shortcut token. Type !token! in a bone export name to insert this prefix (e.g. !vbip! -> ValveBiped.). The enclosing ! ! are added automatically; leave empty for no shortcut",
        'ja': "任意のショートカットトークン。ボーンのエクスポート名に !token! と入力するとこのプレフィックスに展開されます (例: !vbip! -> ValveBiped.)。囲みの ! ! は自動付与。空欄でショートカットなし",
    },
    'prop_arm_items_view': {
        'en': "View",
        'ja': "表示",
    },
    'prop_backface_vgroup': {
        'en': "Backface Group",
        'ja': "裏面グループ",
    },
    'prop_backface_vgroup_tip': {
        'en': "Vertex group that identifies faces to receive generated backfaces",
        'ja': "裏面を生成する面を特定する頂点グループ",
    },
    'prop_backface_vgroup_tolerance': {
        'en': "Backface Tolerance",
        'ja': "裏面許容値",
    },
    'prop_backface_vgroup_tolerance_tip': {
        'en': "Weight threshold above which a vertex gets a generated backface",
        'ja': "このしきい値を超える頂点に裏面が生成されます",
    },
    'prop_bone_dir_left': {
        'en': "Left Bone Dir",
        'ja': "左ボーン方向",
    },
    'prop_bone_dir_left_tip': {
        'en': "String identifying left-side bones for directional naming",
        'ja': "方向命名で左側のボーンを識別する文字列",
    },
    'prop_bone_dir_right': {
        'en': "Right Bone Dir",
        'ja': "右ボーン方向",
    },
    'prop_bone_dir_right_tip': {
        'en': "String identifying right-side bones for directional naming",
        'ja': "方向命名で右側のボーンを識別する文字列",
    },
    'prop_bone_is_jigglebone': {
        'en': "Bone is JiggleBone",
        'ja': "ボーンはジグルボーン",
    },
    'prop_bone_is_jigglebone_tip': {
        'en': "Mark this bone as a Source Engine jigglebone",
        'ja': "このボーンをSource Engineのジグルボーンとしてマーク",
    },
    'prop_bone_name_startcount': {
        'en': "Bone Name Starting Count",
        'ja': "ボーン名開始番号",
    },
    'prop_bone_name_startcount_tip': {
        'en': "Starting number used when auto-numbering duplicate bone names",
        'ja': "ボーン名の重複時に自動採番を開始する番号",
    },
    'prop_bone_sort_order': {
        'en': "Bone Sort Order",
        'ja': "ボーンソート順",
    },
    'prop_bone_sort_order_tip': {
        'en': "Vertex group culling priority; 0 is the highest priority",
        'ja': "頂点グループの間引き優先度。0が最も高い優先度",
    },
    'prop_controller_name': {
        'en': "Controller Name",
        'ja': "コントローラー名",
    },
    'prop_controller_name_tip': {
        'en': "Exported flex controller name (lowercase letters, numbers, and underscores only)",
        'ja': "エクスポートされるフレックスコントローラー名（小文字、数字、アンダースコアのみ）",
    },
    'prop_decimate_factor': {
        'en': "Decimation Per LOD",
        'ja': "LODあたりの削減率",
    },
    'prop_decimate_factor_tip': {
        'en': "Percentage of faces to remove per LOD level",
        'ja': "LODレベルごとに削除するポリゴンの割合",
    },
    'prop_delta_name': {
        'en': "Delta Name",
        'ja': "デルタ名",
    },
    'prop_delta_name_tip': {
        'en': "Name of the delta shape key referenced by this controller",
        'ja': "このコントローラーが参照するデルタシェイプキー名",
    },
    'prop_delta_override_shapekey_tip': {
        'en': "Blender shape key to rename on export",
        'ja': "エクスポート時にリネームするBlenderシェイプキー",
    },
    'prop_delta_override_name_tip': {
        'en': "Delta name to use in the exported DMX instead of the shape key name",
        'ja': "シェイプキー名の代わりにDMXエクスポートで使用するデルタ名",
    },
    'prop_dme_flexcontrollers': {
        'en': "Flex Controllers",
        'ja': "フレックスコントローラー",
    },
    'prop_dmx_attachment': {
        'en': "Is Attachment",
        'ja': "アタッチメントとして使用",
    },
    'prop_dmx_attachment_tip': {
        'en': "Export this empty as a DMX model attachment point",
        'ja': "このエンプティをDMXモデルのアタッチメントポイントとしてエクスポート",
    },
    'prop_edgeline_per_material': {
        'en': "Edgeline Per Material",
        'ja': "マテリアルごとのエッジライン",
    },
    'prop_edgeline_per_material_tip': {
        'en': "Generate a separate edge shell per material slot",
        'ja': "マテリアルスロットごとに別のエッジシェルを生成",
    },
    'prop_edgeline_thickness': {
        'en': "Thickness",
        'ja': "太さ",
    },
    'prop_edgeline_thickness_tip': {
        'en': "Base thickness of the generated toon edge shell",
        'ja': "生成されるトゥーンエッジシェルの基本の太さ",
    },
    'prop_edgeline_vgroup': {
        'en': "Vertex Group Ratio",
        'ja': "頂点グループ比率",
    },
    'prop_edgeline_vgroup_tip': {
        'en': "Vertex group that scales edge thickness per vertex (0=full thickness, 1=no edge)",
        'ja': "エッジの太さを頂点ごとにスケールする頂点グループ（0=最大の太さ、1=エッジなし）",
    },
    'prop_export_edgeline_separately': {
        'en': "Export Edgeline Separately",
        'ja': "エッジラインを別ファイルでエクスポート",
    },
    'prop_export_edgeline_separately_tip': {
        'en': "Write the edge shell as a separate DMX/SMD file instead of appending to the model",
        'ja': "エッジシェルをモデルに追加せず別個のDMX/SMDファイルとして書き出す",
    },
    'prop_export_mesh_split_separately': {
        'en': "Export Mesh Split Separately",
        'ja': "メッシュ分割を別ファイルでエクスポート",
    },
    'prop_export_mesh_split_separately_tip': {
        'en': "Write mesh split segments as separate DMX files",
        'ja': "メッシュ分割セグメントを別個のDMXファイルとして書き出す",
    },
    'prop_eyelid': {
        'en': "Eyelid",
        'ja': "まぶた",
    },
    'prop_eyelid_tip': {
        'en': "Tag this controller as an eyelid flex for Source Engine",
        'ja': "このコントローラーをSource Engineのまぶたフレックスとしてタグ付け",
    },
    'prop_flex_type': {
        'en': "Flex Type",
        'ja': "フレックスタイプ",
    },
    'prop_flex_type_tip': {
        'en': "Flex group category used for QC organization",
        'ja': "QC整理に使用されるフレックスグループカテゴリー",
    },
    'prop_flex_group_custom_tip': {
        'en': "Custom flex group name exported when Flex Group is set to CUSTOM",
        'ja': "フレックスグループがCUSTOMの場合にエクスポートされるカスタムフレックスグループ名",
    },
    'prop_flex_min_tip': {
        'en': "Minimum value for this flex controller",
        'ja': "このフレックスコントローラーの最小値",
    },
    'prop_flex_max_tip': {
        'en': "Maximum value for this flex controller",
        'ja': "このフレックスコントローラーの最大値",
    },
    'prop_flexctrl_shapekey_tip': {
        'en': "Shape key driven by this flex controller",
        'ja': "このフレックスコントローラーが制御するシェイプキー",
    },
    'prop_dme_flex_rule_type_tip': {
        'en': "Type of flex rule element to write in DmeFlexRules",
        'ja': "DmeFlexRulesに書き込むフレックスルール要素の種類",
    },
    'prop_dme_flex_rule_expression_tip': {
        'en': "Shape driven by a math expression referencing controller names",
        'ja': "コントローラー名を参照する数式で制御されるシェイプ",
    },
    'prop_dme_flex_rule_passthrough_tip': {
        'en': "Shape controlled directly by its matching flex controller (no expression needed)",
        'ja': "対応するフレックスコントローラーで直接制御されるシェイプ（数式不要）",
    },
    'prop_dme_flex_rule_localvar_tip': {
        'en': "Intermediate variable that can be referenced in other expressions",
        'ja': "他の数式から参照できる中間変数",
    },
    'prop_dme_flex_rule_domination_tip': {
        'en': "Suppress certain shapes when specified controllers are active",
        'ja': "指定したコントローラーがアクティブなときに特定のシェイプを無効化",
    },
    'prop_dme_flex_rule_corrective_tip': {
        'en': "Mark a shape key as a corrective driven by the combination of its component shapes",
    },
    'prop_dme_corrective_components_tip': {
        'en': "Component shape key names separated by +, e.g. brow+anger+mouth",
    },
    'prop_dme_flex_rule_name_tip': {
        'en': "Delta shape key name (for Expression/PassThrough) or local variable name (for Local Var)",
        'ja': "デルタシェイプキー名（Expression/PassThrough用）またはローカル変数名（Local Var用）",
    },
    'prop_dme_flex_rule_expr_tip': {
        'en': "Math expression using controller names, +, -, *, /, (), min(), max(), sqrt()",
        'ja': "コントローラー名と +, -, *, /, (), min(), max(), sqrt() を使う数式",
    },
    'prop_dme_dominator_names_tip': {
        'en': "Comma-separated flex controller names that trigger this domination rule",
        'ja': "このドミネーションルールを発動するフレックスコントローラー名（カンマ区切り）",
    },
    'prop_dme_suppressed_names_tip': {
        'en': "Comma-separated delta shape names to suppress when dominators are active",
        'ja': "ドミネーターがアクティブなときに無効化するデルタシェイプ名（カンマ区切り）",
    },
    'exporter_warn_dme_smd': {
        'en': "'{0}' uses DME Rule mode which is DMX-only - flex rules are ignored for SMD export",
        'ja': "'{0}' はDMEルールモードを使用していますがSMDエクスポートでは無視されます",
    },
    'prop_float_map_group_tip': {
        'en': "Vertex map group name to remap",
        'ja': "リマップする頂点マップのグループ名",
    },
    'prop_generate_backface': {
        'en': "Generate Backface",
        'ja': "裏面を生成",
    },
    'prop_generate_backface_tip': {
        'en': "Duplicate and flip faces for vertices in the backface vertex group",
        'ja': "裏面頂点グループの頂点に対して面を複製して反転",
    },
    'prop_generate_lods': {
        'en': "Generate LODs on Export",
        'ja': "エクスポート時にLODを生成",
    },
    'prop_generate_lods_tip': {
        'en': "Generate decimated LOD meshes automatically on export",
        'ja': "エクスポート時にデシメートされたLODメッシュを自動生成",
    },
    'op_hitbox_add': {
        'en': "Add Hitbox",
        'ja': "ヒットボックスを追加",
    },
    'op_hitbox_from_bone': {
        'en': "Add from Selected Bones",
        'ja': "選択ボーンから追加",
    },
    'op_hitbox_remove': {
        'en': "Remove Hitbox",
        'ja': "ヒットボックスを削除",
    },
    'op_hitbox_duplicate': {
        'en': "Duplicate Hitbox",
    },
    'op_hitbox_copy_entry': {
        'en': "Copy Hitbox Entry",
    },
    'op_hitbox_copy_entry_tip': {
        'en': "Copy the active hitbox entry to the clipboard",
    },
    'op_hitbox_copy_all': {
        'en': "Copy All Hitboxes",
    },
    'op_hitbox_copy_all_tip': {
        'en': "Copy all hitbox entries from this armature to the clipboard",
    },
    'op_hitbox_paste_entries': {
        'en': "Paste Hitbox Entries",
    },
    'op_hitbox_paste_entries_tip': {
        'en': "Append clipboard hitbox entries to the current armature",
    },
    'op_hitbox_paste_values': {
        'en': "Paste Values",
    },
    'op_hitbox_paste_values_tip': {
        'en': "Overwrite the selected hitbox entry with values from the clipboard (no new entry created)",
    },
    'op_hitbox_copy_to_armature': {
        'en': "Copy All to Selected Armature(s)",
    },
    'op_hitbox_copy_to_armature_tip': {
        'en': "Replace hitboxes on all other selected armatures with a copy of this armature's hitboxes",
    },
    'op_hitbox_mirror_x': {
        'en': "Mirror X",
    },
    'op_hitbox_mirror_x_tip': {
        'en': "Mirror the active hitbox along the X axis",
    },
    'op_hitbox_mirror_y': {
        'en': "Mirror Y",
    },
    'op_hitbox_mirror_y_tip': {
        'en': "Mirror the active hitbox along the Y axis",
    },
    'op_hitbox_mirror_z': {
        'en': "Mirror Z",
    },
    'op_hitbox_mirror_z_tip': {
        'en': "Mirror the active hitbox along the Z axis",
    },
    'prop_hitbox_sync_pose': {
        'en': "Pose Sync",
    },
    'prop_hitbox_sync_pose_tip': {
        'en': "Automatically select the matching UIList entry when the active pose bone changes",
    },
    'prop_hitbox_sync_propagate': {
        'en': "Propagate Edits",
    },
    'prop_hitbox_sync_propagate_tip': {
        'en': "Apply the same delta to all hitbox entries on selected pose bones when editing the active entry (in Pose mode)",
    },
    'prop_hitbox_bone': {
        'en': "Bone",
        'ja': "ボーン",
    },
    'prop_hitbox_bone_tip': {
        'en': "Bone this hitbox is attached to",
        'ja': "このヒットボックスが関連付けられているボーン",
    },
    'prop_hitbox_rotation': {
        'en': "Rotation",
        'ja': "回転",
    },
    'prop_hitbox_rotation_tip': {
        'en': "Rotation of the hitbox around its center in bone-local space (degrees in QC)",
        'ja': "ボーンローカル空間でのヒットボックスの中心周りの回転（QCでは度数）",
    },
    'prop_hitbox_scale': {
        'en': "Scale / Radius",
        'ja': "スケール / 半径",
    },
    'prop_hitbox_scale_tip': {
        'en': "0 or negative = oriented box (OBB), positive = capsule with this radius",
        'ja': "0以下 = 方向付きボックス (OBB)、正の値 = この半径のカプセル",
    },
    'prop_hitbox_vec_max': {
        'en': "Max / P2",
        'ja': "最大 / P2",
    },
    'prop_hitbox_vec_min': {
        'en': "Min / P1",
        'ja': "最小 / P1",
    },
    'prop_hitbox_hboxset': {
        'en': "HBox Set",
        'ja': "ヒットボックスセット",
    },
    'prop_hitbox_hboxset_tip': {
        'en': "Name of the hitbox set. Defaults to \"default\" on export if left empty.",
        'ja': "ヒットボックスセットの名前。空の場合はエクスポート時に「default」になります。",
    },
    'prop_ignore_location_offset': {
        'en': "Ignore Location Offsets",
        'ja': "位置オフセットを無視",
    },
    'prop_ignore_location_offset_tip': {
        'en': "Skip applying the location offset for this bone during export",
        'ja': "エクスポート時にこのボーンの位置オフセットの適用をスキップ",
    },
    'prop_ignore_rotation_offset': {
        'en': "Ignore Rotation Offsets",
        'ja': "回転オフセットを無視",
    },
    'prop_ignore_rotation_offset_tip': {
        'en': "Skip applying the rotation offset for this bone during export",
        'ja': "エクスポート時にこのボーンの回転オフセットの適用をスキップ",
    },
    'prop_jiggle_allow_length_flex': {
        'en': "Allow Length Flex",
        'ja': "長さフレックスを許可",
    },
    'prop_jiggle_allow_length_flex_tip': {
        'en': "Allow the jigglebone to stretch along its length",
        'ja': "ジグルボーンの長さに沿ったストレッチを許可",
    },
    'prop_jiggle_along_damping': {
        'en': "Along Damping",
        'ja': "軸方向減衰",
    },
    'prop_jiggle_along_damping_tip': {
        'en': "Damping along the bone length",
        'ja': "ボーンの長さ方向の減衰",
    },
    'prop_jiggle_along_stiffness': {
        'en': "Along Stiffness",
        'ja': "軸方向剛性",
    },
    'prop_jiggle_along_stiffness_tip': {
        'en': "Spring strength along the bone length",
        'ja': "ボーンの長さ方向のばね力",
    },
    'prop_jiggle_amplitude': {
        'en': "Amplitude",
        'ja': "振幅",
    },
    'prop_jiggle_amplitude_tip': {
        'en': "Oscillation amplitude of the boing spring",
        'ja': "ボイングスプリングの振幅",
    },
    'label_jiggle_collision': {
        'en': "Collision (Source 2)",
        'ja': "コリジョン (Source 2)",
    },
    'prop_jiggle_has_collision': {
        'en': "Enable Collision",
        'ja': "コリジョンを有効化",
    },
    'prop_jiggle_has_collision_tip': {
        'en': "Add a collision capsule to this jigglebone (Source 2 / ModelDoc only)",
        'ja': "このジグルボーンにコリジョンカプセルを追加します (Source 2 / ModelDoc 専用)",
    },
    'prop_jiggle_collision_radius0': {
        'en': "Radius Head",
        'ja': "半径 (始点)",
    },
    'prop_jiggle_collision_radius0_tip': {
        'en': "Capsule radius at the head endpoint (point 0)",
        'ja': "始点 (point 0) でのカプセル半径",
    },
    'prop_jiggle_collision_radius1': {
        'en': "Radius Tip",
        'ja': "半径 (終点)",
    },
    'prop_jiggle_collision_radius1_tip': {
        'en': "Capsule radius at the tip endpoint (point 1)",
        'ja': "終点 (point 1) でのカプセル半径",
    },
    'prop_jiggle_collision_point0': {
        'en': "Point 0",
        'ja': "ポイント 0",
    },
    'prop_jiggle_collision_point0_tip': {
        'en': "Head endpoint of the collision capsule, in bone-local space (follows the bone's export rotation/location offset)",
        'ja': "コリジョンカプセルの始点。ボーンローカル空間で指定します (ボーンのエクスポート回転/位置オフセットに追従します)",
    },
    'prop_jiggle_collision_point1': {
        'en': "Point 1",
        'ja': "ポイント 1",
    },
    'prop_jiggle_collision_point1_tip': {
        'en': "Tip endpoint of the collision capsule, in bone-local space (follows the bone's export rotation/location offset)",
        'ja': "コリジョンカプセルの終点。ボーンローカル空間で指定します (ボーンのエクスポート回転/位置オフセットに追従します)",
    },
    'prop_jiggle_angle_constraint': {
        'en': "Angle Constraint",
        'ja': "角度制約",
    },
    'prop_jiggle_angle_constraint_tip': {
        'en': "Enable overall angular rotation limit",
        'ja': "全体的な角度回転制限を有効化",
    },
    'prop_jiggle_angular_constraint': {
        'en': "Angular Constraint",
        'ja': "最大角度変位",
    },
    'prop_jiggle_angular_constraint_tip': {
        'en': "Maximum angular displacement allowed from rest",
        'ja': "静止位置から許容される最大角変位",
    },
    'prop_jiggle_base_damping': {
        'en': "Base Damping",
        'ja': "ベース減衰",
    },
    'prop_jiggle_base_damping_tip': {
        'en': "Damping at the base spring of the jigglebone",
        'ja': "ジグルボーンのベーススプリングの減衰",
    },
    'prop_jiggle_base_mass': {
        'en': "Base Mass",
        'ja': "ベース質量",
    },
    'prop_jiggle_base_mass_tip': {
        'en': "Mass applied at the jigglebone base",
        'ja': "ジグルボーンベースの質量",
    },
    'prop_jiggle_base_stiffness': {
        'en': "Base Stiffness",
        'ja': "ベース剛性",
    },
    'prop_jiggle_base_stiffness_tip': {
        'en': "Spring stiffness at the base of the jigglebone",
        'ja': "ジグルボーンのベースのばね剛性",
    },
    'prop_jiggle_base_type': {
        'en': "Base Type",
        'ja': "ベースタイプ",
    },
    'prop_jiggle_base_type_tip': {
        'en': "Type of base spring behavior attached to this jigglebone",
        'ja': "このジグルボーンに付属するベーススプリングの動作タイプ",
    },
    'prop_jiggle_damping_rate': {
        'en': "Damping Rate",
        'ja': "減衰率",
    },
    'prop_jiggle_damping_rate_tip': {
        'en': "How quickly boing oscillation decays over time",
        'ja': "ボイング振動が時間とともに減衰する速さ",
    },
    'prop_jiggle_flex_type': {
        'en': "Flexible Type",
        'ja': "柔軟タイプ",
    },
    'prop_jiggle_flex_type_tip': {
        'en': "Type of flexible motion for the jigglebone",
        'ja': "ジグルボーンの柔軟な動きのタイプ",
    },
    'prop_jiggle_forward_constraint': {
        'en': "Forward Constraint",
        'ja': "前方制約",
    },
    'prop_jiggle_forward_constraint_tip': {
        'en': "Enable forward/backward constraint",
        'ja': "前後制約を有効化",
    },
    'prop_jiggle_forward_constraint_max': {
        'en': "Max Forward Constraint",
        'ja': "前方制約の最大値",
    },
    'prop_jiggle_forward_constraint_max_tip': {
        'en': "Maximum forward displacement allowed",
        'ja': "許容される最大の前方変位",
    },
    'prop_jiggle_forward_constraint_min': {
        'en': "Min Forward Constraint",
        'ja': "前方制約の最小値",
    },
    'prop_jiggle_forward_constraint_min_tip': {
        'en': "Minimum forward displacement allowed",
        'ja': "許容される最小の前方変位",
    },
    'prop_jiggle_forward_friction': {
        'en': "Forward Friction",
        'ja': "前方摩擦",
    },
    'prop_jiggle_forward_friction_tip': {
        'en': "Friction applied when sliding against forward constraint",
        'ja': "前方制約に当たって滑るときの摩擦",
    },
    'prop_jiggle_frequency': {
        'en': "Frequency",
        'ja': "周波数",
    },
    'prop_jiggle_frequency_tip': {
        'en': "Oscillation frequency of the boing spring",
        'ja': "ボイングスプリングの振動周波数",
    },
    'prop_jiggle_impact_angle': {
        'en': "Impact Angle",
        'ja': "衝撃角度",
    },
    'prop_jiggle_impact_angle_tip': {
        'en': "Rotation angle applied on impact",
        'ja': "衝撃時に適用される回転角度",
    },
    'prop_jiggle_impact_speed': {
        'en': "Impact Speed",
        'ja': "衝撃速度",
    },
    'prop_jiggle_impact_speed_tip': {
        'en': "Minimum speed required to trigger impact response",
        'ja': "衝撃応答をトリガーするために必要な最低速度",
    },
    'prop_jiggle_length': {
        'en': "Length",
        'ja': "長さ",
    },
    'prop_jiggle_length_tip': {
        'en': "Rest length of the jigglebone segment",
        'ja': "ジグルボーンセグメントの静止長",
    },
    'prop_jiggle_pitch_constraint': {
        'en': "Pitch Constraint",
        'ja': "ピッチ制約",
    },
    'prop_jiggle_pitch_constraint_tip': {
        'en': "Enable pitch rotation constraint",
        'ja': "ピッチ回転制約を有効化",
    },
    'prop_jiggle_pitch_constraint_max': {
        'en': "Max Pitch Constraint",
        'ja': "ピッチ制約の最大値",
    },
    'prop_jiggle_pitch_constraint_max_tip': {
        'en': "Maximum pitch rotation allowed",
        'ja': "許容される最大のピッチ回転",
    },
    'prop_jiggle_pitch_constraint_min': {
        'en': "Min Pitch Constraint",
        'ja': "ピッチ制約の最小値",
    },
    'prop_jiggle_pitch_constraint_min_tip': {
        'en': "Minimum pitch rotation allowed",
        'ja': "許容される最小のピッチ回転",
    },
    'prop_jiggle_pitch_friction': {
        'en': "Pitch Friction",
        'ja': "ピッチ摩擦",
    },
    'prop_jiggle_pitch_friction_tip': {
        'en': "Friction applied during pitch constraint motion",
        'ja': "ピッチ制約動作中に適用される摩擦",
    },
    'prop_jiggle_pitch_damping': {
        'en': "Pitch Damping",
        'ja': "ピッチ減衰",
    },
    'prop_jiggle_pitch_damping_tip': {
        'en': "Resistance that slows down pitch motion over time",
        'ja': "ピッチ運動を時間とともに減速させる抵抗",
    },
    'prop_jiggle_pitch_stiffness': {
        'en': "Pitch Stiffness",
        'ja': "ピッチ剛性",
    },
    'prop_jiggle_pitch_stiffness_tip': {
        'en': "Spring strength resisting pitch rotation",
        'ja': "ピッチ回転に抵抗するばね力",
    },
    'prop_jiggle_side_constraint': {
        'en': "Side Constraint",
        'ja': "サイド制約",
    },
    'prop_jiggle_side_constraint_tip': {
        'en': "Enable side constraints to limit sideways motion",
        'ja': "横方向の動きを制限するサイド制約を有効化",
    },
    'prop_jiggle_side_constraint_max': {
        'en': "Max Side Constraint",
        'ja': "サイド制約の最大値",
    },
    'prop_jiggle_side_constraint_max_tip': {
        'en': "Maximum sideways offset allowed",
        'ja': "許容される最大の横方向オフセット",
    },
    'prop_jiggle_side_constraint_min': {
        'en': "Min Side Constraint",
        'ja': "サイド制約の最小値",
    },
    'prop_jiggle_side_constraint_min_tip': {
        'en': "Minimum sideways offset allowed",
        'ja': "許容される最小の横方向オフセット",
    },
    'prop_jiggle_side_friction': {
        'en': "Side Friction",
        'ja': "サイド摩擦",
    },
    'prop_jiggle_side_friction_tip': {
        'en': "Friction applied when sliding against side constraint",
        'ja': "サイド制約に当たって滑るときの摩擦",
    },
    'prop_proc_sim_enabled': {
        'en': "Procedural Simulation",
        'ja': "プロシージャルシミュレーション",
    },
    'prop_proc_sim_enabled_tip': {
        'en': "Enable real-time Jiggle/procedural in the 3D viewport",
        'ja': "3Dビューポートでのリアルタイムのジグル/プロシージャルを有効にします",
    },
    'prop_jiggle_sim_rate': {
        'en': "Sim Rate (Hz)",
        'ja': "シミュレーションレート (Hz)",
    },
    'prop_jiggle_sim_rate_tip': {
        'en': "Simulation update rate in Hz - higher is smoother but uses more CPU",
        'ja': "シミュレーション更新レート (Hz) - 高いほど滑らかになりますが、CPU使用量が増加します",
    },
    'prop_jiggle_tip_mass': {
        'en': "Tip Mass",
        'ja': "先端質量",
    },
    'prop_jiggle_tip_mass_tip': {
        'en': "Mass at the end of the jigglebone",
        'ja': "ジグルボーン先端の質量",
    },
    'prop_jiggle_up_constraint': {
        'en': "Up Constraint",
        'ja': "上方制約",
    },
    'prop_jiggle_up_constraint_tip': {
        'en': "Enable vertical up/down constraint",
        'ja': "垂直上下制約を有効化",
    },
    'prop_jiggle_up_constraint_max': {
        'en': "Max Up Constraint",
        'ja': "上方制約の最大値",
    },
    'prop_jiggle_up_constraint_max_tip': {
        'en': "Maximum upward displacement allowed",
        'ja': "許容される最大の上方向変位",
    },
    'prop_jiggle_up_constraint_min': {
        'en': "Min Up Constraint",
        'ja': "上方制約の最小値",
    },
    'prop_jiggle_up_constraint_min_tip': {
        'en': "Minimum upward displacement allowed",
        'ja': "許容される最小の上方向変位",
    },
    'prop_jiggle_up_friction': {
        'en': "Up Friction",
        'ja': "上方摩擦",
    },
    'prop_jiggle_up_friction_tip': {
        'en': "Friction applied when sliding against upward constraint",
        'ja': "上方制約に当たって滑るときの摩擦",
    },
    'prop_jiggle_yaw_constraint': {
        'en': "Yaw Constraint",
        'ja': "ヨー制約",
    },
    'prop_jiggle_yaw_constraint_tip': {
        'en': "Enable yaw rotation constraint",
        'ja': "ヨー回転制約を有効化",
    },
    'prop_jiggle_yaw_constraint_max': {
        'en': "Max Yaw Constraint",
        'ja': "ヨー制約の最大値",
    },
    'prop_jiggle_yaw_constraint_max_tip': {
        'en': "Maximum yaw rotation allowed",
        'ja': "許容される最大のヨー回転",
    },
    'prop_jiggle_yaw_constraint_min': {
        'en': "Min Yaw Constraint",
        'ja': "ヨー制約の最小値",
    },
    'prop_jiggle_yaw_constraint_min_tip': {
        'en': "Minimum yaw rotation allowed",
        'ja': "許容される最小のヨー回転",
    },
    'prop_jiggle_yaw_friction': {
        'en': "Yaw Friction",
        'ja': "ヨー摩擦",
    },
    'prop_jiggle_yaw_friction_tip': {
        'en': "Friction applied during yaw constraint motion",
        'ja': "ヨー制約動作中に適用される摩擦",
    },
    'prop_jiggle_yaw_damping': {
        'en': "Yaw Damping",
        'ja': "ヨー減衰",
    },
    'prop_jiggle_yaw_damping_tip': {
        'en': "Resistance that slows down yaw motion over time",
        'ja': "ヨー運動を時間とともに減速させる抵抗",
    },
    'prop_jiggle_yaw_stiffness': {
        'en': "Yaw Stiffness",
        'ja': "ヨー剛性",
    },
    'prop_jiggle_yaw_stiffness_tip': {
        'en': "Spring strength resisting yaw rotation",
        'ja': "ヨー回転に抵抗するばね力",
    },
    'prop_location_x': {
        'en': "Location X",
        'ja': "位置 X",
    },
    'prop_location_x_tip': {
        'en': "Location offset applied to this bone's X position on export",
        'ja': "エクスポート時にこのボーンのX位置に適用されるオフセット",
    },
    'prop_location_y': {
        'en': "Location Y",
        'ja': "位置 Y",
    },
    'prop_location_y_tip': {
        'en': "Location offset applied to this bone's Y position on export",
        'ja': "エクスポート時にこのボーンのY位置に適用されるオフセット",
    },
    'prop_location_z': {
        'en': "Location Z",
        'ja': "位置 Z",
    },
    'prop_location_z_tip': {
        'en': "Location offset applied to this bone's Z position on export",
        'ja': "エクスポート時にこのボーンのZ位置に適用されるオフセット",
    },
    'prop_location_offset_space': {
        'en': "Armature Space Input",
        'ja': "アーマチュア空間入力",
    },
    'prop_location_offset_space_tip': {
        'en': "Enter location offset in armature space instead of bone local space. The stored value is always in local bone space.",
        'ja': "ボーンのローカル空間ではなくアーマチュア空間で位置オフセットを入力します。保存される値は常にボーンのローカル空間です。",
    },
    'prop_location_arm_x': {
        'en': "Location X (Arm)",
        'ja': "位置 X (アーム)",
    },
    'prop_location_arm_x_tip': {
        'en': "Location offset in armature space (X). Converted to local bone space on edit.",
        'ja': "アーマチュア空間での位置オフセット (X)。編集時にボーンのローカル空間に変換されます。",
    },
    'prop_location_arm_y': {
        'en': "Location Y (Arm)",
        'ja': "位置 Y (アーム)",
    },
    'prop_location_arm_y_tip': {
        'en': "Location offset in armature space (Y). Converted to local bone space on edit.",
        'ja': "アーマチュア空間での位置オフセット (Y)。編集時にボーンのローカル空間に変換されます。",
    },
    'prop_location_arm_z': {
        'en': "Location Z (Arm)",
        'ja': "位置 Z (アーム)",
    },
    'prop_location_arm_z_tip': {
        'en': "Location offset in armature space (Z). Converted to local bone space on edit.",
        'ja': "アーマチュア空間での位置オフセット (Z)。編集時にボーンのローカル空間に変換されます。",
    },
    'prop_lod_count': {
        'en': "LOD count",
        'ja': "LOD数",
    },
    'prop_lod_count_tip': {
        'en': "Number of LOD levels to generate beyond LOD0",
        'ja': "LOD0以降に生成するLODレベルの数",
    },
    'prop_max_mesh_split': {
        'en': "Max Order Number",
        'ja': "最大順序番号",
    },
    'prop_max_mesh_split_tip': {
        'en': "Maximum number of mesh split order segments to generate",
        'ja': "生成するメッシュ分割の最大セグメント数",
    },
    'prop_mesh_split_threshold': {
        'en': "Mesh Split Threshold",
        'ja': "メッシュ分割しきい値",
    },
    'prop_mesh_split_threshold_tip': {
        'en': "Weight threshold above which a vertex belongs to the split mesh",
        'ja': "このしきい値を超える頂点は分割メッシュに属します",
    },
    'prop_non_exportable_vgroup': {
        'en': "Export Cull Vertex Group",
        'ja': "エクスポートカリング頂点グループ",
    },
    'prop_non_exportable_vgroup_tip': {
        'en': "Vertices in this group above the weight threshold are culled from export",
        'ja': "このグループでしきい値を超える頂点はエクスポート時に削除されます",
    },
    'prop_non_exportable_vgroup_tolerance': {
        'en': "Export Cull Weight Threshold",
        'ja': "エクスポートカリングウェイトしきい値",
    },
    'prop_non_exportable_vgroup_tolerance_tip': {
        'en': "Vertices with weights above this threshold are culled from export",
        'ja': "このしきい値を超えるウェイトの頂点はエクスポート時に削除されます",
    },
    'prop_normalize_shapekeys': {
        'en': "Normalize Shapekeys",
        'ja': "シェイプキーを正規化",
    },
    'prop_normalize_shapekeys_tip': {
        'en': "Normalize shapekeys so max value is 1 and min is -1 or 0",
        'ja': "シェイプキーの最大値を1、最小値を-1または0に正規化",
    },
    'prop_override_dmx_export_path_tip': {
        'en': "Override the material path written into DMX for this material",
        'ja': "このマテリアルのDMXに書き込まれるマテリアルパスを上書き",
    },
    'prop_prefab_filepath': {
        'en': "Filepath",
        'ja': "ファイルパス",
    },
    'prop_prefab_filepath_tip': {
        'en': "Output path for this prefab. Leave blank to export next to the Scene export path "
              "using the default name \"<armature>_<type>\". A directory uses the default name; "
              "a full file path is used as-is. Relative paths are taken from the Scene export path.",
        'ja': "このプレハブの出力先パス。空欄の場合はシーンのエクスポートパスに既定名 "
              "\"<アーマチュア>_<種類>\" で出力します。ディレクトリを指定すると既定名を使用し、"
              "ファイルパスを指定するとそのまま使用します。相対パスはシーンのエクスポートパス基準です。",
    },
    'prop_prefab_export_tip': {
        'en': "Include this prefab when exporting the scene",
        'ja': "シーンのエクスポート時にこのプレハブを含める",
    },
    'prop_preview_edgeline': {
        'en': "Preview Edgeline",
        'ja': "エッジラインをプレビュー",
    },
    'prop_preview_edgeline_tip': {
        'en': "Draw edgeline shell in the viewport, approximating the exported result",
        'ja': "エクスポート結果に近いエッジラインシェルをビューポートに描画",
    },
    'prop_preview_attachment_mesh': {
        'en': "Attachment Mesh Preview",
    },
    'prop_preview_attachment_mesh_tip': {
        'en': "Draw the assigned display mesh as a ghost at attachment empties",
    },
    'prop_attachment_display_mesh': {
        'en': "Display Mesh",
    },
    'prop_attachment_display_mesh_tip': {
        'en': "Mesh to display as a ghost at this attachment point",
    },
    'prop_attachment_display_mesh_color': {
        'en': "Ghost Color",
    },
    'prop_attachment_display_mesh_color_tip': {
        'en': "Color and opacity of the attachment mesh ghost",
    },
    'warn_dme_dmx_only_panel': {
        'en': "DME mode is DMX-only - ignored for SMD export.",
        'ja': "DMEモードはDMX専用です — SMDエクスポートでは無視されます。",
    },
    'warn_edgeline_jiggle_sim': {
        'en': "Inactive: paused while jiggle simulation runs",
        'ja': "非アクティブ: ジグルシミュレーション中は停止",
    },
    'warn_edgeline_expensive': {
        'en': "Expensive - may cause viewport lag",
        'ja': "負荷が高い - ビューポートが重くなる場合があります",
    },
    'warn_edgeline_approximate': {
        'en': "Preview is approximate - may show",
        'ja': "プレビューは近似値です。エクスポートには",
    },
    'warn_edgeline_smudging': {
        'en': "smudging not present in export",
        'ja': "存在しないにじみが表示される場合があります",
    },
    'prop_preview_hitboxes': {
        'en': "Preview Hitboxes",
        'ja': "ヒットボックスをプレビュー",
    },
    'prop_preview_hitboxes_tip': {
        'en': "All: draw all hitboxes; Selected: draw only the list-selected entry; Pose: draw hitboxes for selected pose bones; None: hide preview",
    },
    'prop_preview_export_pose': {
        'en': "Preview Export Pose",
        'ja': "エクスポートポーズをプレビュー",
    },
    'prop_preview_export_pose_tip': {
        'en': "Draw a ghost bone showing where this bone will be after export offsets are applied",
        'ja': "エクスポートオフセット適用後のボーン位置をゴーストボーンで表示",
    },
    'prop_preview_jigglebone_constraints': {
        'en': "Preview Jigglebone Constraints",
        'ja': "ジグルボーン制約のプレビュー",
    },
    'prop_preview_jigglebone_constraints_tip': {
        'en': "Show jigglebone angle and constraint visualizations in the viewport",
        'ja': "ビューポートでジグルボーンの角度と制約の可視化を表示",
    },
    'prop_preview_proc_bones': {
        'en': "Preview Procedural Bones",
        'ja': "プロシージャルボーンのプレビュー",
    },
    'prop_preview_proc_bones_tip': {
        'en': "Draw aim target and offset markers for the active procedural bone list entry",
        'ja': "アクティブなプロシージャルボーンリストのエントリのエイムターゲットとオフセットマーカーを表示",
    },
    'prop_proc_bone_action': {
        'en': "Action",
        'ja': "アクション",
    },
    'prop_proc_bone_action_tip': {
        'en': "Action whose keyframes define trigger-to-target pose pairs for the procedural bone",
        'ja': "プロシージャルボーンのトリガー→ターゲットポーズペアを定義するキーフレームを持つアクション",
    },
    'prop_proc_bone_frame_end': {
        'en': "Frame End",
        'ja': "終了フレーム",
    },
    'prop_proc_bone_frame_end_tip': {
        'en': "Last frame to sample for trigger poses (only used when Manual Range is on)",
        'ja': "トリガーポーズをサンプリングする最後のフレーム（手動範囲がオンの場合のみ使用）",
    },
    'prop_proc_bone_frame_start': {
        'en': "Frame Start",
        'ja': "開始フレーム",
    },
    'prop_proc_bone_frame_start_tip': {
        'en': "First frame to sample for trigger poses (only used when Manual Range is on)",
        'ja': "トリガーポーズをサンプリングする最初のフレーム（手動範囲がオンの場合のみ使用）",
    },
    'prop_proc_bone_preview_frame': {
        'en': "Frame",
        'ja': "フレーム",
    },
    'prop_proc_bone_preview_frame_tip': {
        'en': "Current frame shown in the tolerance navigator; use << < > >> to step through the trigger range",
        'ja': "許容角度ナビゲーターに表示される現在のフレーム。<< < > >>で範囲内を移動します",
    },
    'prop_proc_bone_use_manual_range': {
        'en': "Manual Range",
        'ja': "手動範囲",
    },
    'prop_proc_bone_use_manual_range_tip': {
        'en': "Set frame range manually instead of detecting it from the action's bone keyframes",
        'ja': "アクションのボーンキーフレームから自動検出する代わりに、フレーム範囲を手動で設定します",
    },
    'warn_no_trigger_frames': {
        'en': "No valid bone keyframes in action",
        'ja': "アクションに有効なボーンキーフレームがありません",
    },
    'prop_proc_bone_driver': {
        'en': "Driver Bone",
        'ja': "ドライバーボーン",
    },
    'prop_proc_bone_driver_tip': {
        'en': "Bone whose current pose is compared against the action triggers",
        'ja': "現在のポーズをアクションのトリガーと比較するボーン",
    },
    'prop_proc_bone_helper': {
        'en': "Helper Bone",
        'ja': "ヘルパーボーン",
    },
    'prop_proc_bone_helper_tip': {
        'en': "Bone that is driven by the procedural simulation",
        'ja': "プロシージャルシミュレーションによって駆動されるボーン",
    },
    'prop_proc_bone_reference_armature': {
        'en': "Reference Armature",
        'ja': "参照アーマチュア",
    },
    'prop_proc_bone_reference_armature_tip': {
        'en': "Optional. Compute the action triggers on this armature instead of the "
              "one being exported. Use it when two near-identical rigs (e.g. the same "
              "character with a different outfit or IK setup) should share the base "
              "rig's triggers. The driver and helper bones must exist on it by name; "
              "rest position (basePos) still comes from the exported armature",
        'ja': "任意。エクスポート対象ではなくこのアーマチュアでアクショントリガーを計算します。"
              "ほぼ同一のリグ（衣装やIK構成だけが異なる同一キャラクターなど）でベースリグの"
              "トリガーを共有したい場合に使用します。ドライバーボーンとヘルパーボーンが名前で"
              "存在する必要があります。レスト位置（basePos）はエクスポート対象のアーマチュアから取得されます",
    },
    'prop_proc_bone_lookat_aim_axis': {
        'en': "Aim Axis",
        'ja': "エイム軸",
    },
    'prop_proc_bone_lookat_aim_axis_tip': {
        'en': "Local bone axis that points toward the target bone (aimvector)",
        'ja': "ターゲットボーンに向くボーンのローカル軸（aimvector）",
    },
    'prop_proc_bone_lookat_offset': {
        'en': "Aim Offset",
        'ja': "エイムオフセット",
    },
    'prop_proc_bone_lookat_offset_tip': {
        'en': "Local-space offset from the target bone's exported origin to aim at",
        'ja': "ターゲットボーンのエクスポート原点からのローカル空間オフセット",
    },
    'prop_proc_bone_lookat_target': {
        'en': "Target Bone",
        'ja': "ターゲットボーン",
    },
    'prop_proc_bone_lookat_target_tip': {
        'en': "Bone whose head position this bone aims at",
        'ja': "このボーンが向くヘッド位置を持つボーン",
    },
    'prop_proc_bone_lookat_target_type': {
        'en': "Target Type",
        'ja': "ターゲットタイプ",
    },
    'prop_proc_bone_lookat_target_type_tip': {
        'en': "Whether the LookAt target is a bone or an attachment (Empty parented to a bone)",
        'ja': "LookAtターゲットがボーンか、アタッチメント（ボーンに親付けされたEmpty）かを指定します。",
    },
    'prop_proc_bone_lookat_target_attachment': {
        'en': "Target Attachment",
        'ja': "ターゲットアタッチメント",
    },
    'prop_proc_bone_lookat_target_attachment_tip': {
        'en': "Empty object whose position this bone aims at. Must be parented to a bone on this armature",
        'ja': "このボーンが向く位置を持つEmptyオブジェクト。このアーマチュアのボーンに親付けされている必要があります。",
    },
    'warn_lookat_attachment_invalid': {
        'en': "Pick an Empty parented to a bone on this armature",
        'ja': "このアーマチュアのボーンに親付けされたEmptyを選択してください",
    },
    'prop_proc_bone_lookat_up_axis': {
        'en': "Up Axis",
        'ja': "アップ軸",
    },
    'prop_proc_bone_lookat_up_axis_tip': {
        'en': "Local bone axis kept aligned with world up (upvector)",
        'ja': "ワールドのアップ方向に合わせるボーンのローカル軸（upvector）",
    },
    'prop_proc_bone_slot': {
        'en': "Slot",
        'ja': "スロット",
    },
    'prop_proc_bone_slot_tip': {
        'en': "Action slot identifier for Blender 4.5+ layered actions (leave empty to use the first slot)",
        'ja': "Blender 4.5+のレイヤーアクション用スロット識別子（空の場合は最初のスロットを使用）",
    },
    'prop_proc_bone_type': {
        'en': "Type",
        'ja': "タイプ",
    },
    'prop_proc_bone_type_tip': {
        'en': "Trigger: action-driven pose blending.  LookAt: continuously aim toward a target bone",
        'ja': "トリガー：アクション駆動のポーズブレンド。  LookAt：ターゲットボーンへ継続的に向く",
    },
    'prop_pose_bone_proc_tolerance': {
        'en': "Proc Bone Tolerance",
        'ja': "Procボーン許容角度",
    },
    'prop_pose_bone_proc_tolerance_tip': {
        'en': "Angular cone within which this trigger pose is active when used as a Proc Bone driver. Keyframe in the driver's action to vary per trigger",
        'ja': "このボーンがProcボーンのドライバーとして使用される場合のトリガーポーズが有効になるコーンの角度。アクション内でキーフレームを打つとトリガーごとに変化させることができます。",
    },
    'prop_reset_pose_per_anim': {
        'en': "Reset Pose Per Animation Export",
        'ja': "アニメーションエクスポートごとにポーズをリセット",
    },
    'prop_reset_pose_per_anim_tip': {
        'en': "Reset all bones to rest pose before each export",
        'ja': "各エクスポート前にすべてのボーンを静止ポーズにリセット",
    },
    'prop_rotation_x': {
        'en': "Rotation X",
        'ja': "回転 X",
    },
    'prop_rotation_x_tip': {
        'en': "Rotation offset applied to this bone's X axis on export",
        'ja': "エクスポート時にこのボーンのX軸に適用される回転オフセット",
    },
    'prop_rotation_y': {
        'en': "Rotation Y",
        'ja': "回転 Y",
    },
    'prop_rotation_y_tip': {
        'en': "Rotation offset applied to this bone's Y axis on export",
        'ja': "エクスポート時にこのボーンのY軸に適用される回転オフセット",
    },
    'prop_rotation_z': {
        'en': "Rotation Z",
        'ja': "回転 Z",
    },
    'prop_rotation_z_tip': {
        'en': "Rotation offset applied to this bone's Z axis on export",
        'ja': "エクスポート時にこのボーンのZ軸に適用される回転オフセット",
    },
    'prop_rotation_copy_target': {
        'en': "Copy Rotation From Bone",
        'ja': "ボーンから回転をコピー",
    },
    'prop_rotation_copy_target_tip': {
        'en': "When set, X/Y/Z rotation offset values are mirrored from this bone, adjusted for any difference in rest-pose orientation. Clear to set offsets manually",
        'ja': "設定すると、このボーンからX/Y/Z回転オフセットが自動的にコピーされ、レストポーズの向きの差に応じて調整されます。手動設定する場合は空欄にしてください",
    },
    'prop_shapekey': {
        'en': "ShapeKey",
        'ja': "シェイプキー",
    },
    'prop_sim_jiggle_bones': {
        'en': "Simulate Jiggle Bones",
        'ja': "ジグルボーンをシミュレート",
    },
    'prop_sim_jiggle_bones_tip': {
        'en': "Run jiggle bone spring simulation",
        'ja': "ジグルボーンのばねシミュレーションを実行",
    },
    'prop_sim_proc_bones': {
        'en': "Simulate Procedural Bones",
        'ja': "プロシージャルボーンをシミュレート",
    },
    'prop_sim_proc_bones_tip': {
        'en': "Run action-driven procedural bone simulation",
        'ja': "アクション駆動のプロシージャルボーンシミュレーションを実行",
    },
    'prop_stereo': {
        'en': "Stereo",
        'ja': "ステレオ",
    },
    'prop_stereo_tip': {
        'en': "Split this controller into left/right stereo variants",
        'ja': "このコントローラーを左右のステレオバリアントに分割",
    },
    'prop_use_bone_length_for_jb': {
        'en': "Use Bone's Length for JiggleBone Length",
        'ja': "ボーンの長さをジグルボーンの長さに使用",
    },
    'prop_use_bone_length_for_jb_tip': {
        'en': "Use this bone's length as the jigglebone segment length",
        'ja': "このボーンの長さをジグルボーンセグメントの長さとして使用",
    },
    'prop_use_mesh_split': {
        'en': "Separate Mesh Split",
        'ja': "メッシュ分割",
    },
    'prop_use_mesh_split_tip': {
        'en': "Split the mesh by vertex group weight for multi-part export",
        'ja': "頂点グループのウェイトでメッシュを分割してマルチパートエクスポート",
    },
    'prop_use_toon_edgeline': {
        'en': "Use Toon Edge Line",
        'ja': "トゥーンエッジラインを使用",
    },
    'prop_use_toon_edgeline_tip': {
        'en': "Generate a thickened edge shell for toon-style outlines",
        'ja': "トゥーンスタイルのアウトライン用に厚みのあるエッジシェルを生成",
    },
    'prop_vertex_anim_name_tip': {
        'en': "Name used in the exported QC sequence",
        'ja': "エクスポートされるQCシークエンスで使用される名前",
    },
    'qc_warn_noarmature': {
        'en': "Skipping {0}; no armature found.",
        'ja': "{0}をスキップ: アーマチュアが見つかりません",
    },
    'qc_warn_noarmature_hbox': {
        'en': "Skipping $hbox import in {0}; no armature found.",
        'ja': "{0}の$hboxインポートをスキップ: アーマチュアが見つかりません",
    },
    'scene_export': {
        'en': "Scene Export",
        'ja': "シーンをエクスポート",
    },
    'settings_prop': {
        'en': "Blender Source Tools settings",
        'ja': "Blender Source Tools 設定",
    },
    'shape_stereo_mode': {
        'en': "DMX stereo split mode",
        'ja': "DMXステレオ分割モード",
    },
    'shape_stereo_mode_tip': {
        'en': "How stereo split balance should be defined",
        'ja': "ステレオ分割バランスの定義方法",
    },
    'shape_stereo_mode_vgroup': {
        'en': "Use a vertex group to define stereo balance",
        'ja': "頂点グループでステレオバランスを定義",
    },
    'shape_stereo_sharpness': {
        'en': "DMX stereo split sharpness",
        'ja': "DMXステレオ分割シャープネス",
    },
    'shape_stereo_sharpness_tip': {
        'en': "How sharply stereo flex shapes should transition from left to right",
        'ja': "ステレオフレックスシェイプの左右遷移のシャープさ",
    },
    'shape_stereo_vgroup': {
        'en': "DMX stereo split vertex group",
        'ja': "DMXステレオ分割頂点グループ",
    },
    'shape_stereo_vgroup_tip': {
        'en': "The vertex group that defines stereo balance (0=Left, 1=Right)",
        'ja': "ステレオバランスを定義する頂点グループ (0=左, 1=右)",
    },
    'slot_filter': {
        'en': "Slot Filter",
        'ja': "スロットフィルター",
    },
    'slot_filter_tip': {
        'en': "Slots of the assigned Action with names matching this wildcard filter pattern will be exported (blank to export everything)",
        'ja': "ワイルドカードフィルターに一致するスロットをエクスポート",
    },
    'smd_format': {
        'en': "Target Engine",
        'ja': "対象のエンジン",
    },
    'smd_format_tip': {
        'en': "Target game engine for SMD export",
        'ja': "SMDエクスポートの対象ゲームエンジン",
    },
    'subdir': {
        'en': "Subfolder",
        'ja': "サブフォルダー",
    },
    'subdir_tip': {
        'en': "Optional path relative to scene output folder",
        'ja': "シーン出力フォルダーからの相対パス（省略可）",
    },
    'triangulate': {
        'en': "Triangulate",
        'ja': "三角化",
    },
    'triangulate_tip': {
        'en': "Avoids concave DMX faces, which are not supported by Source",
        'ja': "Sourceがサポートしない凹面DMX面を回避",
    },
    'up_axis': {
        'en': "Target Up Axis",
        'ja': "対象の上方向軸",
    },
    'up_axis_offset': {
        'en': "Target Up Axis Offset",
        'ja': "ターゲット上方向軸オフセット",
    },
    'up_axis_tip': {
        'en': "Use for compatibility with data from other 3D tools",
        'ja': "他の3Dツールとの互換性のために使用",
    },
    'use_scene_export_tip': {
        'en': "Export this item with the scene",
        'ja': "このアイテムをシーンと一緒にエクスポート",
    },
    'vca_add': {
        'en': "Add Vertex Animation",
        'ja': "頂点アニメーションを追加",
    },
    'vca_add_tip': {
        'en': "Add a Vertex Animation to the active Source Tools exportable",
        'ja': "アクティブなエクスポート可能に頂点アニメーションを追加",
    },
    'vca_end_tip': {
        'en': "Scene frame at which to stop recording Vertex Animation",
        'ja': "頂点アニメーションの記録を停止するシーンフレーム",
    },
    'vca_group_props': {
        'en': "Vertex Animation",
        'ja': "頂点アニメーション",
    },
    'vca_preview': {
        'en': "Preview Vertex Animation",
        'ja': "頂点アニメーションを再生します",
    },
    'vca_preview_tip': {
        'en': "Plays the active Source Tools Vertex Animation using scene preview settings",
        'ja': "シーンプレビュー設定で頂点アニメーションを再生",
    },
    'vca_qcgen': {
        'en': "Generate QC Segment",
        'ja': "QCの抜粋を生成します",
    },
    'vca_qcgen_tip': {
        'en': "Copies a QC segment for this object's Vertex Animations to the clipboard",
        'ja': "頂点アニメーションのQCセグメントをクリップボードにコピー",
    },
    'vca_remove': {
        'en': "Remove Vertex Animation",
        'ja': "頂点アニメーションを削除",
    },
    'vca_remove_tip': {
        'en': "Remove the active Vertex Animation from the active Source Tools exportable",
        'ja': "アクティブな頂点アニメーションを削除",
    },
    'vca_sequence': {
        'en': "Generate Sequence",
        'ja': "シークエンスを生成します",
    },
    'vca_sequence_tip': {
        'en': "On export, generate an animation sequence that drives this Vertex Animation",
        'ja': "エクスポート時にこの頂点アニメーションを駆動するシークエンスを生成",
    },
    'vca_start_tip': {
        'en': "Scene frame at which to start recording Vertex Animation",
        'ja': "頂点アニメーションの記録を開始するシーンフレーム",
    },
    'vertmap_create': {
        'en': "Create Source 2 Vertex Map",
        'ja': "Source 2 頂点マップを作成",
    },
    'vertmap_remove': {
        'en': "Remove Source 2 Vertex Map",
        'ja': "Source 2 頂点マップを削除",
    },
    'vertmap_select': {
        'en': "Select Source 2 Vertex Map",
        'ja': "Source 2 頂点マップを選択",
    },
    'weightlink_threshold': {
        'en': "Weight Link Cull Threshold",
        'ja': "ウェイト・リンクの間引きのしきい値",
    },
    'weightlink_threshold_tip': {
        'en': "The minimum weight value below which vertex weights are removed to eliminate noise.",
        'ja': "頂点ウェイトが削除される最小ウェイト値",
    },
    'world_scale': {
        'en': "World Scale",
        'ja': "ワールドスケール",
    },
    'world_scale_tip': {
        'en': "Scales the objects in the world proportionally",
        'ja': "ワールドのすべてのオブジェクトを比例してスケーリング",
    },
}

def _get_ids() -> dict[str,str]:
    ids = {}
    for id,values in _data.items():
        ids[id] = values['en']
    return ids
ids = _get_ids()

def _get_translations():
    import collections
    translations = collections.defaultdict(dict)
    for lang in _languages:
        for id,values in _data.items():
            value = values.get(lang)
            if value: translations[lang][(None, ids[id])] = value
    return translations
translations = _get_translations()