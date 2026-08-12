class AdminEarningsIngestionError(Exception):
    """Base exception for safe ingestion failures."""


class UnsupportedSourceFormatError(AdminEarningsIngestionError):
    pass


class SourceFileTooLargeError(AdminEarningsIngestionError):
    pass


class SourceParseError(AdminEarningsIngestionError):
    pass


class SchemaValidationError(AdminEarningsIngestionError):
    pass
