"""Вычисляемые типы записей для обоих представлений."""

GROUP_NAMES = (
    "Папки", "Видео", "Аудио", "Архивы", "Изображения", "Проекты / исходники",
    "Документы", "Прочее",
)
FOLDERS = 0
OTHER = len(GROUP_NAMES) - 1

EXTENSIONS = {
    extension: group
    for group, extensions in enumerate((
        "mp4 mov avi mxf mkv mpg mpeg wmv mts m2ts webm",
        "mp3 wav aif aiff flac ogg m4a wma aac",
        "rar zip 7z ace arj tar gz bz2 xz",
        "jpg jpeg png tif tiff bmp gif tga webp exr dpx",
        "aep aepx prproj drp veg max blend c4d psd psb ai cdr indd fla",
        "pdf doc docx xls xlsx ppt pptx txt rtf odt",
    ), start=1)
    for extension in extensions.split()
}


def file_group(kind, extension):
    if kind == "Folder":
        return FOLDERS
    return EXTENSIONS.get(extension.lstrip(".").casefold(), OTHER)
