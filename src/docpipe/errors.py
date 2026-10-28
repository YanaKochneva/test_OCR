class DocpipeError(Exception):
    """Базовая ошибка конвейера."""


class InputError(DocpipeError):
    """Ошибка входного файла или нарушения лимитов."""


class DependencyError(DocpipeError):
    """Не установлена необязательная или обязательная зависимость."""


class ModelError(DocpipeError):
    """Ошибка загрузки или работы модели."""


class EmptyDocumentError(DocpipeError):
    """The OCR result contains no usable text or fallback regions."""
