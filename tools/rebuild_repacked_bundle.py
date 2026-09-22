"""重建一个经过验证的 SpriteAtlas Bundle，用于离线重排验证。

这是面向已验证 L7 SpriteAtlas 重排流程的窄范围辅助工具，不是旧的全量
AssetBundle 替换流水线。运行时部署仍然使用 Skyline 和 PNG 输入；本工具只重建
源 Bundle，使生成的重排结果能够在作为证据使用前重新打开并检查。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import UnityPy
from PIL import Image
from UnityPy.export.Texture2DConverter import image_to_texture2d
from UnityPy.streams import EndianBinaryReader


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metadata(texture) -> dict[str, object]:
    stream = texture.m_StreamData
    return {
        "name": texture.m_Name,
        "width": texture.m_Width,
        "height": texture.m_Height,
        "format": int(texture.m_TextureFormat),
        "mipCount": texture.m_MipCount,
        "completeImageSize": texture.m_CompleteImageSize,
        "platformBlob": bytes(texture.m_PlatformBlob).hex(),
        "stream": {
            "path": stream.path,
            "offset": stream.offset,
            "size": stream.size,
        },
    }


def _encode(texture_object, png_path: str | Path, resize: bool) -> tuple[bytes, tuple[int, int]]:
    texture = texture_object.read()
    png = Path(png_path)
    with Image.open(png) as image:
        image = image.convert("RGBA")
        original_size = image.size
        target_size = (texture.m_Width, texture.m_Height)
        if image.size != target_size:
            if not resize:
                raise ValueError(f"{png}：{image.size} != 目标尺寸 {target_size}")
            image = image.resize(target_size, Image.Resampling.LANCZOS)
        encoded, output_format = image_to_texture2d(
            image,
            texture.m_TextureFormat,
            texture_object.platform,
            texture.m_PlatformBlob,
            flip=True,
        )

    if int(output_format) != int(texture.m_TextureFormat):
        raise ValueError(
            f"{png}：格式发生变化 {texture.m_TextureFormat} -> {output_format}"
        )
    expected = texture.m_StreamData.size or texture.m_CompleteImageSize
    if len(encoded) != expected:
        raise ValueError(f"{png}：编码后为 {len(encoded)} 字节，期望 {expected} 字节")
    return encoded, original_size


def patch_bundle(
    bundle_path: str | Path,
    specs: list[dict[str, object]],
    *,
    switch_root: str | Path,
    destination_root: str | Path,
) -> dict[str, object]:
    """替换选定的 Texture2D 对象，并验证重新打开的 Bundle。

    ``specs`` 包含 ``pathId``、``png``、``source`` 和可选的 ``resize``。
    原始 Bundle 只读；重建结果会按照相对于 ``switch_root`` 的源路径写入
    ``destination_root``。
    """

    source_path = Path(bundle_path).resolve()
    switch_root_path = Path(switch_root).resolve()
    destination_root_path = Path(destination_root).resolve()
    UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.50f1"
    source_bytes = source_path.read_bytes()
    environment = UnityPy.load(str(source_path))
    bundle = next(iter(environment.files.values()))
    objects = {obj.path_id: obj for obj in environment.objects}
    resource_originals: dict[str, bytes] = {}
    resource_modified: dict[str, bytearray] = {}
    expected: dict[int, tuple[dict[str, object], bytes]] = {}
    rows: list[dict[str, object]] = []

    for number, spec in enumerate(specs, 1):
        path_id = int(spec["pathId"])
        obj = objects.get(path_id)
        if obj is None or obj.type.name != "Texture2D":
            raise ValueError(f"{source_path}：缺少 Texture2D {path_id}")
        texture = obj.read()
        before = _metadata(texture)
        encoded, input_size = _encode(obj, str(spec["png"]), bool(spec.get("resize", False)))
        stream = texture.m_StreamData
        if stream.size:
            resource_name = stream.path.replace("\\", "/").rsplit("/", 1)[-1]
            reader = bundle.files.get(resource_name)
            if reader is None:
                raise ValueError(f"{source_path}：缺少精确资源 {resource_name}")
            if resource_name not in resource_originals:
                resource_originals[resource_name] = reader.bytes
                resource_modified[resource_name] = bytearray(reader.bytes)
            modified = resource_modified[resource_name]
            if stream.offset + stream.size > len(modified):
                raise ValueError(f"{source_path}：流范围超出 {resource_name}")
            modified[stream.offset : stream.offset + stream.size] = encoded
        else:
            texture.image_data = encoded
            texture.save()

        expected[path_id] = (before, encoded)
        rows.append(
            {
                "pathId": path_id,
                "name": texture.m_Name,
                "source": spec["source"],
                "png": str(spec["png"]),
                "inputSize": list(input_size),
                "targetSize": [texture.m_Width, texture.m_Height],
                "resized": bool(spec.get("resize", False)),
                "format": int(texture.m_TextureFormat),
                "encodedSize": len(encoded),
                "encodedSha256": _digest(encoded),
            }
        )
        print(
            f"  已编码 {number}/{len(specs)}：{texture.m_Name} "
            f"{texture.m_Width}×{texture.m_Height}",
            flush=True,
        )

    for resource_name, modified in resource_modified.items():
        original = resource_originals[resource_name]
        restored = bytearray(modified)
        for object_id in expected:
            texture = objects[object_id].read()
            stream = texture.m_StreamData
            if stream.size and stream.path.replace("\\", "/").rsplit("/", 1)[-1] == resource_name:
                restored[stream.offset : stream.offset + stream.size] = original[
                    stream.offset : stream.offset + stream.size
                ]
        if bytes(restored) != original:
            raise ValueError(f"{source_path}：{resource_name} 中的非目标字节发生变化")
        old_reader = bundle.files[resource_name]
        new_reader = EndianBinaryReader(bytes(modified), endian=old_reader.endian)
        new_reader.flags = old_reader.flags
        bundle.files[resource_name] = new_reader

    saved = bundle.save(packer="original")
    reopened = UnityPy.load(saved)
    reopened_objects = {obj.path_id: obj for obj in reopened.objects}
    for object_id, (before, encoded) in expected.items():
        texture = reopened_objects[object_id].read()
        if _metadata(texture) != before:
            raise ValueError(f"{source_path}：PathID {object_id} 的元数据发生变化")
        if bytes(texture.get_image_data()) != encoded:
            raise ValueError(f"{source_path}：重新打开后 PathID {object_id} 的编码字节发生变化")
        image = texture.image
        if image.size != (texture.m_Width, texture.m_Height):
            raise ValueError(f"{source_path}：PathID {object_id} 的解码尺寸发生变化")

    relative = source_path.relative_to(switch_root_path)
    destination = destination_root_path / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(saved)
    if _digest(source_path.read_bytes()) != _digest(source_bytes):
        raise ValueError(f"{source_path}：源文件哈希发生变化")
    return {
        "source": str(source_path),
        "sourceSha256": _digest(source_bytes),
        "sourceSize": len(source_bytes),
        "destination": str(destination),
        "destinationSha256": _digest(saved),
        "destinationSize": len(saved),
        "textures": rows,
        "reopenVerified": True,
        "nonTargetResourceBytesUnchanged": True,
    }
