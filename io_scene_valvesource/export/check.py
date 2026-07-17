# ExportCheck - shared validation mixin for the export operators (SmdExporter,
# PrefabExporter). Lives in its own module so both can import it without a cycle.


class ExportCheck:
    def check_duplicate_bone_names(self, bone_names_dict: dict) -> bool:
        seen = {}
        duplicates = []
        for bone, name in bone_names_dict.items():
            if name in seen:
                duplicates.append(name)
            else:
                seen[name] = bone

        if not duplicates:
            return True

        dupe_report = {
            name: [b for b, n in bone_names_dict.items() if n == name]
            for name in set(duplicates)
        }
        msg = "Found duplicate bone export names:\n"
        for name, bones in dupe_report.items():
            msg += f"- '{name}' used by: {', '.join(bones)}\n"
        self.report({"ERROR"}, msg)
        return False
