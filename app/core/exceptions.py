class AppException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Invalid credentials", code: str = "UNAUTHORIZED"):
        super().__init__(code, message, 401)


class PermissionDeniedException(AppException):
    def __init__(self, message: str = "Insufficient permissions", code: str = "PERMISSION_DENIED"):
        super().__init__(code, message, 403)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", code: str = "NOT_FOUND"):
        super().__init__(code, message, 404)


class ValidationException(AppException):
    def __init__(self, message: str = "Validation failed", code: str = "VALIDATION_ERROR"):
        super().__init__(code, message, 400)


class ConflictException(AppException):
    def __init__(self, message: str = "Conflict", code: str = "CONFLICT"):
        super().__init__(code, message, 409)


class TooManyRequestsException(AppException):
    def __init__(self, message: str = "Too many requests", code: str = "RATE_LIMITED"):
        super().__init__(code, message, 429)
