class GoogleIntegrationError(Exception):
    """Base exception safe for translation at application boundaries."""


class GoogleAuthenticationError(GoogleIntegrationError):
    pass


class GoogleConfigurationError(GoogleAuthenticationError):
    pass


class DriveConnectionError(GoogleIntegrationError):
    pass


class DrivePermissionError(GoogleIntegrationError):
    pass


class StorageArchitectureError(GoogleIntegrationError):
    """Processed storage cannot satisfy the existing-file update contract."""


class DriveFileNotFoundError(GoogleIntegrationError):
    pass


class DriveFolderNotFoundError(DriveFileNotFoundError):
    pass


class SourceDiscoveryError(GoogleIntegrationError):
    pass


class AmbiguousSourceError(SourceDiscoveryError):
    pass
