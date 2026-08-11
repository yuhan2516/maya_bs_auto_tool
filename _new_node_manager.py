# -*- coding: utf-8 -*-

try:
    import hou
except Exception:
    hou = None

from . import abc_parser


class NodeManager(object):
    PART_NODE_KEYS = (
        "blast", "primitive_group", "constraint_group", "vellumcloth"
    )
    PART_NODE_NAMES = {
        "blast": "blast_{part}",
        "primitive_group": "groupprim_{part}",
        "constraint_group": "grouppoint_{part}",
        "vellumcloth": "vellumcloth_{part}"
    }

    def __init__(self, node_config=None, save_callback=None):
        self.node_config = node_config or {}
        self.save_callback = save_callback

    def get_node(self, node_path):
        if hou is None or not node_path:
            return None
        return hou.node(node_path)

    def validate_required_nodes(self):
        missing = []
        for group_name, data in self.node_config.get("abc_groups", {}).items():
            path = data.get("alembic", "")
            if path and self.get_node(path) is None:
                missing.append(("abc_groups.{}.alembic".format(group_name), path))
        for key in ("alembic_node", "solver_node", "top_network"):
            path = self.node_config.get(key, "")
            if path and self.get_node(path) is None:
                missing.append((key, path))
        for part_name, data in self.node_config.get("parts", {}).items():
            for key in self.PART_NODE_KEYS:
                path = data.get(key, "")
                if path and self.get_node(path) is None:
                    missing.append(("{}.{}".format(part_name, key), path))
        return missing

    def set_node_parm(self, node_path, parm_name, value):
        node = self.get_node(node_path)
        if node is None:
            return False
        parm = node.parm(parm_name)
        if parm is None:
            return False
        parm.set(value)
        return True

    def get_node_parm(self, node_path, parm_name, default=None):
        node = self.get_node(node_path)
        if node is None:
            return default
        parm = node.parm(parm_name)
        if parm is None:
            return default
        try:
            return parm.eval()
        except Exception:
            return default

    def set_abc_file_for_all_groups(self, abc_path):
        return {
            name: self.set_abc_group_file(name, abc_path)
            for name in ("collision", "cloth", "output")
        }

    def set_abc_group_file(self, group_name, abc_path):
        data = self.node_config.get("abc_groups", {}).get(group_name, {})
        node_path = data.get("alembic", "") or self.node_config.get("alembic_node", "")
        return self.set_node_parm(node_path, data.get("file_parm", "fileName"), abc_path)

    def set_abc_group_object_paths(self, group_name, object_paths):
        data = self.node_config.get("abc_groups", {}).get(group_name, {})
        node_path = data.get("alembic", "") or self.node_config.get("alembic_node", "")
        expression = abc_parser.object_paths_to_object_path_expression(object_paths)
        return self.set_node_parm(
            node_path, data.get("object_path_parm", "objectPath"), expression
        )

    def sync_classification_nodes(self, asset_data):
        result = {
            "collision": self.set_abc_group_object_paths("collision", asset_data.collision_objects),
            "cloth": self.set_abc_group_object_paths("cloth", self._all_cloth_objects(asset_data)),
            "output": self.set_abc_group_object_paths("output", asset_data.output_objects),
            "parts": {}
        }
        for part_name, object_paths in asset_data.cloth_parts.items():
            if object_paths:
                self.ensure_part_nodes(part_name)
                result["parts"][part_name] = self.sync_part_nodes(part_name, object_paths)
        return result

    def ensure_part_nodes(self, part_name):
        parts = self.node_config.setdefault("parts", {})
        part_data = parts.setdefault(part_name, self._empty_part_config())
        if all(self.get_node(part_data.get(key, "")) for key in self.PART_NODE_KEYS):
            return part_data
        template_name = self._template_part_name(exclude=part_name)
        if not template_name:
            return part_data
        template_data = parts[template_name]
        source_nodes = [self.get_node(template_data.get(key, "")) for key in self.PART_NODE_KEYS]
        if any(node is None for node in source_nodes):
            return part_data
        parents = set(node.parent().path() for node in source_nodes)
        if len(parents) != 1:
            return part_data
        parent = source_nodes[0].parent()
        copied = hou.copyNodesTo(tuple(source_nodes), parent)
        if len(copied) != len(source_nodes):
            return part_data
        for key, node in zip(self.PART_NODE_KEYS, copied):
            node.setName(self.PART_NODE_NAMES[key].format(part=part_name), unique_name=True)
            part_data[key] = node.path()
        for key in ("blast_group_parm", "primitive_group_parm", "constraint_group_parm"):
            part_data[key] = template_data.get(key, self._empty_part_config()[key])
        self._save_config()
        return part_data

    def rename_part(self, old_name, new_name):
        parts = self.node_config.setdefault("parts", {})
        if old_name not in parts or new_name in parts:
            return False
        data = parts.pop(old_name)
        parts[new_name] = data
        for key in self.PART_NODE_KEYS:
            node = self.get_node(data.get(key, ""))
            if node is not None:
                node.setName(self.PART_NODE_NAMES[key].format(part=new_name), unique_name=True)
                data[key] = node.path()
        self._save_config()
        return True

    def add_part(self, part_name):
        parts = self.node_config.setdefault("parts", {})
        if part_name in parts:
            return False
        parts[part_name] = self._empty_part_config()
        self._save_config()
        return True

    def remove_part_config(self, part_name):
        if part_name not in self.node_config.get("parts", {}):
            return False
        self.node_config["parts"].pop(part_name)
        self._save_config()
        return True

    def sync_part_nodes(self, part_name, object_paths):
        part_data = self.node_config.get("parts", {}).get(part_name, {})
        expression = abc_parser.object_paths_to_group_expression(object_paths)
        return {
            "blast": self.set_node_parm(part_data.get("blast", ""), part_data.get("blast_group_parm", "group"), expression),
            "primitive_group": self.set_node_parm(part_data.get("primitive_group", ""), part_data.get("primitive_group_parm", "groupname"), part_name),
            "constraint_group": self.set_node_parm(part_data.get("constraint_group", ""), part_data.get("constraint_group_parm", "groupname"), part_name)
        }

    def set_blast_group(self, part_name, group_names):
        if group_names:
            self.ensure_part_nodes(part_name)
        return self.sync_part_nodes(part_name, group_names).get("blast", False)

    def get_vellum_node_path(self, part_name):
        return self.node_config.get("parts", {}).get(part_name, {}).get("vellumcloth", "")

    def pick_node_path(self, start_node_path="/obj"):
        if hou is None:
            return ""
        try:
            return hou.ui.selectNode(initial_node=start_node_path) or ""
        except Exception:
            return ""

    def jump_to_node(self, node_path):
        if hou is None:
            return False
        node = self.get_node(node_path)
        if node is None:
            return False
        node.setSelected(True, clear_all_selected=True)
        try:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if pane is not None:
                pane.setCurrentNode(node)
                pane.homeToSelection()
        except Exception:
            pass
        return True

    def _template_part_name(self, exclude=""):
        configured = self.node_config.get("part_template", "")
        names = [configured] if configured else sorted(self.node_config.get("parts", {}))
        for name in names:
            if not name or name == exclude:
                continue
            data = self.node_config["parts"].get(name, {})
            if all(self.get_node(data.get(key, "")) for key in self.PART_NODE_KEYS):
                return name
        return ""

    def _empty_part_config(self):
        return {
            "blast": "", "blast_group_parm": "group",
            "primitive_group": "", "primitive_group_parm": "groupname",
            "constraint_group": "", "constraint_group_parm": "groupname",
            "vellumcloth": ""
        }

    def _save_config(self):
        if self.save_callback:
            self.save_callback(self.node_config)

    def _all_cloth_objects(self, asset_data):
        seen = set()
        result = []
        for object_paths in asset_data.cloth_parts.values():
            for object_path in object_paths:
                if object_path not in seen:
                    seen.add(object_path)
                    result.append(object_path)
        if result:
            return result
        return [path for path in asset_data.all_objects if path not in asset_data.collision_objects and path not in asset_data.output_objects]
