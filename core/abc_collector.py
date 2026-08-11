# -*- coding: utf-8 -*-
import maya.cmds as cmds

from . import name_parser


def collect_abc_meshes_from_selection():
    """
    从当前选中的 transform 递归收集 Alembic mesh。
    以用户实际选中的 transform 作为 root 计算相对路径。

    返回：
        {
            "items": [
                {
                    "dag_path": "|Cloth_wrap|grpA|skirt1",
                    "shape_path": "...",
                    "basename": "skirt1",
                    "root_long_path": "|Cloth_wrap",
                    "relative_path": "grpA|skirt1"
                },
                ...
            ],
            "align_root": "Cloth_wrap",
            "root_long_paths": ["|Cloth_wrap", ...]
        }
    """
    selected = cmds.ls(selection=True, long=True) or []
    if not selected:
        cmds.warning(u"请先选择导入的 Alembic top group 或 mesh。")
        return {"items": [], "align_root": "", "root_long_paths": []}

    roots = _resolve_roots(selected)
    if not roots:
        cmds.warning(u"选中的对象不是 transform 或 mesh。")
        return {"items": [], "align_root": "", "root_long_paths": []}

    roots = _filter_nested_roots(roots)

    root_align_map, align_roots = _build_root_align_map(roots)

    result = []
    collected_paths = set()
    shape_types = ["mesh", "nurbsCurve"]
    align_root_count = len(align_roots)
    for root in roots:
        align_root = root_align_map.get(root, root)
        meshes = cmds.listRelatives(
            root,
            allDescendents=True,
            type=shape_types,
            fullPath=True,
            noIntermediate=True
        ) or []

        for shape in meshes:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if not parents:
                continue

            dag_path = parents[0]
            if dag_path in collected_paths:
                continue
            collected_paths.add(dag_path)

            relative = name_parser.get_relative_path_by_long_root(dag_path, align_root)
            relative = name_parser.normalize_match_relative_path(
                relative, align_root, align_root_count)

            result.append({
                "dag_path": dag_path,
                "shape_path": shape,
                "basename": name_parser.get_basename(dag_path),
                "root_long_path": align_root,
                "relative_path": relative,
            })

    align_root = name_parser.get_basename(align_roots[0]) if align_roots else ""

    return {
        "items": result,
        "align_root": align_root,
        "root_long_paths": align_roots,
    }


def _resolve_roots(selected):
    """
    将 mesh 转换为父 transform，过滤非 transform/mesh，去重。
    """
    roots = []
    seen = set()
    shape_types = ["mesh", "nurbsCurve"]
    for obj in selected:
        obj_type = cmds.objectType(obj)

        if obj_type in shape_types:
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if not parents:
                continue
            obj = parents[0]
            obj_type = "transform"

        if obj_type != "transform":
            continue

        if obj in seen:
            continue
        seen.add(obj)
        roots.append(obj)

    return roots


def _filter_nested_roots(roots):
    """
    如果选中多个父子嵌套的 root，只保留最顶层。
    """
    sorted_roots = sorted(roots, key=lambda x: x.count("|"))
    filtered = []

    for root in sorted_roots:
        is_nested = any(
            root != other and root.startswith(other + "|")
            for other in sorted_roots
        )
        if not is_nested:
            filtered.append(root)

    return filtered


def _path_parts(long_path):
    return [part for part in long_path.strip("|").split("|") if part]


def _build_path(parts):
    if not parts:
        return ""
    return "|" + "|".join(parts)


def _common_ancestor_path(paths):
    """
    返回多个 DAG 路径的共同祖先。
    多选时会把共同祖先作为匹配 root，避免每个所选模型的 relative_path 都变成空字符串。
    """
    if len(paths) < 2:
        return None

    split_paths = [_path_parts(path) for path in paths]
    if not split_paths:
        return None

    common = []
    for parts in zip(*split_paths):
        first = parts[0]
        if all(part == first for part in parts):
            common.append(first)
        else:
            break

    if not common:
        return None

    # 如果多个选择完全相同或被嵌套过滤后只剩同一路径，不在这里改变 root。
    if any(len(common) >= len(parts) for parts in split_paths):
        return None

    return _build_path(common)


def _build_root_align_map(roots):
    """
    collection root 控制收集范围；align root 控制 relative_path 的计算。
    单选保持原行为；多选使用共同祖先作为 align root。
    """
    if len(roots) < 2:
        return {root: root for root in roots}, roots

    common_root = _common_ancestor_path(roots)
    if not common_root:
        return {root: root for root in roots}, roots

    return {root: common_root for root in roots}, [common_root]
