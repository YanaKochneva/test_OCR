"""Visual agreement for one already segmented line; preserve primary spelling."""
import unicodedata
from docpipe.stages.short_value_consensus import _VISUAL_LETTERS


def same_visible_line(primary,alternative):
    def key(value):
        value=unicodedata.normalize('NFC',value).casefold().translate(_VISUAL_LETTERS)
        return ' '.join(value.split())
    return bool(primary.strip()) and key(primary)==key(alternative)
