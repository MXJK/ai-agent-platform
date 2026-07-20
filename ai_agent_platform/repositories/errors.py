class RepositoryIndexStoreConflictError(Exception):
    """Raised when a repository already has a pending or running index job."""
