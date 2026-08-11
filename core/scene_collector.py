# -*- coding: utf-8 -*-
import maya.cmds as cmds

from . import name_parser


# Python 2/3 兼容：统一字符串类型
try:
    string_types = basestring  # Python 2
except NameError:
    string_types = str         # Python 3


def collect_user_namespaces():
    """
    收集当前场景中的用户 namespace（排除 UI、shared）。
    """
    all_ns = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
    exclude = {"UI", "shared"}
    result = []

    for ns in all_ns:
        if not ns or ns in exclude:
            continue
        result.append(ns)

    return sorted(result)


def collect_original_meshes(selected_namespaces, align_roots=None):
    """
    根据选中的 namespace 和 ABC root long paths 收集原始模型。

    参数：
        selected_namespaces: list of namespace strings
        align_roots: list of ABC root long paths, 或单个字符串

    返回：
        {
            "items": [
                {
                    "dag_path": "|ns1:grp|ns1:pSphere1",
                    "shape_path": "...",
                    "namespace": "ns1",
                    "basename": "pSphere1",
                    "stripped_path": "|grp|pSphere1",
                    "relative_path": "pSphere1" or "grpA|pSphere1" or None,
                    "matched_root": "|Cloth_wrap" or None
                },
                ...
            ],
            "skipped": [...]  # 因 namespace 或 root 不匹配被跳过的 mesh dag_paths
        }
    """
    if not selected_namespaces:
        return {"items": [], "skipped": []}

    if align_roots is None:
        align_roots = []
    elif isinstance(align_roots, string_types):
        align_roots = [align_roots]
    align_root_count = len(align_roots)

    selected_set = set(selected_namespaces)
    shape_types = ["mesh", "nurbsCurve"]
    geo_shapes = cmds.ls(type=shape_types, long=True, noIntermediate=True) or []
    result = []
    skipped = []

    for shape in geo_shapes:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents:
            continue

        dag_path = parents[0]
        ns = name_parser.get_namespace(dag_path)

        if ns not in selected_set:
            skipped.append(dag_path)
            continue

        stripped = name_parser.strip_namespace_from_path(dag_path)

        relative = None
        matched_root = None

        if align_roots:
            for root in align_roots:
                rel = name_parser.get_relative_path_by_long_root(dag_path, root)
                if rel is not None:
                    relative = name_parser.normalize_match_relative_path(
                        rel, root, align_root_count)
                    matched_root = root
                    break

            if relative is None:
                skipped.append(dag_path)
                continue

        result.append({
            "dag_path": dag_path,
            "shape_path": shape,
            "namespace": ns,
            "basename": name_parser.get_basename(dag_path),
            "stripped_path": stripped,
            "relative_path": relative,
            "matched_root": matched_root,
        })

    return {"items": result, "skipped": skipped}
