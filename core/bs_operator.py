# -*- coding: utf-8 -*-
import maya.cmds as cmds

from ..core import name_parser

cmds.lockNode('initialShadingGroup', l=False, lockUnpublished=False)
cmds.lockNode('defaultTextureList1', l=False, lockUnpublished=False)
cmds.lockNode('initialParticleSE', l=False, lockUnpublished=False)
cmds.lockNode('renderPartition', l=False, lockUnpublished=False)


def generate_bs_name(base_dag, target_dag, pattern):
    """
    根据命名规则生成 blendShape 节点名。
    支持变量：
        {base}      -> 原始模型短名
        {target}    abc 模型短名
        {namespace} -> 原始模型 namespace
    """
    namespace = name_parser.get_namespace(base_dag)
    base_name = name_parser.get_basename(base_dag)
    target_name = name_parser.get_basename(target_dag)

    name = pattern
    name = name.replace("{base}", base_name)
    name = name.replace("{target}", target_name)
    name = name.replace("{namespace}", namespace)

    return name


def _check_topology(base_dag, target_dag):
    """
    检查 base 和 target 顶点数是否一致。
    返回 (ok, message)
    """
    try:
        base_vtx = cmds.polyEvaluate(base_dag, vertex=True)
        target_vtx = cmds.polyEvaluate(target_dag, vertex=True)
    except Exception as e:
        return False, u"无法获取顶点数：{}".format(str(e))

    if base_vtx != target_vtx:
        return False, u"顶点数不一致 (base:{} vs target:{})".format(base_vtx, target_vtx)

    return True, ""


def _set_weight(bs_node, target_dag, weight):
    """
    使用 weightAliasList 安全设置目标权重。
    """
    aliases = cmds.aliasAttr(bs_node, query=True) or []
    target_basename = name_parser.get_basename(target_dag)

    for i in range(0, len(aliases), 2):
        alias = aliases[i]
        attr = aliases[i + 1]
        if alias == target_basename or attr.endswith(".w[{}]".format(i // 2)):
            idx = int(attr.split("[")[-1].rstrip("]"))
            cmds.setAttr("{}.w[{}]".format(bs_node, idx), weight)
            return True

    cmds.setAttr("{}.w[0]".format(bs_node), weight)
    return True


def _hide_target(target_dag):
    """
    隐藏 ABC 目标 mesh 的父 transform。
    """
    try:
        cmds.setAttr("{}.visibility".format(target_dag), 0)
    except Exception:
        pass


def _show_target(target_dag):
    """
    显示 ABC 目标 transform。
    """
    try:
        cmds.setAttr("{}.visibility".format(target_dag), 1)
    except Exception:
        pass


def _short_name(node):
    return node.split("|")[-1]


def _with_name_suffix(name, suffix):
    if ":" not in name:
        return name + suffix

    namespace, short_name = name.rsplit(":", 1)
    return "{}:{}{}".format(namespace, short_name, suffix)


def _shape_children(transform):
    return cmds.listRelatives(transform, shapes=True, fullPath=True) or []


def _shape_name_exists_under(transform, shape_name, ignore_shapes=None):
    ignore_shapes = set(ignore_shapes or [])
    for shape in _shape_children(transform):
        if shape in ignore_shapes:
            continue
        if _short_name(shape) == shape_name:
            return True
    return False


def _shapes_with_name_under(transform, shape_name):
    return [
        shape for shape in _shape_children(transform)
        if _short_name(shape) == shape_name
    ]


def _unique_child_shape_name(transform, base_name, ignore_shapes=None):
    if not _shape_name_exists_under(transform, base_name, ignore_shapes):
        return base_name

    index = 1
    while True:
        candidate = "{}{}".format(base_name, index)
        if not _shape_name_exists_under(transform, candidate, ignore_shapes):
            return candidate
        index += 1


def _find_child_shape_by_name(transform, shape_name):
    for shape in _shape_children(transform):
        if _short_name(shape) == shape_name:
            return shape
    return None


def _rename_shape_under_transform(transform, shape, new_name):
    try:
        cmds.lockNode(shape, lock=False)
    except Exception:
        pass

    renamed = cmds.rename(shape, new_name)
    renamed_short = _short_name(renamed)
    return _find_child_shape_by_name(transform, renamed_short) or renamed


def _is_referenced_node(node):
    try:
        return cmds.referenceQuery(node, isNodeReferenced=True)
    except Exception:
        return False


def _blendshape_order_kwargs(deformation_order, front_of_chain):
    mode = deformation_order
    if mode is None:
        mode = "before" if front_of_chain else "default"

    kwargs = {}
    if mode == "frontOfChain":
        kwargs["frontOfChain"] = True
    elif mode == "before":
        kwargs["before"] = True
    elif mode == "after":
        kwargs["after"] = True
    elif mode == "parallel":
        kwargs["parallel"] = True
    elif mode == "split":
        kwargs["split"] = True

    return kwargs


def _visible_shapes_under(dag_path):
    """
    收集 dag_path 下所有非 intermediate 的 mesh/curve shape。
    返回 [(transform, shape, shape_short_name), ...]
    """
    shape_types = ["mesh", "nurbsCurve"]
    shapes = []

    node_type = cmds.nodeType(dag_path)
    if node_type in shape_types:
        if not cmds.getAttr(dag_path + ".intermediateObject"):
            parents = cmds.listRelatives(dag_path, parent=True, fullPath=True) or []
            if parents:
                shapes.append((parents[0], dag_path, _short_name(dag_path)))
        return shapes

    if node_type != "transform":
        return shapes

    direct_shapes = cmds.listRelatives(
        dag_path, shapes=True, fullPath=True, type=shape_types) or []
    descendant_shapes = cmds.listRelatives(
        dag_path, allDescendents=True, fullPath=True, type=shape_types) or []

    seen_shapes = set()
    for shape in direct_shapes + descendant_shapes:
        if shape in seen_shapes:
            continue
        seen_shapes.add(shape)

        try:
            if cmds.getAttr(shape + ".intermediateObject"):
                continue
        except Exception:
            continue

        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            shapes.append((parents[0], shape, _short_name(shape)))

    return shapes


def _capture_visible_shape_names(dag_path):
    return {
        transform: shape_name
        for transform, shape, shape_name in _visible_shapes_under(dag_path)
    }


def _restore_visible_shape_names(shape_name_map):
    """
    Maya 创建 deformer 后可能把可见 shape 改成 xxxDeformed。
    这里把可见 shape 改回创建前的名字，intermediate shape 改成 xxxOrig。
    """
    for transform, original_shape_name in shape_name_map.items():
        if not cmds.objExists(transform):
            continue

        all_shapes = _shape_children(transform)
        visible_shapes = []
        blocking_shapes = []

        for shape in all_shapes:
            try:
                is_intermediate = cmds.getAttr(shape + ".intermediateObject")
            except Exception:
                continue

            if is_intermediate:
                if _short_name(shape) == original_shape_name:
                    blocking_shapes.append(shape)
            else:
                visible_shapes.append(shape)

        if len(visible_shapes) != 1:
            continue

        visible_shape = visible_shapes[0]
        if _short_name(visible_shape) == original_shape_name:
            continue

        moved_blocking_shapes = []
        for shape in blocking_shapes:
            try:
                tmp_base = _with_name_suffix(original_shape_name, "_bsTmp")
                tmp_name = _unique_child_shape_name(
                    transform, tmp_base, ignore_shapes=[shape])
                moved_blocking_shapes.append(
                    _rename_shape_under_transform(transform, shape, tmp_name))
            except Exception as e:
                ref_text = u" referenced" if _is_referenced_node(shape) else u""
                print(u"警告: 无法临时移动占名 shape{}：{} -> {}，{}".format(
                    ref_text, shape, tmp_name, str(e)))

        if _shape_name_exists_under(transform, original_shape_name):
            blockers = _shapes_with_name_under(transform, original_shape_name)
            print(u"警告: 无法释放 shape 名称，跳过恢复：{}，占用节点：{}".format(
                original_shape_name, ", ".join(blockers)))
            continue

        try:
            visible_shape = _rename_shape_under_transform(
                transform, visible_shape, original_shape_name)
        except Exception as e:
            print(u"警告: 恢复 shape 名称失败 {} -> {}: {}".format(
                visible_shape, original_shape_name, str(e)))
            continue

        if _short_name(visible_shape) != original_shape_name:
            print(u"警告: Maya 自动调整了 shape 名称：{}，期望：{}".format(
                visible_shape, original_shape_name))
            continue

        for shape in moved_blocking_shapes:
            if not cmds.objExists(shape):
                continue
            try:
                orig_base = _with_name_suffix(original_shape_name, "Orig")
                orig_name = _unique_child_shape_name(
                    transform, orig_base, ignore_shapes=[shape])
                _rename_shape_under_transform(transform, shape, orig_name)
            except Exception:
                pass


def _ensure_bs_at_top(bs_node, dag_path):
    """
    强制将指定的 blendShape 节点移动到变形链的最顶端（在所有其他变形器之前执行）。
    支持传入 Group 或具体的 Mesh。
    """
    # 1. 收集所有需要处理的 shape 节点
    target_shapes = []
    
    # 如果传入的是 Transform/Group，向下查找所有的 mesh shape
    if cmds.nodeType(dag_path) == 'transform':
        # 递归查找所有子层级中的 mesh
        all_descendants = cmds.listRelatives(dag_path, allDescendents=True, fullPath=True, type='mesh')
        if all_descendants:
            for mesh in all_descendants:
                # 排除中间节点(Intermediate Object)，只保留实际的渲染模型
                if not cmds.getAttr(mesh + '.intermediateObject'):
                    target_shapes.append(mesh)
    # 如果传入的直接是 Mesh 的 Shape 节点
    elif cmds.nodeType(dag_path) == 'mesh':
        if not cmds.getAttr(dag_path + '.intermediateObject'):
            target_shapes.append(dag_path)
    # 如果传入的是 Mesh 的 Transform 节点
    elif cmds.objectType(dag_path, isType='transform') and cmds.listRelatives(dag_path, shapes=True, type='mesh'):
        shapes = cmds.listRelatives(dag_path, shapes=True, fullPath=True, type='mesh')
        for sh in shapes:
            if not cmds.getAttr(sh + '.intermediateObject'):
                target_shapes.append(sh)

    if not target_shapes:
        return

    # 2. 遍历所有找到的 shape，逐个重排变形器
    for shape in target_shapes:
        # 获取影响该 shape 的所有几何体变形器 (gl=True 表示获取全局历史, il=2 表示仅变形器)
        history_deformers = cmds.listHistory(shape, gl=True, il=2) or []
        
        # 如果该 shape 没有任何历史变形器，或者找不到我们创建的 bs_node，跳过
        if not history_deformers or bs_node not in history_deformers:
            continue
            
        # 去重并保持原顺序
        unique_deformers = []
        for d in history_deformers:
            if d not in unique_deformers:
                unique_deformers.append(d)
                
        # 检查是否已经在最顶层 (索引 0)
        if unique_deformers[0] == bs_node:
            continue
            
        # 将 bs_node 依次移动到其他 deformer 前面。
        unique_deformers.remove(bs_node)
        
        for deformer in unique_deformers:
            try:
                cmds.reorderDeformers(bs_node, deformer, shape)
            except Exception:
                pass



def create_blendshape(base_dag, target_dag, name_pattern="{base}_{target}_bbs",
                      weight=1.0, front_of_chain=True, skip_existing=True,
                      hide_target_after=True, origin="world",
                      deformation_order="before"):
    """
    单个 mesh 创建 BlendShape。
    """
    bs_name = generate_bs_name(base_dag, target_dag, name_pattern)

    if skip_existing and cmds.objExists(bs_name):
        return None, u"已存在同名 blendShape，跳过：{}".format(bs_name), False

    ok, msg = _check_topology(base_dag, target_dag)
    if not ok:
        return None, u"拓扑检查失败 {} -> {}：{}".format(base_dag, target_dag, msg), False

    shape_names = _capture_visible_shape_names(base_dag)
    order_kwargs = _blendshape_order_kwargs(deformation_order, front_of_chain)

    try:
        bs_node = cmds.blendShape(
            target_dag,
            base_dag,
            name=bs_name,
            origin=origin,
            **order_kwargs
        )[0]

        _set_weight(bs_node, target_dag, weight)
        _restore_visible_shape_names(shape_names)

        if hide_target_after:
            _hide_target(target_dag)

        return bs_node, u"创建成功：{}".format(bs_node), hide_target_after

    except Exception as e:
        return None, u"创建失败 {} -> {}：{}".format(base_dag, target_dag, str(e)), False


def _delete_blendshape_node(bs_name):
    if not cmds.objExists(bs_name):
        return False, u"未找到 blendShape，跳过：{}".format(bs_name)

    try:
        if cmds.nodeType(bs_name) != "blendShape":
            return False, u"同名节点不是 blendShape，跳过：{}".format(bs_name)
        cmds.delete(bs_name)
        return True, u"删除成功：{}".format(bs_name)
    except Exception as e:
        return False, u"删除失败 {}：{}".format(bs_name, str(e))


def delete_blendshape(base_dag, target_dag, name_pattern="{base}_{target}_bbs",
                      show_target_after=True):
    """
    删除单个 mesh 对应的 blendShape 节点。
    """
    bs_name = generate_bs_name(base_dag, target_dag, name_pattern)
    deleted, msg = _delete_blendshape_node(bs_name)

    if deleted and show_target_after:
        _show_target(target_dag)

    return deleted, msg, show_target_after if deleted else False


def should_use_single_blendshape(match_result, abc_items, original_items):
    """
    判断是否可以使用单个 blendShape 节点：
    1. 所有 ABC mesh 都被匹配
    2. 没有冲突
    3. 原始模型数量与 ABC 数量完全一致（无剩余未匹配原始模型）
    4. base/target group 内部可见 shape 的 DAG 顺序一致
    """
    matched = match_result.get("matched", [])
    conflicts = match_result.get("conflicts", [])
    unmatched = match_result.get("unmatched", [])
    unmatched_originals = match_result.get("unmatched_originals", [])

    if conflicts or unmatched:
        return False

    if len(matched) != len(abc_items):
        return False

    matched_orig_paths = {orig["dag_path"] for orig, _ in matched}
    if len(matched_orig_paths) != len(original_items):
        return False

    if unmatched_originals:
        return False

    return _single_blendshape_order_matches(matched)


def _has_visible_shape(transform):
    shape_types = ["mesh", "nurbsCurve"]
    shapes = cmds.listRelatives(
        transform, shapes=True, fullPath=True, type=shape_types) or []

    for shape in shapes:
        try:
            if not cmds.getAttr(shape + ".intermediateObject"):
                return True
        except Exception:
            pass

    return False


def _ordered_visible_transforms(root):
    """
    按 DAG 子节点顺序递归收集含可见 shape 的 transform。
    用于判断 group blendShape 是否可以安全按 group 顺序创建。
    """
    shape_types = ["mesh", "nurbsCurve"]
    node_type = cmds.nodeType(root)

    if node_type in shape_types:
        try:
            if cmds.getAttr(root + ".intermediateObject"):
                return []
        except Exception:
            return []

        parents = cmds.listRelatives(root, parent=True, fullPath=True) or []
        return parents[:1]

    if node_type != "transform":
        return []

    ordered = []
    if _has_visible_shape(root):
        ordered.append(root)

    children = cmds.listRelatives(root, children=True, fullPath=True) or []
    for child in children:
        try:
            if cmds.nodeType(child) != "transform":
                continue
        except Exception:
            continue

        ordered.extend(_ordered_visible_transforms(child))

    return ordered


def _ordered_relative_paths(root):
    relative_paths = []
    for transform in _ordered_visible_transforms(root):
        relative = name_parser.get_relative_path_by_long_root(transform, root)
        if relative is None:
            return None
        relative_paths.append(relative)
    return relative_paths


def _single_blendshape_order_matches(matched_pairs):
    top_base, top_target, root_msg = _find_group_blendshape_roots(matched_pairs)
    if root_msg:
        return False

    base_order = _ordered_relative_paths(top_base)
    target_order = _ordered_relative_paths(top_target)

    if base_order is None or target_order is None:
        return False

    return base_order == target_order


def _get_short_name_no_ns(dag_path):
    """获取无路径、无命名空间的纯短名"""
    short_name = dag_path.split("|")[-1]
    if ":" in short_name:
        short_name = short_name.split(":")[-1]
    return short_name


def _find_top_matching_group(base_dag, target_dag):
    """
    从 base 和 target 模型向上递归，找到最上层的同名父级 Group。
    """
    def get_all_parents(dag):
        parents = []
        parent = cmds.listRelatives(dag, parent=True, fullPath=True)
        while parent:
            parents.append(parent[0])
            parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)
        return parents

    base_parents = get_all_parents(base_dag)
    target_parents = get_all_parents(target_dag)

    # 如果没有父级，直接返回自身
    if not base_parents or not target_parents:
        return base_dag, target_dag

    top_base = base_dag
    top_target = target_dag

    # 从下往上比对名字
    for b_par, t_par in zip(reversed(base_parents), reversed(target_parents)):
        b_name = _get_short_name_no_ns(b_par)
        t_name = _get_short_name_no_ns(t_par)
        
        if b_name == t_name:
            top_base = b_par
            top_target = t_par
        else:
            break  # 名字不一样就停止递归

    return top_base, top_target


def _path_parts_no_ns(dag_path):
    return [part.split(":")[-1] for part in dag_path.strip("|").split("|") if part]


def _path_parts_with_ns(dag_path):
    return [part for part in dag_path.strip("|").split("|") if part]


def _find_root_from_item(item, root_key):
    """
    根据采集时记录的 root long path，在 item 的实际 DAG 路径中找到对应 group。
    这样可以模拟 Maya 中直接选择 group 创建 blendShape 的行为。
    """
    dag_path = item.get("dag_path")
    root_path = item.get(root_key)
    if not dag_path or not root_path:
        return None

    dag_parts = _path_parts_with_ns(dag_path)
    dag_parts_no_ns = _path_parts_no_ns(dag_path)
    root_parts_no_ns = _path_parts_no_ns(root_path)

    if not dag_parts or not root_parts_no_ns:
        return None

    for start in range(len(dag_parts_no_ns) - len(root_parts_no_ns) + 1):
        end = start + len(root_parts_no_ns)
        if dag_parts_no_ns[start:end] == root_parts_no_ns:
            return "|" + "|".join(dag_parts[:end])

    return None


def _find_group_blendshape_roots(matched_pairs):
    """
    从匹配结果中解析出单节点模式应使用的一对 group。
    优先使用加载 ABC 时记录的 root；缺失时回退到向上查找同名父级。
    """
    base_roots = []
    target_roots = []
    seen = set()

    for orig, abc in matched_pairs:
        base_root = _find_root_from_item(orig, "matched_root")
        target_root = _find_root_from_item(abc, "root_long_path")

        if not base_root or not target_root:
            base_root, target_root = _find_top_matching_group(
                orig["dag_path"], abc["dag_path"])

        key = (base_root, target_root)
        if key in seen:
            continue

        seen.add(key)
        base_roots.append(base_root)
        target_roots.append(target_root)

    if len(base_roots) != 1 or len(target_roots) != 1:
        return None, None, u"单节点模式需要解析到唯一的一组 base/target Group，当前解析到 {} 组。".format(
            len(base_roots))

    return base_roots[0], target_roots[0], ""


def create_single_blendshape(matched_pairs, name_pattern="{base}_{target}_bbs",
                             weight=1.0, front_of_chain=True, skip_existing=True,
                             hide_target_after=True, origin="world",
                             deformation_order="before"):
    """
    为所有匹配对向上查找最顶层 Group，并使用该 Group 创建单一 blendShape 节点。
    """
    if not matched_pairs:
        return None, [u"没有匹配对"], 0

    # 1. 预检拓扑 (防止中途出错)
    messages = []
    for orig, abc in matched_pairs:
        ok, msg = _check_topology(orig["dag_path"], abc["dag_path"])
        if not ok:
            messages.append(u"拓扑检查失败 {} -> {}：{}".format(
                orig["dag_path"], abc["dag_path"], msg))

    if messages:
        return None, messages, 0

    # 2. 解析唯一的一对 Group，模拟 Maya 里直接选择 group 创建 blendShape。
    top_base, top_target, root_msg = _find_group_blendshape_roots(matched_pairs)
    if root_msg:
        return None, [root_msg], 0

    first_base = top_base
    first_target = top_target
    bs_name = generate_bs_name(first_base, first_target, name_pattern)

    if skip_existing and cmds.objExists(bs_name):
        return None, [u"已存在同名 blendShape，跳过：{}".format(bs_name)], 0

    shape_names = _capture_visible_shape_names(top_base)
    order_kwargs = _blendshape_order_kwargs(deformation_order, front_of_chain)

    try:
        # 3. 直接传入一对 Group，而不是多个子 Group 列表。
        bs_node = cmds.blendShape(
            top_target,
            top_base,
            name=bs_name,
            origin=origin,
            **order_kwargs
        )[0]

        # 4. 恢复 Maya 创建 deformer 后可能改掉的可见 shape 名称
        _restore_visible_shape_names(shape_names)

        # 5. 设置所有 target 权重
        aliases = cmds.aliasAttr(bs_node, query=True) or []
        for i in range(0, len(aliases), 2):
            attr = aliases[i + 1]
            idx = int(attr.split("[")[-1].rstrip("]"))
            cmds.setAttr("{}.w[{}]".format(bs_node, idx), weight)

        # 6. 隐藏目标模型
        hidden_count = 0
        if hide_target_after:
            _hide_target(top_target)
            hidden_count = 1
            
        messages.append(u"使用 Group 创建单节点 blendShape 成功：{} ({} -> {})".format(
            bs_name, top_target, top_base))

        return bs_node, messages, hidden_count

    except Exception as e:
        return None, [u"创建单节点 blendShape 失败：{}".format(str(e))], 0


def delete_single_blendshape(matched_pairs, name_pattern="{base}_{target}_bbs",
                             show_target_after=True):
    """
    删除单节点模式创建的 group blendShape。
    """
    if not matched_pairs:
        return [u"没有匹配对"], 0, 0

    top_base, top_target, root_msg = _find_group_blendshape_roots(matched_pairs)
    if root_msg:
        return [root_msg], 0, 0

    bs_name = generate_bs_name(top_base, top_target, name_pattern)
    deleted, msg = _delete_blendshape_node(bs_name)

    shown_count = 0
    if deleted and show_target_after:
        _show_target(top_target)
        shown_count = 1

    return [msg], 1 if deleted else 0, shown_count
