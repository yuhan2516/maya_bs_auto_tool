# -*- coding: utf-8 -*-


def strip_namespace_from_path(long_name):
    """
    去掉完整 DAG 路径中每个节点的 namespace。
    例如：
        |ns1:ns2:grp|ns1:ns2:mesh  ->  |grp|mesh
    """
    if not long_name:
        return ""

    leading_pipe = long_name.startswith("|")
    parts = long_name.strip("|").split("|")
    stripped_parts = [p.split(":")[-1] for p in parts]
    result = "|".join(stripped_parts)

    if leading_pipe:
        result = "|" + result

    return result


def get_namespace(long_name):
    """
    获取 transform 的 namespace（不含 leading |）。
    例如：
        |ns1:ns2:pSphere1  ->  ns1:ns2
        |pSphere1          ->  ""
    """
    short = long_name.strip("|").split("|")[-1]
    if ":" not in short:
        return ""
    return ":".join(short.split(":")[:-1])


def get_basename(long_name):
    """
    获取 transform 的短名。
    """
    short = long_name.strip("|").split("|")[-1]
    return short.split(":")[-1]


def get_top_group_name(long_name):
    """
    获取最顶层 transform 的 basename。
    """
    if not long_name:
        return ""
    return long_name.strip("|").split("|")[0].split(":")[-1]


def _path_parts(long_name):
    return long_name.strip("|").split("|")


def get_relative_path_by_long_root(long_name, root_long_path):
    """
    从指定的完整 root_long_path 开始取相对路径（忽略 namespace）。

    参数：
        long_name: 目标对象的完整 DAG 路径
        root_long_path: 根节点的完整 DAG 路径（如用户选中的 ABC top group）

    返回：
        相对路径字符串，例如 "grpA|skirt1"
        如果 mesh 直接在 root 下一级，返回 ""。
        如果无法匹配，返回 None。
    """
    if not long_name or not root_long_path:
        return None

    long_stripped = strip_namespace_from_path(long_name)
    root_stripped = strip_namespace_from_path(root_long_path)

    long_parts = _path_parts(long_stripped)
    root_parts = _path_parts(root_stripped)

    if not root_parts:
        return None

    # 优先：完整前缀匹配
    if long_parts[:len(root_parts)] == root_parts:
        relative_parts = long_parts[len(root_parts):]
        return "|".join(relative_parts) if relative_parts else ""

    # Fallback：在路径中查找 root 作为一段子路径
    indices = []
    for i in range(len(long_parts) - len(root_parts) + 1):
        if long_parts[i:i + len(root_parts)] == root_parts:
            indices.append(i)

    if not indices:
        return None

    # 出现多次时取第一次，调用方应通过 has_multiple_root_occurrences 提示用户
    relative_parts = long_parts[indices[0] + len(root_parts):]
    return "|".join(relative_parts) if relative_parts else ""


def normalize_match_relative_path(relative_path, root_long_path, root_count):
    """
    多个 root 同时匹配时，空 relative_path 会导致所有 root mesh 共用 "" key。
    这种情况下用 root basename 作为 key，保持独立模型可以按名字匹配。
    """
    if relative_path == "" and root_count > 1:
        return get_basename(root_long_path)
    return relative_path


def has_multiple_root_occurrences(long_name, root_long_path):
    """
    检查 root_long_path（忽略 namespace）在 long_name 中是否出现多次。
    """
    if not long_name or not root_long_path:
        return False

    long_stripped = strip_namespace_from_path(long_name)
    root_stripped = strip_namespace_from_path(root_long_path)

    long_parts = _path_parts(long_stripped)
    root_parts = _path_parts(root_stripped)

    if not root_parts:
        return False

    count = 0
    for i in range(len(long_parts) - len(root_parts) + 1):
        if long_parts[i:i + len(root_parts)] == root_parts:
            count += 1

    return count > 1
