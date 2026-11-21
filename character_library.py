"""Picture-first characters in the existing models/characters cache."""
import json
from pathlib import Path
import shutil
import uuid


def saved_characters(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.glob('*/bundle.json')):
        try:
            bundle = json.loads(path.read_text())
            if not bundle.get('picture_character'):
                continue
            cid = path.parent.name
            pictures = []
            for name in bundle.get('pictures', []):
                picture = (path.parent / name).resolve()
                if not picture.is_relative_to(path.parent.resolve()):
                    raise ValueError('Invalid character picture')
                pictures.append(str(picture))
            records.append(dict(bundle, id=cid, trigger=cid, face_lora_path=None,
                                audio_lora_path=None, has_voice=bool(bundle.get('voice_id')),
                                sample_image_path=pictures[0] if pictures else None,
                                sample_image_url=f'/characters/{cid}/preview' if pictures else None,
                                pictures=pictures, ltx_compatible=True))
        except (OSError, ValueError, TypeError):
            continue
    return records


def create_character(root: Path, name: str, pictures: list[bytes], voice_id: str = '') -> dict:
    """Publish only after every picture decodes; never overwrite a character."""
    from PIL import Image, ImageOps
    from io import BytesIO
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError('Give the character a name (up to 100 characters)')
    if len(pictures) > 3:
        raise ValueError('Choose up to three pictures')
    cid = 'char' + uuid.uuid4().hex[:12].translate(str.maketrans('0123456789', 'ghijklmnop')) + 'trn'
    folder = root / cid
    folder.mkdir(parents=True)
    try:
        paths = []
        for i, data in enumerate(pictures):
            with Image.open(BytesIO(data)) as source:
                source.load()
                picture = ImageOps.exif_transpose(source).convert('RGB')
                target = folder / ('avatar.png' if i == 0 else f'picture{i + 1}.png')
                picture.save(target)
                paths.append(target.name)
        record = dict(name=name, picture_character=True, pictures=paths, voice_id=voice_id)
        (folder / 'bundle.json').write_text(json.dumps(record, ensure_ascii=False) + '\n')
        return dict(record, id=cid, trigger=cid)
    except Exception:
        shutil.rmtree(folder)
        raise
